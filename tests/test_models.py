"""Notification identity (ADR-0001). RED until notification_id is written."""

from __future__ import annotations

from notifier.models import Notification


def _n(user="u1", key="k1"):
    return Notification(user_id=user, type="welcome", payload={"name": "G"}, idempotency_key=key)


def test_same_user_and_key_gives_same_id():
    assert _n().notification_id == _n().notification_id  # a retried POST collapses


def test_different_users_same_key_do_not_collide():
    assert _n(user="u1").notification_id != _n(user="u2").notification_id


def test_different_keys_differ():
    assert _n(key="k1").notification_id != _n(key="k2").notification_id


def test_id_is_a_clean_fixed_length_key():
    nid = _n().notification_id
    assert len(nid) == 64 and all(c in "0123456789abcdef" for c in nid)


def test_wire_roundtrip():
    n = _n()
    assert Notification.from_wire(n.to_wire()) == n
