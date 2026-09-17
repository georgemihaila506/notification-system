# SNS → per-channel SQS routing (ADR-0002). Three resources per channel:
#   1. a queue policy letting the topic (and only the topic) send into the queue
#   2. a subscription  topic → queue
#   3. a filter policy on that subscription so only matching channels are delivered

# 1. The queue's resource policy. SNS is a service principal outside our IAM
#    roles, so the queue must explicitly let it in.
data "aws_iam_policy_document" "sns_to_sqs" {
  for_each = local.channels

  statement {
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.channel[each.key].arn]

    principals {
      type        = "Service"
      identifiers = ["sns.amazonaws.com"]
    }

    # why: without this, ANY SNS topic in ANY account could push into our queue.
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_sns_topic.notifications.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "sns_to_sqs" {
  for_each  = local.channels
  queue_url = aws_sqs_queue.channel[each.key].id
  policy    = data.aws_iam_policy_document.sns_to_sqs[each.key].json
}

# 2 + 3. Subscribe each queue and filter on the `channels` message attribute that
#    ingest stamps on every publish (handlers/ingest.py).
resource "aws_sns_topic_subscription" "channel" {
  for_each  = local.channels
  topic_arn = aws_sns_topic.notifications.arn
  protocol  = "sqs"
  endpoint  = aws_sqs_queue.channel[each.key].arn

  filter_policy = jsonencode({
    channels = [each.key]
  })
}
