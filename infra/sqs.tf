# Per-channel queues (ADR-0002 bulkheads) with the retry/DLQ settings of ADR-0003.
# Each channel gets its own queue, DLQ and redrive policy, so a failing SMS
# provider cannot stall email.

locals {
  channels = toset(["email", "sms", "push"])

  # Worker Lambda timeout — the queue settings below are derived from it.
  # why: cold start ~3s + a real SES send; 5s would kill healthy work and burn an attempt.
  worker_timeout_s = 10

  # The SES events worker (ADR-0009) does no provider call at all — two DynamoDB
  # writes at most — so it needs less headroom than a sending worker.
  events_timeout_s = 10
}

resource "aws_sqs_queue" "dlq" {
  for_each = local.channels
  name     = "${var.project}-${each.key}-dlq"

  # How long a dead message stays around for redrive before SQS discards it.
  # why: redrive is manual; must survive a week of not looking.
  message_retention_seconds = 10 * 24 * 60 * 60
}

resource "aws_sqs_queue" "channel" {
  for_each = local.channels
  name     = "${var.project}-${each.key}"

  # why: ADR-0003 §2 — a Lambda cannot outlive its timeout, so a message reappearing
  # can ONLY mean the previous worker is dead. 1x would duplicate slow-but-alive sends.
  visibility_timeout_seconds = local.worker_timeout_s * 6

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq[each.key].arn
    # why: ADR-0003 low end. Time-to-DLQ = 3 x 60s = 3 min — short enough to watch in a drill.
    maxReceiveCount = 3
  })
}
