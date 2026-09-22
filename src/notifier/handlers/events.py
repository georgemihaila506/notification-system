"""SES delivery events back into the ledger (M5, ADR-0009).

The inbound mirror of handlers.worker: SES publishes what actually happened to a
message — delivered, bounced, complained, delayed — to a configuration set's event
destination, which fans out SNS -> SQS -> here.

Two things make this possible. The send tags each message with its
`notification_id` and `channel` (channels/ses.py), so an event arrives already
carrying the ledger key. And outcomes are written to `provider_status`, a
different attribute from the sender's `status`, so the two writers can never
clobber each other regardless of which lands first.

Acting on an outcome needs no new machinery: a permanent bounce or a complaint
removes the channel from the user's prefs, and ADR-0005's existing checks — at
ingest and immediately before every send — stop everything after that.
"""

from __future__ import annotations

import json
import logging
import os

from ..db import Store

logger = logging.getLogger(__name__)

# SES eventType -> the provider_status we record. Anything absent is ignored:
# a destination can also publish Send, Open, Click, Reject and more, and an
# unrecognised type must be a quiet no-op rather than a crash or a DLQ trip.
STATUS_BY_EVENT: dict[str, str] = {
    "Delivery": "delivered",
    "Bounce": "bounced",
    "Complaint": "complained",
    "DeliveryDelay": "delayed",
}


def handler(event: dict, context: object) -> dict:
    """SQS-triggered entry point for SES events. Same contract as the delivery
    worker: every record listed in `batchItemFailures` stays on the queue, and
    everything else is deleted.

    Outcomes are written to `provider_status`, never to the sender's `status`, so
    an event arriving before the sending worker has finished writing cannot
    overwrite it (ADR-0009). A Complaint, or a Bounce whose `bounceType` is
    `Permanent`, also removes the channel from the user's prefs — ADR-0005's
    existing checks then stop every future send with no new enforcement path. A
    Transient or Undetermined bounce is recorded only, and is never re-sent: the
    SQS message was deleted long before the event arrived.

    Three things are deliberately quiet rather than errors, because a DLQ message
    here fires an alarm and none of them mean anything is wrong:
      * an event type nobody maps (a destination may also publish Send, Open,
        Click, Reject);
      * an event with no correlation tags — some other sender used this
        configuration set, so the message was never ours;
      * an event whose ledger row does not exist, e.g. a send predating the table.

    A record that cannot be parsed IS kept, and exhausts its retries into the DLQ
    with its body intact rather than vanishing. Duplicate events are certain (SNS
    and SQS are both at-least-once) and are harmless: the same `provider_status`
    is rewritten and `disable_channel` is idempotent.
    """
    batch_failures = []
    store = Store(os.environ["PREFS_TABLE"], os.environ["DELIVERIES_TABLE"])
    for record in event.get("Records", []):
        message_id = record.get("messageId")
        try:
            ses_event = json.loads(json.loads(record["body"])["Message"])
            status = STATUS_BY_EVENT.get(ses_event["eventType"])
            if not status:
                continue  # uninteresting type, e.g. Send, Open, Click

            # SES returns tag values as LISTS: ["abc..."], not "abc...".
            tags = ses_event["mail"].get("tags", {})
            if "notification_id" not in tags or "channel" not in tags:
                # Not one of ours — anything sent through this configuration set
                # publishes events, tagged or not. Dropping it quietly keeps the
                # DLQ alarm meaningful.
                logger.info("untagged SES event ignored: %s", ses_event["eventType"])
                continue
            nid, channel = tags["notification_id"][0], tags["channel"][0]

            detail = None
            if status == "bounced":
                detail = {"bounce_type": ses_event["bounce"]["bounceType"]}

            row = store.set_provider_status(nid, channel, status, detail)
            if not row:
                continue  # best-effort: no such row, e.g. a send predating the ledger

            if status == "complained" or (
                status == "bounced" and detail["bounce_type"] == "Permanent"
            ):
                store.disable_channel(row["user_id"], channel)
        except (KeyError, TypeError, ValueError):
            # Narrow on purpose, matching handlers.worker: a malformed record is
            # kept for the DLQ, but anything unexpected propagates and fails the
            # whole invoke, which keeps the batch. Retrying the unknown is safe.
            logger.exception("unparseable SES event %s kept for DLQ", message_id)
            batch_failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": batch_failures}
