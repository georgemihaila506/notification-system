"""db.Store against moto — the conditional-write dedup primitive. These pass now."""

from __future__ import annotations


def test_put_delivery_if_absent_claims_once(store):
    assert store.put_delivery_if_absent("n1", "email", "u1") is True
    assert store.put_delivery_if_absent("n1", "email", "u1") is False  # already claimed
    assert store.put_delivery_if_absent("n1", "sms", "u1") is True  # other channel is separate


def test_mark_delivery_updates_status_not_attempts(store):
    store.put_delivery_if_absent("n1", "email", "u1")
    store.mark_delivery("n1", "email", "sent")
    row = store.get_delivery("n1", "email")
    assert row["status"] == "sent" and int(row["attempts"]) == 1


def test_reclaim_delivery_is_conditional_on_what_was_read(store):
    store.put_delivery_if_absent("n1", "email", "u1")
    seen = int(store.get_delivery("n1", "email")["updated_at"])
    assert store.reclaim_delivery("n1", "email", seen) is True
    assert int(store.get_delivery("n1", "email")["attempts"]) == 2
    # a second worker holding the same stale read loses
    assert store.reclaim_delivery("n1", "email", seen - 1) is False


def test_claim_records_the_user_id(store):
    """The SES event carries notification_id and channel but no user_id, and
    suppression needs one to find the prefs row. Tagging it onto the send is not
    safe (SES tag values allow only [A-Za-z0-9_-] and user ids are caller-supplied),
    so the ledger row carries it instead (ADR-0009)."""
    store.put_delivery_if_absent("n1", "email", "u1")
    assert store.get_delivery("n1", "email")["user_id"] == "u1"


def test_set_provider_status_returns_the_updated_row(store):
    store.put_delivery_if_absent("n1", "email", "u1")
    row = store.set_provider_status("n1", "email", "bounced", {"bounce_type": "Permanent"})
    assert row["provider_status"] == "bounced"
    assert row["bounce_type"] == "Permanent"
    assert row["user_id"] == "u1"  # handed back in the same call, no extra read
    assert row["status"] == "pending"  # the sender's attribute is untouched


def test_set_provider_status_on_an_unknown_row_creates_nothing(store):
    """An event may arrive for a notification this ledger never saw -- a send that
    predates the table, or a manual aws ses send-email. update_item would happily
    upsert a half-row; it must not."""
    assert store.set_provider_status("nope", "email", "delivered") is None
    assert store.get_delivery("nope", "email") is None


def test_disable_channel_removes_only_that_channel(store):
    store.put_prefs("u1", ["email", "sms"], {"email": "a@b.c", "sms": "+40"})
    store.disable_channel("u1", "email")
    row = store.get_prefs("u1")
    assert row["channels"] == ["sms"]
    assert row["addresses"] == {"email": "a@b.c", "sms": "+40"}  # address kept


def test_disable_channel_is_idempotent(store):
    """A duplicate bounce event must not corrupt the row -- SNS and SQS are both
    at-least-once, so the same event will arrive twice sooner or later."""
    store.put_prefs("u1", ["email"], {"email": "a@b.c"})
    store.disable_channel("u1", "email")
    store.disable_channel("u1", "email")
    assert store.get_prefs("u1")["channels"] == []


def test_disable_channel_on_a_missing_user_does_not_crash(store):
    store.disable_channel("ghost", "email")
    assert store.get_prefs("ghost") is None


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
