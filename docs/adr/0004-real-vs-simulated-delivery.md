# ADR-0004 — Real vs simulated delivery: email real (SES); SMS + push simulated

**Status:** Accepted (grilled 2026-09-14)

## Decision
- **Email is real** via SES in its **sandbox** (sends only to verified addresses — George's own
  inbox; 200/day free). Real deliveries, $0, zero spam risk; proves the provider integration.
- **SMS and push are simulated.** Workers log `would deliver via <channel>` and honor a payload
  fault-injection hint — `simulate: "transient_fail" | "permanent_fail"` — making them the
  **deterministic test rig** for ADR-0003's retry/DLQ drills, which cannot safely be run against a
  real provider.

A simulated channel still exercises 100% of the pipeline (fan-out, filter, queue, dedup, retry,
DLQ); only the final send is stubbed.

## Rejected
- **Real SMS now** — the first per-use cost in the project, multiplied by every retry drill, plus
  SNS SMS sandbox setup. A fine *later* upgrade to George's own verified number.
- **Everything simulated** — never touches SES, forfeiting a core reason for choosing ch. 10.
- **Real push** — infeasible regardless (Apple/Google accounts + a receiving app).
