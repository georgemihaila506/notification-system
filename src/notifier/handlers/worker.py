"""The per-message delivery loop shared by every channel worker, and the SQS-triggered
Lambda entry point that drives it.

`deliver` is deliberately AWS-agnostic: it takes a `store` and a `send` callable, so
the whole reliability story — dedup, opt-out re-check, transient-vs-permanent
handling — is testable with fakes and no queue. `handler` is the thin wrapper: one
Lambda per channel, each fed by its own queue, unwrapping records and translating
`deliver`'s outcome into what the SQS poller understands (delete or keep).
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable

from ..channels.simulated import simulated
from ..db import Store
from ..models import CHANNELS, Notification, PermanentError, TransientError

logger = logging.getLogger(__name__)

# A channel's send function: given the notification, deliver it or raise
# TransientError / PermanentError.
Sender = Callable[[Notification], None]

# channel -> its sender. Simulated for every channel until M4 swaps email for SES.
SENDERS: dict[str, Sender] = {c: simulated(c) for c in CHANNELS}


def handler(event: dict, context: object) -> dict:
    """SQS-triggered entry point. One Lambda per channel; `CHANNEL` env says which.

    Each record's body is the SNS envelope; its `Message` is what ingest published.
    Every record is attempted independently, and the return value tells the poller
    which ones to keep (ADR-0003 §3, requires `ReportBatchItemFailures` on the
    event source mapping): anything listed in `batchItemFailures` stays on the queue
    and is redelivered after the visibility timeout; everything else is deleted.

    What ends up in that list:
      * TransientError from deliver() — retry may help.
      * A record that can't be parsed — logged, and kept on purpose: it will exhaust
        its retries and land in the DLQ with its body intact, instead of vanishing.

    What doesn't:
      * PermanentError — deliver() has already recorded it as `failed`; deleting the
        message is what makes "never retried" true.
      * Any other exception propagates and fails the whole invoke, which keeps every
        record in the batch. Retrying the unknown is the safe default.

    `VISIBILITY_TIMEOUT_S` is passed through as deliver()'s stale threshold so the
    worker and the queue can never disagree about how long "in flight" can be.
    """
    channel = os.environ["CHANNEL"]
    stale_after_s = int(os.environ["VISIBILITY_TIMEOUT_S"])
    send = SENDERS[channel]
    store = Store(os.environ["PREFS_TABLE"], os.environ["DELIVERIES_TABLE"])
    failed_ids = []
    for record in event["Records"]:
        message_id = record["messageId"]
        try:
            notification = Notification.from_wire(json.loads(record["body"])["Message"])
            deliver(
                store=store,
                channel=channel,
                notification=notification,
                send=send,
                stale_after_s=stale_after_s,
            )
        except TransientError:
            failed_ids.append(message_id)
        except (KeyError, TypeError, ValueError):
            logger.exception("unparseable record %s kept for DLQ", message_id)
            failed_ids.append(message_id)

    return {"batchItemFailures": [{"itemIdentifier": mid} for mid in failed_ids]}


# A `pending` row younger than this is assumed to be in flight on another worker;
# older, and that worker must have died. Must be >= the queue's visibility timeout
# (ADR-0003 §2), else a slow-but-alive send gets duplicated.
PENDING_STALE_AFTER_S = 60


def deliver(
    store: Store,
    channel: str,
    notification: Notification,
    send: Sender,
    *,
    stale_after_s: int = PENDING_STALE_AFTER_S,
) -> str:
    """Deliver one notification on one channel, effectively once.

    Returns "delivered" | "skipped" | "failed".

    * Dedup (ADR-0001): a row already marked `delivered` or `failed` is finished work →
      skip. No row → claim it with a conditional put; losing that race means another
      worker is sending right now → skip. A `pending` row is either in flight (young →
      skip) or a crashed earlier attempt (stale → re-claim, conditionally, and retry).
      Skipping only work someone else *provably* holds is what keeps a crash-after-claim
      from losing the notification without letting concurrent receives double-send.
    * Opt-out re-check (ADR-0005) happens right before sending, inside the same
      `try` as the send, so it's recorded as a permanent failure like any other.
    * Classification (ADR-0003): a PermanentError is recorded as `failed` and swallowed —
      never retried. A TransientError deliberately propagates: raising out of the
      worker leaves the SQS message undeleted, which is exactly what makes it retry.
    """
    nid = notification.notification_id
    existing = store.get_delivery(nid, channel)
    if existing is None:
        if not store.put_delivery_if_absent(nid, channel):
            return "skipped"
    elif existing["status"] != "pending":
        return "skipped"
    else:
        seen = int(existing["updated_at"])
        if time.time() - seen < stale_after_s:
            return "skipped"
        if not store.reclaim_delivery(nid, channel, seen):
            return "skipped"

    try:
        prefs = store.get_prefs(notification.user_id)
        if not prefs or channel not in prefs["channels"]:
            raise PermanentError("opted out")
        send(notification)
    except PermanentError:
        store.mark_delivery(nid, channel, "failed")
        return "failed"

    store.mark_delivery(nid, channel, "delivered")
    return "delivered"
