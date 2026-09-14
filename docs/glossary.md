# Glossary — first principles

## AWS
- **SNS (Simple Notification Service)** — pub/sub topics: publish once, every subscriber gets a
  copy (fan-out). Also the service that can actually deliver SMS/push.
- **SNS filter policy** — a per-subscription rule on message attributes; SNS delivers a message
  to a subscriber only if it matches. Routing as configuration (ADR-0002).
- **SQS (Simple Queue Service)** — a durable queue. A worker *receives* a message (it becomes
  invisible), then *deletes* it on success. Not deleted → it reappears → retry. At-least-once.
- **Visibility timeout** — how long a received message stays hidden from other workers. Must
  exceed the worker's max runtime (rule of thumb: ≥ 6× the Lambda timeout) or a slow success
  gets processed twice (ADR-0003).
- **DLQ (dead-letter queue)** — where a message goes after `maxReceiveCount` failed receives
  (via a *redrive policy*). Only useful if alarmed and redrivable.
- **Redrive** — SQS's native "move messages from the DLQ back to the source queue" after a fault
  is fixed.
- **Partial batch response** (`ReportBatchItemFailures`) — a Lambda-on-SQS worker reports which
  messages in a batch failed, so only those are retried, not the whole batch.
- **SES (Simple Email Service)** — email delivery. **Sandbox** = can only send to *verified*
  identities, 200/day; perfect for a learning project (ADR-0004).
- **Bulkhead** — isolating failure domains so one failing component can't sink the others; here,
  one queue per channel.

## System design
- **Idempotency key** — a caller-supplied name for an *intent*, so a retry of the same intent
  collapses to one action (ADR-0001). Stripe-style.
- **At-least-once / effectively-once** — queues may redeliver; an idempotent consumer (dedup on
  a deterministic key) makes the observable result once. Same lesson as FleetTracker.
- **Transient vs permanent failure** — will retrying ever help? Timeouts: yes. "Invalid
  address": never. The worker must tell them apart (ADR-0003).
- **Fan-out** — one event delivered to N independent consumers (SNS → N queues).
