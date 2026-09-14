# ADR-0007 — Trigger: `POST /notifications` API; EventBridge as an optional stretch

**Status:** Accepted (grilled 2026-09-14)

## Decision
An API Gateway endpoint is the simplest, directly testable trigger and mirrors the shortener.
**EventBridge** can be added later as a rule routing bus events into the *same* ingest Lambda —
an isolated add-on that doesn't disturb the pipeline.

## Rejected (for now)
EventBridge from the start — more genuinely event-driven and a new service, but an extra layer,
and you'd still need a way to publish events onto the bus just to test.

## Also decided (not grilled)
Templates: a minimal `type → (subject, body)` map rendered with `str.format` over the payload.
No templating engine.
