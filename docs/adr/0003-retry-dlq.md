# ADR-0003 — Retry & DLQ: classify failures; retry only transient; DLQ + alarm + redrive

**Status:** Accepted (grilled 2026-09-14)

## Mechanics
A Lambda-on-SQS worker receives a message (it becomes invisible). Success → Lambda deletes it.
Exception → it reappears after the *visibility timeout* and is retried. After `maxReceiveCount`
receives the redrive policy moves it to the DLQ.

## Decision — five deliberate settings, not one vague "N"
1. **Classify failures.** *Transient* (provider timeout / 5xx) → raise, let SQS retry
   (`maxReceiveCount` ≈ 3–5) → DLQ. *Permanent* (invalid address, opted-out) → record
   `status=failed` in the deliveries table and **succeed** (delete) — never retried.
2. **Visibility timeout ≥ 6× the Lambda timeout** so a slow-but-succeeding delivery isn't picked up
   concurrently by a second worker. Dedup would catch it; don't lean on the net for a config error.
3. **Partial-batch failure reporting** (`ReportBatchItemFailures`) so one bad message doesn't
   redrive its batch-mates. Batch size 1 is acceptable while learning.
4. **Alarm on DLQ depth** — a DLQ nobody watches is silent data loss.
5. **Native SQS redrive-to-source** after the underlying fault is fixed.

## Rejected
- **Retry everything uniformly** — burns retries and provider quota on permanent failures and
  delays the DLQ signal by `maxReceiveCount × visibility timeout`.
- **Retry then drop (no DLQ)** — failed notifications vanish with no record.
