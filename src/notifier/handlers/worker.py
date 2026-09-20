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
      * `in_flight` from deliver() — another worker holds this one right now. Keeping
        it costs a redelivery cycle; deleting it would lose the notification outright
        if that worker then dies.
      * A record that can't be parsed — logged, and kept on purpose: it will exhaust
        its retries and land in the DLQ with its body intact, instead of vanishing.

    What doesn't:
      * `delivered`, `failed` and `skipped` — the work is finished, and deleting the
        message is what makes ADR-0003's "never retried" true for a permanent failure.
      * Any other exception propagates and fails the whole invoke, which keeps every
        record in the batch. Retrying the unknown is the safe default.

    `STALE_AFTER_S` is deliver()'s threshold for calling a `pending` row dead;
    Terraform derives it from the worker timeout and keeps it below the queue's
    visibility timeout. See PENDING_STALE_AFTER_S for why both bounds matter.
    """
    channel = os.environ["CHANNEL"]
    stale_after_s = int(os.environ["STALE_AFTER_S"])
    send = SENDERS[channel]
    store = Store(os.environ["PREFS_TABLE"], os.environ["DELIVERIES_TABLE"])
    failed_ids = []
    for record in event["Records"]:
        message_id = record["messageId"]
        try:
            notification = Notification.from_wire(json.loads(record["body"])["Message"])
            status = deliver(
                store=store,
                channel=channel,
                notification=notification,
                send=send,
                stale_after_s=stale_after_s,
            )
            if status == "in_flight":
                failed_ids.append(message_id)
        except TransientError:
            failed_ids.append(message_id)
        except (KeyError, TypeError, ValueError):
            logger.exception("unparseable record %s kept for DLQ", message_id)
            failed_ids.append(message_id)

    return {"batchItemFailures": [{"itemIdentifier": mid} for mid in failed_ids]}


# How old a `pending` row must be before a worker treats it as a dead attempt
# rather than one in flight. It has to sit STRICTLY BETWEEN two bounds:
#   * above the worker's Lambda timeout — a worker cannot outlive its own timeout,
#     so a row older than that belongs to a process that is provably gone;
#   * below the queue's visibility timeout — an SQS retry arrives at roughly that
#     mark and must be allowed through, not mistaken for a live worker.
# Setting it equal to the visibility timeout deadlocks every retry: the row is
# always a second or two too young, nothing is ever reclaimed, and the message is
# deleted unprocessed. Terraform passes the real value as STALE_AFTER_S.
PENDING_STALE_AFTER_S = 30


def deliver(
    store: Store,
    channel: str,
    notification: Notification,
    send: Sender,
    *,
    stale_after_s: int = PENDING_STALE_AFTER_S,
) -> str:
    """Deliver one notification on one channel, effectively once.

    Returns "delivered" | "in_flight" | "skipped" | "failed".

    * Dedup (ADR-0001): a row marked `delivered` or `failed` is finished work → `skipped`,
      and the caller may delete the message. Anything another worker provably holds right
      now — a lost conditional put, a lost re-claim, or a `pending` row too young to be
      dead — returns `in_flight`, and the caller must KEEP the message: deleting it bets
      that the other worker survives, and losing that bet loses the notification. A
      `pending` row older than `stale_after_s` is a crashed attempt → re-claim it
      (conditionally on `updated_at`, so two workers can't both proceed) and retry.
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
            return "in_flight"
    elif existing["status"] != "pending":
        return "skipped"
    else:
        seen = int(existing["updated_at"])
        if time.time() - seen < stale_after_s:
            return "in_flight"
        if not store.reclaim_delivery(nid, channel, seen):
            return "in_flight"

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
