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

from ..channels.ses import ses_sender
from ..channels.simulated import simulated
from ..db import Store
from ..models import CHANNELS, Notification, PermanentError, TransientError

logger = logging.getLogger(__name__)

# A channel's send function: given the notification and the destination address
# deliver() resolved for this channel, deliver it or raise TransientError /
# PermanentError. Senders never read the store — the address is handed to them.
Sender = Callable[[Notification, str], None]

# channel -> its sender. Email is real (ADR-0004); sms and push stay simulated,
# which is what makes them the deterministic rig for the retry/DLQ drills.
# Built at import so each container creates its boto3 clients once.
SENDERS: dict[str, Sender] = {c: simulated(c) for c in CHANNELS} | {
    "email": ses_sender("email", os.environ["SES_SOURCE"], os.environ["SES_CONFIG_SET"])
}


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
      * `sent`, `failed` and `skipped` — the work is finished, and deleting the
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

    Returns "sent" | "in_flight" | "skipped" | "failed".

    `sent` means the provider accepted it, which is NOT the same as the recipient
    receiving it — that only becomes known later, from the provider's own events, and
    is recorded on a separate `provider_status` attribute (ADR-0009). Calling this
    state `delivered` was a lie the M4 drill caught: SES accepted a message Gmail then
    filed as spam.

    * Dedup (ADR-0001): a row marked `sent` or `failed` is finished work → `skipped`,
      and the caller may delete the message. Anything another worker provably holds right
      now — a lost conditional put, a lost re-claim, or a `pending` row too young to be
      dead — returns `in_flight`, and the caller must KEEP the message: deleting it bets
      that the other worker survives, and losing that bet loses the notification. A
      `pending` row older than `stale_after_s` is a crashed attempt → re-claim it
      (conditionally on `updated_at`, so two workers can't both proceed) and retry.
    * Opt-out re-check (ADR-0005) happens right before sending, inside the same
      `try` as the send, so it's recorded as a permanent failure like any other.
    * Address resolution reuses that same prefs row — one GetItem serves both. A
      channel that is enabled but has no address (or a row predating `addresses`
      entirely) is a PermanentError: no retry will conjure a destination. Checked
      after opt-out, so a disabled channel reads as "opted out" rather than the
      more confusing "no address" for something the user never wanted.
    * Classification (ADR-0003): a PermanentError is recorded as `failed` and swallowed —
      never retried. A TransientError deliberately propagates: raising out of the
      worker leaves the SQS message undeleted, which is exactly what makes it retry.
    """
    nid = notification.notification_id
    existing = store.get_delivery(nid, channel)
    if existing is None:
        if not store.put_delivery_if_absent(nid, channel, notification.user_id):
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
        if not prefs or channel not in prefs.get("channels", []):
            raise PermanentError("opted out")
        if channel not in prefs.get("addresses", {}):
            raise PermanentError(f"no address for channel {channel}")
        address = prefs["addresses"][channel]
        send(notification, address)
    except PermanentError as err:
        # why: the ledger records THAT it failed; only this records WHY. Without it a
        # permanent failure is completely silent — no log line anywhere — and the
        # chained provider reason ("Email address is not verified...") is lost.
        logger.warning("permanent failure %s/%s: %s", nid, channel, err)
        store.mark_delivery(nid, channel, "failed")
        return "failed"

    store.mark_delivery(nid, channel, "sent")
    return "sent"
