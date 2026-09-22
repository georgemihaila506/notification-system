# Notification System on AWS

A learning build of *System Design Interview* ch. 10 on serverless AWS: **API Gateway → Lambda
→ SNS → per-channel SQS → Lambda workers → SES / simulated delivery**, with DynamoDB for
preferences and delivery dedup, all in Terraform, deployed by GitHub Actions via OIDC.

Follow-on to the URL shortener (ch. 8): the same skeleton, now learning the async half of AWS —
queues, fan-out, at-least-once delivery, dead-letter queues, and real email.

## The one idea

A notification's identity is a **caller-supplied idempotency key**, and delivery is deduped
**per (notification, channel)** — so retries anywhere (a timed-out POST, an SQS redelivery, a
crashed worker) collapse to exactly one send per channel. Everything else — per-channel queues
as bulkheads, transient-vs-permanent failure classification, DLQs — protects that guarantee.
The *why* for each choice is in [`docs/adr/`](docs/adr/).

## Architecture

One publish fans out to a queue per channel; each queue is an isolated bulkhead with its
own retries and dead-letter queue (ADR-0002, ADR-0003).

```mermaid
flowchart LR
    C(["Caller<br/>POST /notifications<br/>{user_id, type, payload,<br/>idempotency_key}"]) --> GW["API Gateway"]
    GW --> IN["Lambda: ingest<br/>validate · derive notification_id ·<br/>load prefs · rate limit"]
    IN -- "GetItem" --> P[("DynamoDB<br/>prefs")]
    IN -- "publish once<br/>(channels attribute)" --> SNS{{"SNS topic"}}
    SNS -- "filter: email" --> QE["SQS email"]
    SNS -- "filter: sms" --> QS["SQS sms"]
    SNS -- "filter: push" --> QP["SQS push"]
    QE --> WE["worker (email)"]
    QS --> WS["worker (sms)"]
    QP --> WP["worker (push)"]
    WE -- "dedup + status" --> D[("DynamoDB<br/>deliveries")]
    WS -- "dedup + status" --> D
    WP -- "dedup + status" --> D
    WE -- "SendEmail (real)" --> SES["SES → inbox"]
    WS -- "simulated" --> LS["log"]
    WP -- "simulated" --> LP["log"]
    QE -. "after N failures" .-> DE["DLQ email"]
    QS -. "after N failures" .-> DS["DLQ sms"]
    QP -. "after N failures" .-> DP["DLQ push"]
    DE & DS & DP -. "alarm" .-> CW["CloudWatch"]
```

## Activity — one message through a worker

The reliability logic (ADR-0001 dedup, ADR-0005 opt-out re-check, ADR-0003 failure
classification). Raising a transient error is deliberate: it leaves the SQS message
undeleted, which is exactly what makes it retry.

```mermaid
flowchart TD
    A(["SQS delivers message"]) --> B["parse Notification<br/>nid = notification_id"]
    B --> C{"deliveries row for<br/>(nid, channel)?"}
    C -- "status = delivered" --> S["return skipped<br/>(dedup — already done)"]
    C -- "none" --> CL["claim: conditional put<br/>status = pending"]
    C -- "pending<br/>(earlier attempt crashed)" --> R["retry"]
    CL --> R
    R --> PR{"re-read prefs:<br/>channel allowed?"}
    PR -- "no (opted out)" --> F["mark failed<br/>return failed — never retried"]
    PR -- yes --> SND["send(notification)"]
    SND -- "PermanentError<br/>(bad address, bad template)" --> F
    SND -- "TransientError<br/>(timeout, 5xx)" --> T["raise → message NOT deleted<br/>SQS redelivers after<br/>visibility timeout"]
    T -. "maxReceiveCount<br/>exceeded" .-> DLQ["DLQ + alarm"]
    SND -- ok --> OK["mark delivered<br/>return delivered"]
```

## Layout

```
src/notifier/     models  db  templates  channels/  handlers/   # stdlib + boto3 only
infra/            Terraform (bootstrap/ = state + CI role, kept apart from the app)
tests/            pytest + moto (DynamoDB, SNS, SQS, SES) — no real AWS
docs/             adr/ 0001-0007  glossary.md
```

## Setup (local)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

## Status

Design hardened via a grilling session (ADR-0001…0007). Done: **M0** (scaffold, bootstrap,
CI, SES identity; ingest stub live), **M1** (domain core + delivery loop, moto-tested),
**M2** (ingest live → SNS), **M3** (per-channel SQS queues + DLQs, worker Lambdas with
partial-batch failure reporting, simulated senders with fault injection, DLQ alarms),
**M4** (per-channel destination addresses; real email via SES with failure classification).

**M5** (delivery feedback: SES events → ledger → automatic suppression) and **M6** (per-user
hourly rate limiting) complete the build.

Every milestone from M3 on was drilled against real AWS, not just moto, and the drills are
where the interesting failures were:

* **M3** — two bugs a green test suite had missed, both in assumptions about AWS *timing*
  rather than in logic: a staleness threshold equal to the visibility timeout deleted a
  notification unprocessed, and the DLQ alarm never fired because SQS publishes metrics
  minutes later than the alarm's window ([ADR-0008](docs/adr/0008-in-flight-is-not-skippable.md)).
* **M4** — a real email arrived, in spam. Mail cannot be authenticated for a domain you do
  not control ([ADR-0004](docs/adr/0004-real-vs-simulated-delivery.md)).
* **M5** — worked first time: a simulated bounce suppressed the channel, and the next request
  routed around it with no new enforcement code, because ADR-0005 already re-checks
  preferences twice ([ADR-0009](docs/adr/0009-delivery-events.md)).
* **M6** — the burst tripped the *account's* Lambda concurrency limit (10) long before the
  application limit engaged. The limit you reason about is rarely the one that fires first.
