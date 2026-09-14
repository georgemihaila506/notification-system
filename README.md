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

Design hardened via a grilling session (ADR-0001…0007). **M0** in progress.
