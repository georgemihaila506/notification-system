"""db.Store against moto — the conditional-write dedup primitive. These pass now."""

from __future__ import annotations


def test_put_delivery_if_absent_claims_once(store):
    assert store.put_delivery_if_absent("n1", "email") is True
    assert store.put_delivery_if_absent("n1", "email") is False  # already claimed
    assert store.put_delivery_if_absent("n1", "sms") is True  # other channel is separate


def test_mark_delivery_updates_status_not_attempts(store):
    store.put_delivery_if_absent("n1", "email")
    store.mark_delivery("n1", "email", "delivered")
    row = store.get_delivery("n1", "email")
    assert row["status"] == "delivered" and int(row["attempts"]) == 1


def test_reclaim_delivery_is_conditional_on_what_was_read(store):
    store.put_delivery_if_absent("n1", "email")
    seen = int(store.get_delivery("n1", "email")["updated_at"])
    assert store.reclaim_delivery("n1", "email", seen) is True
    assert int(store.get_delivery("n1", "email")["attempts"]) == 2
    # a second worker holding the same stale read loses
    assert store.reclaim_delivery("n1", "email", seen - 1) is False


def test_prefs_roundtrip(store):
    assert store.get_prefs("u1") is None
    store.put_prefs("u1", ["email", "push"], {"email": "u1@example.com"})
    row = store.get_prefs("u1")
    assert row["channels"] == ["email", "push"]
    assert row["addresses"] == {"email": "u1@example.com"}


def test_prefs_without_addresses_is_allowed(store):
    """Addresses are optional at write time -- a channel with no address fails at
    send time (ADR-0003 permanent), not here."""
    store.put_prefs("u1", ["email"])
    assert store.get_prefs("u1")["channels"] == ["email"]


def test_increment_counter_is_atomic(store):
    assert store.increment_counter("u1", "2026-09-14T16") == 1
    assert store.increment_counter("u1", "2026-09-14T16") == 2
