# ADR-0006 — Per-user rate limiting is a LABELED learning exercise (built late, M6)

**Status:** Accepted (grilled 2026-09-14) · Built and drilled 2026-09-22

## Decision
A per-user DynamoDB counter / token bucket (atomic `ADD`) enforced at ingest (reject with 429),
verified with a synthetic burst.

## Refined (2026-09-22, before building)
- **Fixed window, not a token bucket.** One counter per user per UTC hour, incremented with
  a single atomic `ADD` that returns the new value; over the limit → 429. One round trip, no
  read-modify-write, no race. The boundary burst is accepted and documented: ten at 17:59 plus
  ten at 18:01 is twenty inside two minutes. A token bucket fixes that but trades the atomic
  primitive — the thing this exercise exists to teach — for a conditional write and a retry
  loop, to smooth bursts for a user who does not exist.
- **Counters live in their own table, not in `prefs`.** DynamoDB TTL is a *table-wide*
  setting, so expiring counters from the prefs table would arm automatic deletion on durable
  user data — one stray `expires_at` on a real prefs row and it silently vanishes. A separate
  table also removes the `{user_id}#rate#{window}` key-collision surface, and an idle
  `PAY_PER_REQUEST` table costs nothing.
- **Counter rows expire.** Every row carries `expires_at`; without it each user accumulates
  one row per window forever.

## What the drill found (2026-09-22)
30 concurrent POSTs returned 20×202 and 10×429 with `202 + 429 == n` exactly — the atomic
`ADD` loses no increments under concurrency, which is the claim worth testing.

The first attempt returned 10×202 and 20×**503**. Those never reached this code: the AWS
account's Lambda concurrent-execution limit is 10, so API Gateway surfaced Lambda throttling
as a 503. Three limiters exist here — account concurrency (10), API Gateway stage throttling
(unconfigured), and this per-user counter (20/hour) — and the lowest bound won. A limit you
reason about carefully is not necessarily the one that fires.

## Honesty
At personal scale nobody gets spammed, so this solves no real problem — the same YAGNI knife that
cut Snowflake and labeled the shortener's cache. It is kept because "don't spam a user" is a
genuine notification-system failure mode and the primitive is transferable.
