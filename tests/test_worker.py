"""The delivery loop (ADR-0001/0003/0005) with fake senders and a moto store.
RED until `deliver` (and notification_id) are written."""

from __future__ import annotations

import pytest

from notifier.handlers.worker import deliver
from notifier.models import Notification, PermanentError, TransientError


def _n(user="u1", key="k1"):
    return Notification(user_id=user, type="welcome", payload={"name": "G"}, idempotency_key=key)


class _CountingSender:
    def __init__(self, raise_=None):
        self.calls = 0
        self.raise_ = raise_

    def __call__(self, n):
        self.calls += 1
        if self.raise_:
            raise self.raise_


def test_delivers_once_and_dedups_redelivery(store):
    store.put_prefs("u1", ["email"])
    send = _CountingSender()
    assert deliver(store, "email", _n(), send) == "delivered"
    assert deliver(store, "email", _n(), send) == "skipped"  # SQS redelivered it
    assert send.calls == 1
    assert store.get_delivery(_n().notification_id, "email")["status"] == "delivered"


def test_channels_dedup_independently(store):
    store.put_prefs("u1", ["email", "sms"])
    e, s = _CountingSender(), _CountingSender()
    deliver(store, "email", _n(), e)
    deliver(store, "sms", _n(), s)  # same notification, other channel: must still send
    assert e.calls == 1 and s.calls == 1


def test_permanent_failure_is_recorded_not_raised(store):
    store.put_prefs("u1", ["email"])
    send = _CountingSender(raise_=PermanentError("bad address"))
    assert deliver(store, "email", _n(), send) == "failed"
    assert store.get_delivery(_n().notification_id, "email")["status"] == "failed"


def test_transient_failure_propagates_for_retry(store):
    store.put_prefs("u1", ["email"])
    send = _CountingSender(raise_=TransientError("timeout"))
    with pytest.raises(TransientError):
        deliver(store, "email", _n(), send)
    # not marked delivered -> a redelivery will try again
    assert store.get_delivery(_n().notification_id, "email")["status"] != "delivered"


def test_opted_out_is_permanent_failure(store):
    store.put_prefs("u1", ["sms"])  # email NOT allowed
    send = _CountingSender()
    assert deliver(store, "email", _n(), send) == "failed"
    assert send.calls == 0  # never even attempted


def _backdate(store, nid, channel, seconds):
    store._deliveries.update_item(
        Key={"pk": f"{nid}#{channel}"},
        UpdateExpression="SET updated_at = updated_at - :d",
        ExpressionAttributeValues={":d": seconds},
    )


def test_pending_from_a_crashed_attempt_is_retried(store):
    store.put_prefs("u1", ["email"])
    nid = _n().notification_id
    store.put_delivery_if_absent(nid, "email")  # an earlier attempt claimed, then crashed
    _backdate(store, nid, "email", 3600)
    send = _CountingSender()
    assert deliver(store, "email", _n(), send) == "delivered"
    assert send.calls == 1
    assert int(store.get_delivery(nid, "email")["attempts"]) == 2


def test_fresh_pending_is_in_flight_elsewhere_so_skipped(store):
    store.put_prefs("u1", ["email"])
    nid = _n().notification_id
    store.put_delivery_if_absent(nid, "email")  # another worker is sending right now
    send = _CountingSender()
    assert deliver(store, "email", _n(), send) == "skipped"
    assert send.calls == 0


def test_losing_the_claim_race_skips(store, monkeypatch):
    store.put_prefs("u1", ["email"])
    nid = _n().notification_id
    # Both workers read "no row"; the other one's conditional put lands first.
    monkeypatch.setattr(store, "get_delivery", lambda *a: None)
    store.put_delivery_if_absent(nid, "email")
    send = _CountingSender()
    assert deliver(store, "email", _n(), send) == "skipped"
    assert send.calls == 0


def test_permanent_failure_is_not_retried_on_redelivery(store):
    store.put_prefs("u1", ["email"])
    send = _CountingSender(raise_=PermanentError("bad address"))
    assert deliver(store, "email", _n(), send) == "failed"
    assert deliver(store, "email", _n(), send) == "skipped"
    assert send.calls == 1
