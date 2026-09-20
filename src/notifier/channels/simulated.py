"""Simulated delivery (ADR-0004): everything real except the provider call.

Renders the template (so an unknown type is a PermanentError, same as a real
channel), then logs `would deliver via <channel>` instead of sending. A
`simulate` hint in the payload injects a fault so the retry/DLQ path (ADR-0003)
can be drilled end-to-end without touching a real provider:

    "simulate": "transient_fail"  -> raise TransientError
    "simulate": "permanent_fail"  -> raise PermanentError
    anything else                 -> raise PermanentError (a typo must be visible)
    no hint                       -> deliver normally
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from ..models import Notification, PermanentError, TransientError
from ..templates import render

logger = logging.getLogger(__name__)


def simulated(channel: str) -> Callable[[Notification, str], None]:
    """Build the sender for one channel. Returns a `(Notification, address) -> None`.

    `channel` is captured by the closure so the log line can name it. `address` is
    resolved by deliver() from the prefs row and passed in, so senders never touch
    the store.
    """

    def send(notification: Notification, address: str) -> None:
        """Simulate one delivery. Steps, in this order:

        1. render(notification.type, notification.payload) -> (subject, body).
           An unknown type raises PermanentError from render() — let it propagate.
        2. Log the line. Before the hint check on purpose: a transient_fail drill
           should still show it in CloudWatch on every retry. `user_id` rather than
           the address, matching channels/ses.py — a raw phone number in a log
           store is PII, and prefs holds the address if you need it.
        3. hint = notification.payload.get("simulate"):
             None              -> return (delivered)
             "transient_fail"  -> raise TransientError
             "permanent_fail"  -> raise PermanentError
             anything else     -> raise PermanentError naming the bad value
        """
        subject, _ = render(notification.type, notification.payload)
        logger.info(
            "would deliver via %s for user %s: %s",
            channel,
            notification.user_id,
            subject,
        )
        hint = notification.payload.get("simulate")
        if hint is None:
            return  # delivered
        if hint == "transient_fail":
            raise TransientError("simulated transient failure")
        if hint == "permanent_fail":
            raise PermanentError("simulated permanent failure")
        raise PermanentError(f"unknown simulate hint: {hint}")

    return send
