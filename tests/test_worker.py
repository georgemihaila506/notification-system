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

    def __call__(self, n, address):
        self.calls += 1
        self.address = address
        if self.raise_:
            raise self.raise_


def test_the_channels_address_is_passed_to_the_sender(store):
    """deliver() already reads prefs for the opt-out re-check, so it resolves the
    address from that same row -- senders never touch the store."""
    store.put_prefs("u1", ["email"], {"email": "george@example.com"})
    send = _CountingSender()
    assert deliver(store, "email", _n(), send) == "sent"
    assert send.address == "george@example.com"


@pytest.mark.parametrize(
    "item",
    [
        {"pk": "u1", "channels": ["email"]},
        {"pk": "u1", "channels": ["email"], "addresses": {}},
        {"pk": "u1", "channels": ["email"], "addresses": {"sms": "+40000000000"}},
    ],
    ids=["attribute-absent", "addresses-empty", "other-channel-only"],
)
def test_enabled_but_no_address_is_permanent(store, item):
    """Enabled with nowhere to send is unfixable by retrying (ADR-0003): record
    `failed`, never call the sender. These three rows are one behaviour -- no entry
    for this channel, however it got that way -- so they are inputs to one test.
    Whether an entry's VALUE is usable is the sender's call, not deliver()'s: only
    the sender knows what valid looks like for its channel. Written directly:
    put_prefs() always writes the key."""
    store._prefs.put_item(Item=item)
    send = _CountingSender()
    assert deliver(store, "email", _n(), send) == "failed"
    assert send.calls == 0
    assert store.get_delivery(_n().notification_id, "email")["status"] == "failed"


def test_delivers_once_and_dedups_redelivery(store):
    store.put_prefs("u1", ["email"], {"email": "u1@example.com"})
    send = _CountingSender()
    assert deliver(store, "email", _n(), send) == "sent"
    assert deliver(store, "email", _n(), send) == "skipped"  # SQS redelivered it
    assert send.calls == 1
    assert store.get_delivery(_n().notification_id, "email")["status"] == "sent"


def test_channels_dedup_independently(store):
    store.put_prefs("u1", ["email", "sms"], {"email": "u1@example.com", "sms": "+40000000000"})
    e, s = _CountingSender(), _CountingSender()
    deliver(store, "email", _n(), e)
    deliver(store, "sms", _n(), s)  # same notification, other channel: must still send
    assert e.calls == 1 and s.calls == 1


def test_permanent_failure_is_recorded_not_raised(store):
    store.put_prefs("u1", ["email"], {"email": "u1@example.com"})
    send = _CountingSender(raise_=PermanentError("bad address"))
    assert deliver(store, "email", _n(), send) == "failed"
    assert store.get_delivery(_n().notification_id, "email")["status"] == "failed"


def test_transient_failure_propagates_for_retry(store):
    store.put_prefs("u1", ["email"], {"email": "u1@example.com"})
    send = _CountingSender(raise_=TransientError("timeout"))
    with pytest.raises(TransientError):
        deliver(store, "email", _n(), send)
    # not marked delivered -> a redelivery will try again
    assert store.get_delivery(_n().notification_id, "email")["status"] != "sent"


def test_opted_out_is_permanent_failure(store):
    store.put_prefs("u1", ["sms"], {"sms": "+40000000000"})  # email NOT allowed
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
    store.put_prefs("u1", ["email"], {"email": "u1@example.com"})
    nid = _n().notification_id
    store.put_delivery_if_absent(nid, "email", "u1")  # an earlier attempt claimed, then crashed
    _backdate(store, nid, "email", 3600)
    send = _CountingSender()
    assert deliver(store, "email", _n(), send) == "sent"
    assert send.calls == 1
    assert int(store.get_delivery(nid, "email")["attempts"]) == 2


def test_fresh_pending_is_in_flight_not_skipped(store):
    """Too young to be dead -> in_flight, NOT skipped. The caller must keep the
    message: if the worker holding it dies, deleting here would lose it."""
    store.put_prefs("u1", ["email"], {"email": "u1@example.com"})
    nid = _n().notification_id
    store.put_delivery_if_absent(nid, "email", "u1")  # another worker is sending right now
    send = _CountingSender()
    assert deliver(store, "email", _n(), send) == "in_flight"
    assert send.calls == 0


def test_losing_the_claim_race_is_in_flight(store, monkeypatch):
    store.put_prefs("u1", ["email"], {"email": "u1@example.com"})
    nid = _n().notification_id
    # Both workers read "no row"; the other one's conditional put lands first.
    monkeypatch.setattr(store, "get_delivery", lambda *a: None)
    store.put_delivery_if_absent(nid, "email", "u1")
    send = _CountingSender()
    assert deliver(store, "email", _n(), send) == "in_flight"
    assert send.calls == 0


def test_retry_arriving_at_the_visibility_timeout_is_reclaimed(store):
    """The bug the live drill found: an SQS retry lands at ~the visibility timeout
    after the claim. With stale_after_s below that, it must be reclaimed and
    retried -- not mistaken for an in-flight worker and dropped."""
    store.put_prefs("u1", ["email"], {"email": "u1@example.com"})
    nid = _n().notification_id
    store.put_delivery_if_absent(nid, "email", "u1")
    _backdate(store, nid, "email", 58)  # observed gap for a 60s visibility timeout
    send = _CountingSender()
    assert deliver(store, "email", _n(), send, stale_after_s=30) == "sent"
    assert send.calls == 1


def test_permanent_failure_is_not_retried_on_redelivery(store):
    store.put_prefs("u1", ["email"], {"email": "u1@example.com"})
    send = _CountingSender(raise_=PermanentError("bad address"))
    assert deliver(store, "email", _n(), send) == "failed"
    assert deliver(store, "email", _n(), send) == "skipped"
    assert send.calls == 1
