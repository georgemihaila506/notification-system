"""channels.simulated — the fault-injectable stand-in for a provider (ADR-0004).
RED until `simulated` is written."""

from __future__ import annotations

import pytest

from notifier.channels.simulated import simulated
from notifier.handlers import worker
from notifier.models import Notification, PermanentError, TransientError


def _n(payload: dict, type_: str = "welcome") -> Notification:
    return Notification(user_id="u1", type=type_, payload=payload, idempotency_key="k")


def test_clean_send_renders_and_logs(caplog):
    with caplog.at_level("INFO"):
        simulated("sms")(_n({"name": "G"}), "+40000000000")
    assert "would deliver via sms" in caplog.text
    assert "+40000000000" in caplog.text
    assert "Welcome, G!" in caplog.text


def test_transient_hint_raises_transient():
    with pytest.raises(TransientError):
        simulated("sms")(_n({"name": "G", "simulate": "transient_fail"}), "+40000000000")


def test_permanent_hint_raises_permanent():
    with pytest.raises(PermanentError):
        simulated("sms")(_n({"name": "G", "simulate": "permanent_fail"}), "+40000000000")


def test_unknown_hint_is_permanent_so_typos_are_visible():
    with pytest.raises(PermanentError):
        simulated("sms")(_n({"name": "G", "simulate": "transient"}), "+40000000000")


def test_unknown_template_is_permanent_even_when_simulated():
    with pytest.raises(PermanentError):
        simulated("sms")(_n({"name": "G"}, type_="no_such_type"), "+40000000000")


def test_every_channel_has_a_sender_and_only_email_is_real():
    """ADR-0004: email goes through SES, sms and push stay simulated so they can
    be the deterministic rig for retry/DLQ drills."""
    assert set(worker.SENDERS) == {"email", "sms", "push"}
    assert worker.SENDERS["email"].__module__ == "notifier.channels.ses"
    assert worker.SENDERS["sms"].__module__ == "notifier.channels.simulated"
    assert worker.SENDERS["push"].__module__ == "notifier.channels.simulated"
