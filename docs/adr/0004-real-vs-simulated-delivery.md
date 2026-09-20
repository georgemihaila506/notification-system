# ADR-0004 — Real vs simulated delivery: email real (SES); SMS + push simulated

**Status:** Accepted (grilled 2026-09-14)

## Decision
- **Email is real** via SES in its **sandbox** (sends only to verified addresses — George's own
  inbox; 200/day, 1/sec). Real deliveries, no risk of spamming anyone else; proves the
  provider integration.
- **SMS and push are simulated.** Workers log `would deliver via <channel>` and honor a payload
  fault-injection hint — `simulate: "transient_fail" | "permanent_fail"` — making them the
  **deterministic test rig** for ADR-0003's retry/DLQ drills, which cannot safely be run against a
  real provider.

A simulated channel still exercises 100% of the pipeline (fan-out, filter, queue, dedup, retry,
DLQ); only the final send is stubbed.

## Corrections (2026-09-20, from the M4 live drill)
- **"200/day free" was wrong.** 200/day is the sandbox *quota* — a cap on how many may be sent,
  not a free allowance. SES bills **$0.10 per 1,000** outbound from the first email, and this
  account shows no SES free-tier entry (the old 62k/month allowance is tied to EC2-hosted
  sending). The cost is still negligible; the claim was not.
- **The cost of "$0" was not the only optimism.** A real send from
  `george.mihaila506@gmail.com` reached Gmail and was **filed as spam**. gmail.com's SPF
  (`redirect=_spf.google.com`) does not authorise Amazon SES and DKIM cannot be enabled for a
  domain we do not control, so the mail is unauthenticated and indistinguishable from spoofing
  (DMARC `p=none`, hence accepted-then-filtered, 0 bounces). **Sandbox email proves the
  integration but cannot demonstrate deliverability.** Doing that needs a domain identity we
  control (`gemihaila.com`) with Easy DKIM — deliberately out of scope here, noted so the
  limitation is not rediscovered.

## Rejected
- **Real SMS now** — the first per-use cost in the project, multiplied by every retry drill, plus
  SNS SMS sandbox setup. A fine *later* upgrade to George's own verified number.
- **Everything simulated** — never touches SES, forfeiting a core reason for choosing ch. 10.
- **Real push** — infeasible regardless (Apple/Google accounts + a receiving app).
