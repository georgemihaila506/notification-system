"""handlers.ingest — validate, identify, route, publish (M2). RED until written."""

from __future__ import annotations

import json

from notifier.handlers import ingest


def _event(body: dict | str) -> dict:
    return {"body": body if isinstance(body, str) else json.dumps(body)}


def _good(key="k1"):
    return {"user_id": "u1", "type": "welcome", "payload": {"name": "G"}, "idempotency_key": key}


def test_accepts_and_publishes_with_channels(api_env):
    store, queue = api_env
    store.put_prefs("u1", ["email", "push"])
    resp = ingest.handler(_event(_good()), None)
    assert resp["statusCode"] == 202
    body = json.loads(resp["body"])
    assert len(body["notification_id"]) == 64 and body["channels"] == ["email", "push"]
    msgs = queue.receive_messages(MaxNumberOfMessages=1, WaitTimeSeconds=1)
    assert len(msgs) == 1
    envelope = json.loads(msgs[0].body)  # SNS -> SQS envelope
    assert json.loads(envelope["Message"])["idempotency_key"] == "k1"
    assert json.loads(envelope["MessageAttributes"]["channels"]["Value"]) == ["email", "push"]


def test_retry_with_same_key_gives_same_id(api_env):
    store, _ = api_env
    store.put_prefs("u1", ["email"])
    a = json.loads(ingest.handler(_event(_good("same")), None)["body"])["notification_id"]
    b = json.loads(ingest.handler(_event(_good("same")), None)["body"])["notification_id"]
    assert a == b


def test_missing_idempotency_key_is_400(api_env):
    bad = _good()
    del bad["idempotency_key"]
    assert ingest.handler(_event(bad), None)["statusCode"] == 400


def test_malformed_json_is_400(api_env):
    assert ingest.handler(_event("not json{"), None)["statusCode"] == 400


def test_over_the_hourly_limit_is_429(api_env):
    """ADR-0006: a fixed window counter, one atomic ADD, rejected at ingest. The
    fixture sets RATE_LIMIT_PER_HOUR=3, so the fourth request in the hour fails."""
    store, queue = api_env
    store.put_prefs("u1", ["email"], {"email": "u1@example.com"})
    codes = [
        ingest.handler(_event(_good(f"k{i}")), None)["statusCode"] for i in range(4)
    ]
    assert codes == [202, 202, 202, 429]


def test_a_rejected_request_publishes_nothing(api_env):
    """The limit protects the user from being spammed, so the rejection has to
    happen before the message reaches SNS, not after."""
    store, queue = api_env
    store.put_prefs("u1", ["email"], {"email": "u1@example.com"})
    for i in range(4):
        ingest.handler(_event(_good(f"k{i}")), None)
    msgs = queue.receive_messages(MaxNumberOfMessages=10, WaitTimeSeconds=1)
    assert len(msgs) == 3


def test_the_limit_is_per_user(api_env):
    store, _ = api_env
    store.put_prefs("u1", ["email"], {"email": "u1@example.com"})
    store.put_prefs("u2", ["email"], {"email": "u2@example.com"})
    for i in range(3):
        ingest.handler(_event(_good(f"k{i}")), None)
    other = dict(_good("k0"), user_id="u2")
    assert ingest.handler(_event(other), None)["statusCode"] == 202


def test_a_retried_request_still_costs_a_token(api_env):
    """Honest consequence of counting at ingest: idempotency collapses duplicate
    NOTIFICATIONS (ADR-0001) but the rate limiter counts REQUESTS, so a caller
    retrying a timed-out POST spends its quota. Documented, not fixed -- deduping
    the counter would need a read before the atomic ADD."""
    store, _ = api_env
    store.put_prefs("u1", ["email"], {"email": "u1@example.com"})
    codes = [ingest.handler(_event(_good("same")), None)["statusCode"] for _ in range(4)]
    assert codes == [202, 202, 202, 429]


def test_no_prefs_means_no_publish_but_still_202(api_env):
    store, queue = api_env  # u1 has no prefs row
    resp = ingest.handler(_event(_good()), None)
    assert resp["statusCode"] == 202
    assert json.loads(resp["body"])["channels"] == []
    assert queue.receive_messages(MaxNumberOfMessages=1, WaitTimeSeconds=1) == []
