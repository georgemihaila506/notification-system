"""handlers.worker.handler — the SQS-triggered wrapper around deliver() (M3).
RED until `handler` is written."""

from __future__ import annotations

import json

import pytest

from notifier.handlers import worker
from notifier.models import Notification, PermanentError, TransientError

from conftest import DELIVERIES_TABLE, PREFS_TABLE


def _sqs_event(*notifications: Notification) -> dict:
    """What Lambda hands the worker: one SQS record per message, each body being
    the SNS envelope whose `Message` is what ingest published."""
    return {
        "Records": [
            {
                "messageId": f"msg-{i}",
                "body": json.dumps({"Type": "Notification", "Message": n.to_wire()}),
            }
            for i, n in enumerate(notifications)
        ]
    }


def _n(key: str) -> Notification:
    return Notification(user_id="u1", type="welcome", payload={"name": "G"}, idempotency_key=key)


class _Sender:
    def __init__(self, raise_=None):
        self.calls = 0
        self.raise_ = raise_

    def __call__(self, n, address):
        self.calls += 1
        if self.raise_:
            raise self.raise_


@pytest.fixture
def worker_env(store, monkeypatch):
    """Env vars the handler reads, pointing at the moto tables from `store`."""
    monkeypatch.setenv("CHANNEL", "email")
    monkeypatch.setenv("PREFS_TABLE", PREFS_TABLE)
    monkeypatch.setenv("DELIVERIES_TABLE", DELIVERIES_TABLE)
    monkeypatch.setenv("STALE_AFTER_S", "30")
    store.put_prefs("u1", ["email"], {"email": "u1@example.com"})
    return store


def test_clean_delivery_reports_no_failures(worker_env, monkeypatch):
    send = _Sender()
    monkeypatch.setitem(worker.SENDERS, "email", send)
    resp = worker.handler(_sqs_event(_n("a")), None)
    assert resp == {"batchItemFailures": []}
    assert send.calls == 1
    assert worker_env.get_delivery(_n("a").notification_id, "email")["status"] == "delivered"


def test_transient_failure_is_reported_by_message_id(worker_env, monkeypatch):
    monkeypatch.setitem(worker.SENDERS, "email", _Sender(raise_=TransientError("timeout")))
    resp = worker.handler(_sqs_event(_n("a")), None)
    assert resp == {"batchItemFailures": [{"itemIdentifier": "msg-0"}]}


def test_permanent_failure_is_not_reported(worker_env, monkeypatch):
    monkeypatch.setitem(worker.SENDERS, "email", _Sender(raise_=PermanentError("bad address")))
    resp = worker.handler(_sqs_event(_n("a")), None)
    assert resp == {"batchItemFailures": []}  # recorded as failed, message deleted
    assert worker_env.get_delivery(_n("a").notification_id, "email")["status"] == "failed"


def test_in_flight_record_is_kept_not_deleted(worker_env, monkeypatch):
    """A record another worker provably holds must stay on the queue. Deleting it
    on the assumption that worker succeeds is how the live drill lost a message."""
    send = _Sender()
    monkeypatch.setitem(worker.SENDERS, "email", send)
    n = _n("a")
    worker_env.put_delivery_if_absent(n.notification_id, "email")
    resp = worker.handler(_sqs_event(n), None)
    assert resp == {"batchItemFailures": [{"itemIdentifier": "msg-0"}]}
    assert send.calls == 0


def test_unparseable_record_is_kept_for_dlq_not_dropped(worker_env, monkeypatch):
    send = _Sender()
    monkeypatch.setitem(worker.SENDERS, "email", send)
    event = _sqs_event(_n("a"))
    event["Records"].append({"messageId": "poison", "body": "not json{"})
    resp = worker.handler(event, None)
    # kept (-> retries -> DLQ), never silently deleted; the good record still went out
    assert resp == {"batchItemFailures": [{"itemIdentifier": "poison"}]}
    assert send.calls == 1


def test_one_bad_record_does_not_take_down_its_batch_mates(worker_env, monkeypatch):
    calls = {"n": 0}

    def flaky(n, address):
        calls["n"] += 1
        if n.idempotency_key == "b":
            raise TransientError("timeout")

    monkeypatch.setitem(worker.SENDERS, "email", flaky)
    resp = worker.handler(_sqs_event(_n("a"), _n("b"), _n("c")), None)
    assert resp == {"batchItemFailures": [{"itemIdentifier": "msg-1"}]}
    assert calls["n"] == 3  # a and c were still attempted
