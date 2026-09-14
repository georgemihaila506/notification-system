# ADR-0006 — Per-user rate limiting is a LABELED learning exercise (built late, M6)

**Status:** Accepted (grilled 2026-09-14)

## Decision
A per-user DynamoDB counter / token bucket (atomic `ADD`) enforced at ingest (reject with 429),
verified with a synthetic burst.

## Honesty
At personal scale nobody gets spammed, so this solves no real problem — the same YAGNI knife that
cut Snowflake and labeled the shortener's cache. It is kept because "don't spam a user" is a
genuine notification-system failure mode and the primitive is transferable.
