"""DynamoDB access — the ONLY module that talks to the tables.

Two tables:
  prefs       pk=user_id               -> channels (list), [rate counter in M6]
  deliveries  pk=notification_id#channel -> status (pending|delivered|failed), attempts

The deliveries table is the dedup ledger (ADR-0001): one row per notification per
channel. `put_delivery_if_absent` is the conditional write that makes DynamoDB the
uniqueness referee.
"""

from __future__ import annotations

import time

import boto3
from botocore.exceptions import ClientError


def delivery_key(notification_id: str, channel: str) -> str:
    return f"{notification_id}#{channel}"


class Store:
    def __init__(self, prefs_table: str, deliveries_table: str, *, dynamodb=None) -> None:
        ddb = dynamodb or boto3.resource("dynamodb")
        self._prefs = ddb.Table(prefs_table)
        self._deliveries = ddb.Table(deliveries_table)

    # --- preferences -----------------------------------------------------------
    def get_prefs(self, user_id: str) -> dict | None:
        return self._prefs.get_item(Key={"pk": user_id}).get("Item")

    def put_prefs(self, user_id: str, channels: list[str]) -> None:
        self._prefs.put_item(Item={"pk": user_id, "channels": channels})

    # --- deliveries (dedup ledger) ----------------------------------------------
    def get_delivery(self, notification_id: str, channel: str) -> dict | None:
        key = delivery_key(notification_id, channel)
        return self._deliveries.get_item(Key={"pk": key}).get("Item")

    def put_delivery_if_absent(self, notification_id: str, channel: str) -> bool:
        """Claim this (notification, channel) as pending. True if we claimed it;
        False if a row already existed (someone got there first)."""
        try:
            self._deliveries.put_item(
                Item={
                    "pk": delivery_key(notification_id, channel),
                    "status": "pending",
                    "attempts": 1,
                    "updated_at": int(time.time()),
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
            return True
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def mark_delivery(self, notification_id: str, channel: str, status: str) -> None:
        self._deliveries.update_item(
            Key={"pk": delivery_key(notification_id, channel)},
            UpdateExpression="SET #s = :s, updated_at = :t ADD attempts :one",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": status, ":t": int(time.time()), ":one": 1},
        )

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
