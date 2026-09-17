# ADR-0003 §4: a DLQ nobody watches is silent data loss. One alarm per DLQ that
# trips as soon as a single message is sitting in it.

resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  for_each = local.channels

  alarm_name        = "${var.project}-${each.key}-dlq-not-empty"
  alarm_description = "A ${each.key} notification exhausted its retries. Inspect and redrive."

  namespace   = "AWS/SQS"
  metric_name = "ApproximateNumberOfMessagesVisible"
  dimensions  = { QueueName = aws_sqs_queue.dlq[each.key].name }

  # why: threshold 0 with GreaterThan — ONE dead message is already a problem, not
  # a trend. Maximum over the period so a message that arrives and is redriven
  # within the same minute still registers.
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"

  # why: SQS only emits metrics while a queue is active. An idle DLQ reports
  # nothing, and "nothing" must read as healthy, not as INSUFFICIENT_DATA noise.
  treat_missing_data = "notBreaching"

  # No actions yet: the alarm state is visible in the CloudWatch console, which
  # is enough while drilling by hand. An SNS email action is a one-line add later.
}
