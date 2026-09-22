"""DynamoDB access — the ONLY module that talks to the tables.

Two tables:
  prefs       pk=user_id               -> channels (list), addresses (channel -> where
                                          to send), [rate counter in M6]
  deliveries  pk=notification_id#channel -> status (pending|delivered|failed), attempts

The deliveries table is the dedup ledger (ADR-0001): one row per notification per
channel. `put_delivery_if_absent` is the conditional write that makes DynamoDB the
uniqueness referee.
"""

from __future__ import annotations

import logging
import time

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def delivery_key(notification_id: str, channel: str) -> str:
    return f"{notification_id}#{channel}"


class Store:
    def __init__(
        self, prefs_table: str, deliveries_table: str, *, dynamodb=None
    ) -> None:
        ddb = dynamodb or boto3.resource("dynamodb")
        self._prefs = ddb.Table(prefs_table)
        self._deliveries = ddb.Table(deliveries_table)

    # --- preferences -----------------------------------------------------------
    def get_prefs(self, user_id: str) -> dict | None:
        return self._prefs.get_item(Key={"pk": user_id}).get("Item")

    def put_prefs(
        self, user_id: str, channels: list[str], addresses: dict | None = None
    ) -> None:
        self._prefs.put_item(
            Item={"pk": user_id, "channels": channels, "addresses": addresses or {}}
        )

    # --- deliveries (dedup ledger) ----------------------------------------------
    def get_delivery(self, notification_id: str, channel: str) -> dict | None:
        key = delivery_key(notification_id, channel)
        return self._deliveries.get_item(Key={"pk": key}).get("Item")

    def put_delivery_if_absent(
        self, notification_id: str, channel: str, user_id: str
    ) -> bool:
        """Claim this (notification, channel) as pending. True if we claimed it;
        False if a row already existed (someone got there first).

        `user_id` is stored because SES events carry only notification_id and
        channel, and suppression needs the prefs row (ADR-0009). It cannot ride
        along as an SES tag: tag values allow only [A-Za-z0-9_-] and user ids are
        caller-supplied.
        """
        try:
            self._deliveries.put_item(
                Item={
                    "pk": delivery_key(notification_id, channel),
                    "status": "pending",
                    "attempts": 1,
                    "updated_at": int(time.time()),
                    "user_id": user_id,
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
            return True
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def reclaim_delivery(
        self, notification_id: str, channel: str, seen_updated_at: int
    ) -> bool:
        """Re-claim a stale pending row for another attempt. Conditional on
        `updated_at` being what we read, so two workers that both see the same
        stale row can't both proceed. True if we won."""
        try:
            self._deliveries.update_item(
                Key={"pk": delivery_key(notification_id, channel)},
                UpdateExpression="SET updated_at = :t ADD attempts :one",
                ConditionExpression="updated_at = :seen",
                ExpressionAttributeValues={
                    ":t": int(time.time()),
                    ":one": 1,
                    ":seen": seen_updated_at,
                },
            )
            return True
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def mark_delivery(self, notification_id: str, channel: str, status: str) -> None:
        self._deliveries.update_item(
            Key={"pk": delivery_key(notification_id, channel)},
            UpdateExpression="SET #s = :s, updated_at = :t",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": status, ":t": int(time.time())},
        )

    # --- provider outcomes (M5, ADR-0009) ----------------------------------------
    def set_provider_status(
        self,
        notification_id: str,
        channel: str,
        provider_status: str,
        detail: dict | None = None,
    ) -> dict | None:
        """Record what the provider reported, on its own attribute.

        `provider_status` lives beside `status`, never on top of it: the sending
        worker owns one and this owns the other, so neither can clobber the other
        and no ordering guarantee is needed (SES can publish Delivery before the
        sender has finished writing).

        `detail` is merged in as extra attributes (e.g. bounce_type).

        Returns the updated row so the caller gets `user_id` without a second read,
        or None if no such row exists. Conditional on the row existing, because
        update_item would otherwise upsert a half-row for an event belonging to a
        notification this ledger never saw.
        """
        key = delivery_key(notification_id, channel)
        update_expr = "SET provider_status = :s, updated_at = :t"
        expr_attr_vals = {":s": provider_status, ":t": int(time.time())}
        if detail:
            for k, v in detail.items():
                update_expr += f", {k} = :{k}"
                expr_attr_vals[f":{k}"] = v
        try:
            resp = self._deliveries.update_item(
                Key={"pk": key},
                UpdateExpression=update_expr,
                ExpressionAttributeValues=expr_attr_vals,
                ConditionExpression="attribute_exists(pk)",
                ReturnValues="ALL_NEW",
            )
            return resp.get("Attributes")
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return None
            raise

    def disable_channel(self, user_id: str, channel: str) -> None:
        """Remove one channel from a user's prefs, leaving its address in place.

        This is the whole of suppression: ADR-0005 already re-checks prefs at
        ingest and immediately before every send, so removing the channel stops
        future sends at both layers with no new enforcement path.

        Idempotent, and a no-op for a user that does not exist — SNS and SQS are
        both at-least-once, so duplicate events are certain.

        `channels` is a List, which DynamoDB cannot remove from by value (the
        DELETE action works only on Sets), so this is a read-modify-write. The
        write is conditional on the list still being what we read: two events for
        the same user arriving together would otherwise each write their own copy
        and one suppression would be silently lost. On a clash we re-read and try
        again, the same optimistic concurrency `reclaim_delivery` uses.
        """
        for _ in range(3):
            current = (
                self._prefs.get_item(Key={"pk": user_id})
                .get("Item", {})
                .get("channels")
            )
            if current is None or channel not in current:
                return  # already gone, or user not found
            remaining = [c for c in current if c != channel]
            try:
                self._prefs.update_item(
                    Key={"pk": user_id},
                    UpdateExpression="SET channels = :c",
                    ExpressionAttributeValues={":c": remaining, ":old": current},
                    ConditionExpression="channels = :old",
                )
                return
            except ClientError as err:
                if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
                logger.warning("prefs for %s changed under us, retrying", user_id)
        logger.error("could not disable %s for %s after 3 attempts", channel, user_id)

    # --- rate limiting (M6) ------------------------------------------------------
    def increment_counter(self, user_id: str, window: str) -> int:
        """Atomically bump the user's send count for a time window; returns the new count."""
        resp = self._prefs.update_item(
            Key={"pk": f"{user_id}#rate#{window}"},
            UpdateExpression="ADD #c :one",
            ExpressionAttributeNames={"#c": "count"},
            ExpressionAttributeValues={":one": 1},
            ReturnValues="UPDATED_NEW",
        )
        return int(resp["Attributes"]["count"])
