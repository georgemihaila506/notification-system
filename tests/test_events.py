"""handlers.events — SES delivery/bounce/complaint events back into the ledger
(M5, ADR-0009). RED until `handler` is written.

The inbound mirror of the delivery pipeline: same SNS->SQS double envelope, same
batchItemFailures contract, but it writes `provider_status` rather than `status`
and may suppress a channel.
"""

from __future__ import annotations

import json

import pytest

from notifier.handlers import events

from conftest import DELIVERIES_TABLE, PREFS_TABLE

NID = "a" * 64  # a sha256-shaped notification_id


def _event(event_type: str, nid: str = NID, channel: str = "email", **detail) -> dict:
    """One SES event, as it looks inside the SNS envelope inside the SQS record.
    Note the tag values are LISTS -- that is SES's shape, not a typo."""
    body = {
        "eventType": event_type,
        "mail": {
            "messageId": "0100018f-real",
            "tags": {
                "notification_id": [nid],
                "channel": [channel],
                "ses:configuration-set": ["notify-events"],
            },
        },
    }
    body.update(detail)
    return body


def _sqs(*bodies: dict) -> dict:
    return {
        "Records": [
            {"messageId": f"msg-{i}", "body": json.dumps({"Message": json.dumps(b)})}
            for i, b in enumerate(bodies)
        ]
    }


@pytest.fixture
def env(store, monkeypatch):
    monkeypatch.setenv("PREFS_TABLE", PREFS_TABLE)
    monkeypatch.setenv("DELIVERIES_TABLE", DELIVERIES_TABLE)
    store.put_prefs("u1", ["email", "sms"], {"email": "a@b.c", "sms": "+40"})
    store.put_delivery_if_absent(NID, "email", "u1")
    return store


def test_delivery_event_confirms_the_send(env):
    resp = events.handler(_sqs(_event("Delivery")), None)
    assert resp == {"batchItemFailures": []}
    row = env.get_delivery(NID, "email")
    assert row["provider_status"] == "delivered"
    assert row["status"] == "pending"  # the sender's attribute is not touched


def test_permanent_bounce_records_and_suppresses(env):
    events.handler(
        _sqs(_event("Bounce", bounce={"bounceType": "Permanent", "bounceSubType": "General"})),
        None,
    )
    row = env.get_delivery(NID, "email")
    assert row["provider_status"] == "bounced"
    assert row["bounce_type"] == "Permanent"
    # ADR-0009: suppression needs no new enforcement -- ADR-0005 already re-checks
    # prefs at ingest and before every send, so removing the channel stops both.
    assert env.get_prefs("u1")["channels"] == ["sms"]


def test_transient_bounce_records_but_does_not_suppress(env):
    """A full mailbox is not a dead address. Recorded, not acted on -- and not
    retried either: the SQS message was deleted long before this event arrived."""
    events.handler(
        _sqs(_event("Bounce", bounce={"bounceType": "Transient", "bounceSubType": "MailboxFull"})),
        None,
    )
    assert env.get_delivery(NID, "email")["provider_status"] == "bounced"
    assert env.get_prefs("u1")["channels"] == ["email", "sms"]


def test_complaint_records_and_suppresses(env):
    """Someone pressed 'mark as spam'. The strongest possible stop signal, and the
    thing AWS suspends accounts over."""
    events.handler(_sqs(_event("Complaint", complaint={"complaintFeedbackType": "abuse"})), None)
    assert env.get_delivery(NID, "email")["provider_status"] == "complained"
    assert env.get_prefs("u1")["channels"] == ["sms"]


def test_delivery_delay_is_recorded_only(env):
    events.handler(_sqs(_event("DeliveryDelay")), None)
    assert env.get_delivery(NID, "email")["provider_status"] == "delayed"
    assert env.get_prefs("u1")["channels"] == ["email", "sms"]


def test_uninteresting_event_types_are_ignored(env):
    """SES can publish Send, Open, Click and more depending on the destination's
    configuration. Unknown types must be a no-op, not a crash and not a DLQ trip."""
    resp = events.handler(_sqs(_event("Open"), _event("Send")), None)
    assert resp == {"batchItemFailures": []}
    assert "provider_status" not in env.get_delivery(NID, "email")


def test_event_for_an_unknown_notification_is_dropped_quietly(env):
    """Best-effort (ADR-0009): a send predating the ledger, or a manual
    aws ses send-email, produces an event with no row. Not an error, not a retry."""
    resp = events.handler(_sqs(_event("Delivery", nid="f" * 64)), None)
    assert resp == {"batchItemFailures": []}
    assert env.get_delivery("f" * 64, "email") is None


def test_untagged_event_is_ignored_not_dlq_d(env):
    """Anything sent through this configuration set publishes events, tagged or
    not -- a manual `aws ses send-email` for instance. Those are not ours. A DLQ
    message fires an alarm, so dropping them quietly is what keeps the alarm
    meaningful (ADR-0009)."""
    body = _event("Delivery")
    del body["mail"]["tags"]["notification_id"]
    resp = events.handler(_sqs(body), None)
    assert resp == {"batchItemFailures": []}
    assert "provider_status" not in env.get_delivery(NID, "email")


def test_concurrent_suppressions_do_not_lose_one(env, monkeypatch):
    """Two events for the same user arriving together: a plain read-modify-write
    would have each write its own copy of `channels` and silently lose one
    suppression. The conditional write forces a re-read instead."""
    real_get = env._prefs.get_item
    seen = {"n": 0}

    def get_item_then_mutate(**kwargs):
        resp = real_get(**kwargs)
        # After the first read, another worker disables `sms` underneath us.
        if seen["n"] == 0 and kwargs["Key"]["pk"] == "u1":
            seen["n"] = 1
            env._prefs.update_item(
                Key={"pk": "u1"},
                UpdateExpression="SET channels = :c",
                ExpressionAttributeValues={":c": ["email"]},
            )
        return resp

    monkeypatch.setattr(env._prefs, "get_item", get_item_then_mutate)
    env.disable_channel("u1", "email")
    assert env.get_prefs("u1")["channels"] == []  # sms removal survived


def test_unparseable_record_is_kept_for_the_dlq(env):
    event = _sqs(_event("Delivery"))
    event["Records"].append({"messageId": "poison", "body": "not json{"})
    resp = events.handler(event, None)
    assert resp == {"batchItemFailures": [{"itemIdentifier": "poison"}]}
    assert env.get_delivery(NID, "email")["provider_status"] == "delivered"


def test_a_duplicate_event_is_harmless(env):
    """SNS and SQS are both at-least-once, so every event will eventually arrive
    twice. Writing the same provider_status again must change nothing."""
    e = _sqs(_event("Bounce", bounce={"bounceType": "Permanent", "bounceSubType": "General"}))
    events.handler(e, None)
    events.handler(e, None)
    assert env.get_delivery(NID, "email")["provider_status"] == "bounced"
    assert env.get_prefs("u1")["channels"] == ["sms"]
