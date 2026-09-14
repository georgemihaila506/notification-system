"""Domain model: a Notification, its identity, and failure classification.

Two ideas live here (ADR-0001, ADR-0003):
  * A notification's identity comes from the CALLER's idempotency key, scoped to the
    user — so a retried request collapses to the same id.
  * A delivery failure is either transient (retry may help) or permanent (it never
    will). Workers raise the right one and the pipeline treats them differently.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

CHANNELS = ("email", "sms", "push")


class TransientError(Exception):
    """Delivery failed in a way that MAY succeed on retry (timeout, provider 5xx).
    Raise it out of the worker → the message is NOT deleted → SQS redelivers."""


class PermanentError(Exception):
    """Delivery can never succeed (invalid address, opted out, unknown template).
    The worker records `failed` and succeeds — never retried (ADR-0003)."""


@dataclass(frozen=True)
class Notification:
    user_id: str
    type: str
    payload: dict
    idempotency_key: str

    @property
    def notification_id(self) -> str:
        """Deterministic identity, scoped to the user (ADR-0001).

        The same (user_id, idempotency_key) always yields the same id — so a retried
        POST collapses to one notification — while two users sharing a key never
        collide. Hashing the user-scoped string gives a clean fixed-length key.
        """
        return hashlib.sha256(f"{self.user_id}#{self.idempotency_key}".encode()).hexdigest()

    def to_wire(self) -> str:
        """JSON for the SNS message body."""
        return json.dumps(
            {
                "user_id": self.user_id,
                "type": self.type,
                "payload": self.payload,
                "idempotency_key": self.idempotency_key,
            }
        )

    @classmethod
    def from_wire(cls, raw: str) -> "Notification":
        d = json.loads(raw)
        return cls(d["user_id"], d["type"], d["payload"], d["idempotency_key"])
