# ADR-0002 — Topology: SNS topic → per-channel SQS queues, routed by filter policies

**Status:** Accepted (grilled 2026-09-14)

## Decision
Ingest publishes **once** to an SNS topic, stamping the user's allowed channels as a message
attribute. Each channel has its own SQS queue subscribed with a **filter policy**
(`channel ∋ "<this channel>"`), so SNS delivers only to the queues that should receive it.

## Consequences
- **Bulkheads:** each queue has its own visibility timeout, DLQ, and concurrency — a failing or
  slow SMS provider cannot stall email.
- Routing lives in configuration, not in worker code that discards misrouted messages.
- Ingest is decoupled from the channel list: adding a channel = subscribing a new queue.

## Rejected
- **Single queue, worker dispatches by channel** — one channel's backlog becomes everyone's
  outage; one retry policy / DLQ / concurrency for all.
- **Ingest writes directly to per-channel queues** — keeps isolation but couples ingest to the
  channel list and forfeits the SNS→SQS fan-out lesson.

FleetTracker's "one publish, N subscribers" as AWS primitives.
