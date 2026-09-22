# ADR-0009 — Delivery feedback: SES events back into the ledger, correlated by message tag

**Status:** Accepted (grilled 2026-09-22)

## Context
The M4 drill exposed a lie. `deliver()` writes `status=delivered` when `send_email` returns a
`MessageId` — but that only means SES accepted custody. The message it called delivered was
filed as spam by Gmail (ADR-0004, Corrections). Nothing in the system can distinguish "handed
over" from "arrived", and a bounce or a spam complaint is invisible entirely.

Real outcomes arrive **asynchronously**: an SES *configuration set* with an *event destination*
publishes `Delivery`, `Bounce`, `Complaint` and `DeliveryDelay` events after the send. This is a
second, inbound pipeline mirroring the outbound one.

## Mechanics
Sends carry `ConfigurationSetName`; the configuration set's event destination publishes to an
SNS topic; an SQS queue subscribes; a worker consumes. The event payload identifies the message
by `mail.messageId` — SES's id, not ours. Our `notification_id` appears only if we put it there.

## Decision
1. **Correlate with SES message tags.** Sends pass `Tags=[{notification_id}, {channel}]`, and SES
   echoes them back under `mail.tags` on every event. The event arrives already carrying the
   ledger key: no GSI, no lookup table, no extra write. A sha256 hex id fits inside the tag
   charset (alphanumerics, `-`, `_`).
2. **Two attributes, not one status.** The sending worker owns `status`
   (`pending` → `sent` | `failed`); the event worker owns `provider_status`
   (`delivered` | `bounced` | `complained` | `delayed`) plus detail such as `bounce_type`.
   Different attributes cannot clobber each other, so the send write and the event write need no
   ordering guarantee and no conditional transitions. `delivered` finally means delivered.
3. **Suppress on permanent bounce and on any complaint** by removing that channel from the
   user's prefs row. ADR-0005 already re-checks prefs at ingest *and* immediately before each
   send, so suppression needs **no new enforcement path** — the existing checks stop every future
   send at both layers. Transient and Undetermined bounces are recorded only.
4. **A transient bounce is not retried.** By the time the event arrives the SQS message is long
   deleted; retrying would mean re-injecting into the pipeline, which is a separate feature.
5. **Its own topic, queue and DLQ.** The event pipeline is isolated from the delivery pipeline
   for the same bulkhead reason as ADR-0002: a failing event consumer must not stall sending.

## Consequences
- `status=delivered` disappears from the sending path. `sent` is the honest terminal state for
  "we handed it over"; confirmation is a separate fact, and its absence is now visible rather
  than assumed.
- Reading "what happened" means reading two fields, with `provider_status` taking precedence
  when present. A row with `status=sent` and no `provider_status` is genuinely unknown — which
  is the truth, not a gap.
- **Auto-suppression is currently irreversible.** Nothing re-enables a channel that a bounce or
  complaint disabled, and one complaint is enough. Accepted deliberately: protecting sender
  reputation outranks convenience at this scale, and a manual `put_prefs` restores it. A
  self-service re-enable is a real product feature this project is not building.
- Events for a notification whose row does not exist (a send that predates the ledger, or a
  manual `aws ses send-email`) must not crash the worker — the update is best-effort.

## Rejected
- **Storing the SES MessageId on the row and indexing it** — needs a DynamoDB GSI or a second
  lookup table plus an extra write per send, to rebuild an association SES hands back for free.
- **One status with a forward-only state machine** — truer as a model, but every write then needs
  a `ConditionExpression` to stop a late `sent` overwriting an already-arrived `delivered`. The
  race is real (SES can publish `Delivery` before the sending worker finishes its write); two
  attributes make it structurally impossible instead of merely handled.
- **Recording events without acting on them** — leaves the system sending to dead addresses and
  to people who reported it as spam, which is what gets an SES account paused
  (`AccountSendingPausedException`, already in ADR-0003's classification table).

Closes the loop ADR-0004 left open: sandbox email proves the integration, and only the provider
can tell you whether anything arrived. See also [ADR-0005](0005-opt-out-enforcement.md), whose
double enforcement is what makes suppression free.
