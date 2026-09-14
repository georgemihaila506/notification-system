# ADR-0005 — Opt-out enforced at ingest AND re-checked by the worker

**Status:** Accepted (grilled 2026-09-14)

## Context
Ingest reads preferences and routes via the SNS filter (ADR-0002). But preferences can change
*after* enqueue: a user opts out of SMS while their message already sits in the SMS queue.

## Decision
The worker **re-reads preferences immediately before delivering** and treats "opted out" as a
*permanent* failure (ADR-0003 path: record, don't retry). One extra `GetItem` closes the
enqueue→delivery race. Defense in depth.

## Rejected
Ingest-only enforcement — simpler, but leaves the race window open.
