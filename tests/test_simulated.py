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
        simulated("sms")(_n({"name": "G"}))
    assert "would deliver via sms" in caplog.text
    assert "Welcome, G!" in caplog.text


def test_transient_hint_raises_transient():
    with pytest.raises(TransientError):
        simulated("sms")(_n({"name": "G", "simulate": "transient_fail"}))


def test_permanent_hint_raises_permanent():
    with pytest.raises(PermanentError):
        simulated("sms")(_n({"name": "G", "simulate": "permanent_fail"}))


def test_unknown_hint_is_permanent_so_typos_are_visible():
    with pytest.raises(PermanentError):
        simulated("sms")(_n({"name": "G", "simulate": "transient"}))


def test_unknown_template_is_permanent_even_when_simulated():
    with pytest.raises(PermanentError):
        simulated("sms")(_n({"name": "G"}, type_="no_such_type"))


def test_every_channel_has_a_sender_registered():
    assert set(worker.SENDERS) == {"email", "sms", "push"}
