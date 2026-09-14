"""Minimal templates: type -> (subject, body), rendered with str.format over the payload.

An unknown type is a PERMANENT failure — no retry will ever make it renderable.
"""

from __future__ import annotations

from .models import PermanentError

TEMPLATES: dict[str, tuple[str, str]] = {
    "welcome": ("Welcome, {name}!", "Hi {name}, thanks for joining."),
    "order_shipped": ("Your order {order_id} shipped", "Order {order_id} is on its way."),
}


def render(type_: str, payload: dict) -> tuple[str, str]:
    try:
        subject, body = TEMPLATES[type_]
        return subject.format(**payload), body.format(**payload)
    except KeyError as err:  # unknown type, or a missing payload field
        raise PermanentError(f"cannot render {type_!r}: missing {err}") from err
