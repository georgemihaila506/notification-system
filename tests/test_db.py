"""db.Store against moto — the conditional-write dedup primitive. These pass now."""

from __future__ import annotations


def test_put_delivery_if_absent_claims_once(store):
    assert store.put_delivery_if_absent("n1", "email") is True
    assert store.put_delivery_if_absent("n1", "email") is False  # already claimed
    assert store.put_delivery_if_absent("n1", "sms") is True  # other channel is separate


def test_mark_delivery_updates_status_and_attempts(store):
    store.put_delivery_if_absent("n1", "email")
    store.mark_delivery("n1", "email", "delivered")
    row = store.get_delivery("n1", "email")
    assert row["status"] == "delivered" and int(row["attempts"]) == 2


def test_prefs_roundtrip(store):
    assert store.get_prefs("u1") is None
    store.put_prefs("u1", ["email", "push"])
    assert store.get_prefs("u1")["channels"] == ["email", "push"]


def test_increment_counter_is_atomic(store):
    assert store.increment_counter("u1", "2026-09-14T16") == 1
    assert store.increment_counter("u1", "2026-09-14T16") == 2
