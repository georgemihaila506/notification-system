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


def test_no_prefs_means_no_publish_but_still_202(api_env):
    store, queue = api_env  # u1 has no prefs row
    resp = ingest.handler(_event(_good()), None)
    assert resp["statusCode"] == 202
    assert json.loads(resp["body"])["channels"] == []
    assert queue.receive_messages(MaxNumberOfMessages=1, WaitTimeSeconds=1) == []
