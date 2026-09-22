# The inbound half (ADR-0009): SES tells us what actually happened to a message.
#
#   send (ConfigurationSetName) -> SES -> configuration set
#     -> event destination -> SNS topic -> SQS queue -> events Lambda -> ledger
#
# Its own topic, queue and DLQ rather than reusing the delivery ones: same
# bulkhead reasoning as ADR-0002, a failing event consumer must not stall sending.

# why sesv2 and not the v1 resources: DELIVERY_DELAY does not exist in the v1
# event-destination API (its matching_types accepts only send/reject/bounce/
# complaint/delivery/open/click/renderingFailure). A configuration set is one
# underlying resource regardless of which API creates it, so the v1 SES client in
# channels/ses.py sends against this happily.
resource "aws_sesv2_configuration_set" "notify" {
  configuration_set_name = "${var.project}-events"
}

# why: without a configuration set named on the send, SES reports nothing at all
# after accepting a message. These four are the outcomes we record; SES can also
# publish SEND/OPEN/CLICK/RENDERING_FAILURE, which the worker ignores by design.
resource "aws_sesv2_configuration_set_event_destination" "sns" {
  configuration_set_name = aws_sesv2_configuration_set.notify.configuration_set_name
  event_destination_name = "${var.project}-events-to-sns"

  event_destination {
    enabled              = true
    matching_event_types = ["DELIVERY", "BOUNCE", "COMPLAINT", "DELIVERY_DELAY"]

    sns_destination {
      topic_arn = aws_sns_topic.ses_events.arn
    }
  }
}

resource "aws_sns_topic" "ses_events" {
  name = "${var.project}-ses-events"
}

resource "aws_sqs_queue" "ses_events_dlq" {
  name                      = "${var.project}-ses-events-dlq"
  message_retention_seconds = 10 * 24 * 60 * 60
}

resource "aws_sqs_queue" "ses_events" {
  name = "${var.project}-ses-events"

  # why: same rule as the delivery queues (ADR-0003 §2) — a message can only
  # reappear once the worker that held it is provably dead.
  visibility_timeout_seconds = local.events_timeout_s * 6

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ses_events_dlq.arn
    maxReceiveCount     = 3
  })
}

# why: SNS is a service principal, not one of our IAM roles, so the queue must
# admit it explicitly — and only from this topic, or any topic in any account
# could push into it.
data "aws_iam_policy_document" "ses_events_to_sqs" {
  statement {
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.ses_events.arn]

    principals {
      type        = "Service"
      identifiers = ["sns.amazonaws.com"]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_sns_topic.ses_events.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "ses_events" {
  queue_url = aws_sqs_queue.ses_events.id
  policy    = data.aws_iam_policy_document.ses_events_to_sqs.json
}

# No filter policy here, unlike the delivery subscriptions: one consumer wants
# every event, and the worker decides which types are interesting.
resource "aws_sns_topic_subscription" "ses_events" {
  topic_arn = aws_sns_topic.ses_events.arn
  protocol  = "sqs"
  endpoint  = aws_sqs_queue.ses_events.arn
}

resource "aws_lambda_function" "events" {
  function_name    = "${var.project}-events"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "notifier.handlers.events.handler"
  filename         = data.archive_file.app.output_path
  source_code_hash = data.archive_file.app.output_base64sha256
  memory_size      = 256
  timeout          = local.events_timeout_s

  logging_config {
    log_format            = "JSON"
    application_log_level = "INFO"
    system_log_level      = "WARN"
  }

  environment {
    variables = {
      PREFS_TABLE      = aws_dynamodb_table.prefs.name
      DELIVERIES_TABLE = aws_dynamodb_table.deliveries.name
    }
  }
}

resource "aws_lambda_event_source_mapping" "events" {
  event_source_arn        = aws_sqs_queue.ses_events.arn
  function_name           = aws_lambda_function.events.arn
  batch_size              = 1
  function_response_types = ["ReportBatchItemFailures"]
}

# why: ADR-0003 §4 again. A DLQ nobody watches is silent data loss — here the loss
# is knowledge of what happened to a notification, not the notification itself.
resource "aws_cloudwatch_metric_alarm" "ses_events_dlq_not_empty" {
  alarm_name        = "${var.project}-ses-events-dlq-not-empty"
  alarm_description = "SES delivery events are failing to process. Inspect and redrive."

  namespace   = "AWS/SQS"
  metric_name = "ApproximateNumberOfMessagesVisible"
  dimensions  = { QueueName = aws_sqs_queue.ses_events_dlq.name }

  statistic           = "Maximum"
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"

  # why: SQS publishes queue metrics minutes late; at period 60 the window being
  # evaluated is always the one whose datapoint has not landed, and the alarm
  # never fires. Learned the hard way on 2026-09-20.
  period             = 300
  treat_missing_data = "notBreaching"
}
