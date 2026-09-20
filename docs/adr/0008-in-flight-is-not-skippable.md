# ADR-0008 — "Another worker has it" must never mean "delete it"

**Status:** Accepted (written from an incident, 2026-09-20)

## Context
ADR-0001 dedups on a conditional write per `(notification, channel)`. The first implementation
collapsed every non-delivering outcome into one result, `skipped`, and the SQS handler deleted
the message for all of them. That conflated two opposite situations:

* the row says `delivered`/`failed` — the work is **finished**, deleting is correct;
* the row is `pending` and young, or our conditional claim **lost** to another worker — the work
  is **in progress elsewhere**, and deleting bets that the other worker survives.

A live drill lost a notification to that bet, though not in the way expected. The staleness
threshold was set equal to the queue's visibility timeout (60s), so a legitimate SQS retry —
which arrives at *roughly* the visibility timeout, measured at 58s — always looked too young to
be dead. Every retry was judged "in flight", returned `skipped`, and was deleted unprocessed.
The delivery row is still `pending` and nothing will ever retry it.

## Decision
- `deliver()` returns a distinct **`in_flight`** for anything another worker provably holds: a
  lost conditional put, a lost re-claim, or a `pending` row younger than the threshold. The
  handler reports those in `batchItemFailures`, so SQS **keeps** the message.
- The staleness threshold sits **strictly between** the worker's Lambda timeout (a worker cannot
  outlive its own timeout, so an older row is provably dead) and the queue's visibility timeout
  (a retry lands at that mark and must be allowed through). Terraform derives it as
  `3 × worker_timeout` and passes it as `STALE_AFTER_S`; the queue's own timeout is `6 ×`.

## Consequences
- Being wrong about "someone else has it" now costs one redelivery cycle instead of the
  notification. The failure mode is bounded and self-healing: on the next receive the row is
  either `delivered` (skip and delete) or stale (reclaim and retry).
- Two config numbers are coupled to a third. The bound is written down in code, in Terraform, and
  here, because it is not recoverable by reading any one of them.

## Rejected
- **Keeping one `skipped`** — indistinguishable outcomes cannot get different handling, and the
  safe handling differs.
- **Never skipping at all** — a concurrent duplicate would then double-send, which is the
  guarantee ADR-0001 exists to provide.

## Note
A suite of 37 passing tests did not catch this; a three-minute drill against real AWS did. Both
bugs were in the interaction with AWS's *timing* — a retry at 58s, and metrics published minutes
late (ADR-0003 §4's alarm had the same class of bug). Unit tests verify the logic written; only a
drill verifies the assumptions about the platform. See also [ADR-0001](0001-identity-idempotency.md)
and [ADR-0003](0003-retry-dlq.md).
