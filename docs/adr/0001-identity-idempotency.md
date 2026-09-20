# ADR-0001 — Identity & idempotency: client key + per-(notification, channel) dedup

**Status:** Accepted (grilled 2026-09-14)

## Context
"At-least-once + dedup = effectively-once" is only as good as the key we dedup on. Two
duplicate sources exist: SQS redelivery *inside* the pipeline, and — far more common — a
caller retrying a `POST` whose response timed out.

## Decision
- The caller supplies a **required `idempotency_key`** naming the *intent*. A retried POST
  carries the same key and collapses to one notification.
- `notification_id` is derived from it **scoped to the user** (`{user_id}#{idempotency_key}`,
  or a hash of that) so two callers' keys can't collide.
- Workers dedup on **(notification_id, channel)** via a DynamoDB conditional write
  (`attribute_not_exists`), so each channel's delivery is tracked independently.

## Consequences
- End-to-end retry safety: the duplicate is caught even when it was born before any id existed.
- A failed SMS retries even though the email for the same notification delivered.
- The API contract requires an idempotency key (a deliberate burden on callers).

Refined by [ADR-0008](0008-in-flight-is-not-skippable.md): the conditional write has three
outcomes, not two, and "another worker holds it" must not be treated as "already done".

## Rejected
- **Server-generated ids** — blind to client retries: a timed-out-and-retried POST mints two
  ids and double-sends.
- **Per-notification dedup** — a delivered email masks a crashed SMS; the retry sees "already
  delivered" and the SMS is dropped forever.

Same "deterministic id for dedup" lesson as FleetTracker ADR-0004, now with a channel dimension.
