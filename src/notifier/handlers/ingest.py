"""POST /notifications — validate, identify, rate limit, route, publish.

Thin glue around the domain: parse the request, build a Notification (its id comes
from the caller's idempotency key — ADR-0001), charge it against the user's hourly
quota (ADR-0006), look up the user's allowed channels, and publish ONCE to SNS with
those channels as a message attribute so the per-channel queues can filter
(ADR-0002). Returns 202: accepted for delivery, not yet delivered — or 429 when the
quota is spent.

Note the quota counts REQUESTS while ADR-0001 dedups NOTIFICATIONS, so a caller
retrying a timed-out POST spends quota on a notification that was already accepted.
Deduping the counter would need a read before the atomic ADD, which is the whole
thing the ADD exists to avoid.
"""

from __future__ import annotations

import json
import logging
import os

import boto3

from ..db import Store, rate_window
from ..models import Notification

logger = logging.getLogger(__name__)


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def _publish(topic_arn: str, notification: Notification, channels: list[str]) -> None:
    """Publish once. `channels` rides along as a String.Array attribute — that's what
    the SNS filter policies match with `channel ∋ "email"` etc. (Done for you.)"""
    boto3.client("sns").publish(
        TopicArn=topic_arn,
        Message=notification.to_wire(),
        MessageAttributes={
            "channels": {
                "DataType": "String.Array",
                "StringValue": json.dumps(channels),
            }
        },
    )


def handler(event: dict, context: object) -> dict:
    """Turn a POST into an accepted, routed notification.

    202 {notification_id, channels} on success (accepted, not yet delivered); 400 for a
    malformed body or missing/mistyped fields — `idempotency_key` is REQUIRED
    (ADR-0001), so a retried request yields the same notification_id; 500 if publishing
    fails. No prefs row means nothing to route: still a 202 with `channels: []`.
    """
    # Parse + validate. from_wire() raises KeyError for a missing field; the type
    # checks catch e.g. a numeric user_id or a non-dict payload.
    try:
        notification = Notification.from_wire(event["body"])
        if not all(
            isinstance(v, str) and v
            for v in (
                notification.user_id,
                notification.type,
                notification.idempotency_key,
            )
        ) or not isinstance(notification.payload, dict):
            raise ValueError(
                "fields must be non-empty strings; payload must be an object"
            )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as err:
        return _response(400, {"error": f"invalid request: {err}"})

    store = Store(
        os.environ["PREFS_TABLE"],
        os.environ["DELIVERIES_TABLE"],
        rate_table=os.environ["RATE_TABLE"],
    )

    # Rate limit (ADR-0006) after validation but before publishing: counting
    # malformed requests would let a broken client exhaust a real user's quota,
    # and counting after publishing would not stop anything.
    used = store.increment_counter(notification.user_id, rate_window())
    if used > int(os.environ["RATE_LIMIT_PER_HOUR"]):
        return _response(429, {"error": "rate limit exceeded"})

    prefs = store.get_prefs(notification.user_id)
    channels = prefs.get("channels", []) if prefs else []

    # Publish once; the per-channel queues filter on the `channels` attribute.
    try:
        if channels:
            _publish(os.environ["TOPIC_ARN"], notification, channels)
    except Exception:
        logger.exception("failed to publish notification")
        return _response(500, {"error": "could not accept notification"})

    return _response(
        202, {"notification_id": notification.notification_id, "channels": channels}
    )
