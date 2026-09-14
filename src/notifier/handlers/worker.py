"""The per-message delivery loop shared by every channel worker (M1 core; wired to
SQS in M3).

`deliver` is deliberately AWS-agnostic: it takes a `store` and a `send` callable, so
the whole reliability story — dedup, opt-out re-check, transient-vs-permanent
handling — is testable with fakes and no queue. The SQS-triggered Lambda handlers
(M3) are thin wrappers that parse the record and call this.
"""

from __future__ import annotations

from collections.abc import Callable

from ..db import Store
from ..models import Notification, PermanentError

# A channel's send function: given the notification, deliver it or raise
# TransientError / PermanentError.
Sender = Callable[[Notification], None]


def deliver(
    store: Store, channel: str, notification: Notification, send: Sender
) -> str:
    """Deliver one notification on one channel, effectively once.

    Returns "delivered" | "skipped" | "failed".

    * Dedup (ADR-0001): a row already marked `delivered` means this is a redelivery of
      finished work → skip. No row → claim it with a conditional put. A `pending` row is
      an earlier attempt that crashed mid-flight → retry it (skipping only *completed*
      work is what keeps a crash-after-claim from losing the notification).
    * Opt-out re-check (ADR-0005) happens right before sending, inside the same
      `try` as the send, so it's recorded as a permanent failure like any other.
    * Classification (ADR-0003): a PermanentError is recorded as `failed` and swallowed —
      never retried. A TransientError deliberately propagates: raising out of the
      worker leaves the SQS message undeleted, which is exactly what makes it retry.
    """
    nid = notification.notification_id
    existing = store.get_delivery(nid, channel)
    if existing and existing["status"] == "delivered":
        return "skipped"
    if not existing:
        store.put_delivery_if_absent(nid, channel)

    try:
        prefs = store.get_prefs(notification.user_id)
        if not prefs or channel not in prefs["channels"]:
            raise PermanentError("opted out")
        send(notification)
    except PermanentError:
        store.mark_delivery(nid, channel, "failed")
        return "failed"

    store.mark_delivery(nid, channel, "delivered")
    return "delivered"
