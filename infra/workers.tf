# One worker Lambda per channel, each fed by its own queue (ADR-0002 bulkheads).
# Same zip and role as ingest; only the handler and env differ.

resource "aws_lambda_function" "worker" {
  for_each = local.channels

  function_name    = "${var.project}-worker-${each.key}"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "notifier.handlers.worker.handler"
  filename         = data.archive_file.app.output_path
  source_code_hash = data.archive_file.app.output_base64sha256
  memory_size      = 256

  # why: the single source of truth for the timeout; sqs.tf derives the
  # visibility window from it, and VISIBILITY_TIMEOUT_S below hands the same
  # number to the code so worker and queue can never disagree.
  timeout = local.worker_timeout_s

  # why: logger.info() is invisible by default — Lambda's root logger starts at
  # WARNING, so the drill's "would deliver via <channel>" lines never reached
  # CloudWatch. Application log level only applies when the format is JSON.
  logging_config {
    log_format            = "JSON"
    application_log_level = "INFO"
    system_log_level      = "WARN"
  }

  environment {
    variables = {
      CHANNEL          = each.key
      PREFS_TABLE      = aws_dynamodb_table.prefs.name
      DELIVERIES_TABLE = aws_dynamodb_table.deliveries.name

      # why: how old a `pending` row must be before a worker treats it as a dead
      # attempt rather than one in flight. It MUST sit strictly between the worker
      # timeout (a Lambda cannot outlive it, so anything older is provably dead)
      # and the visibility timeout (an SQS retry arrives at ~that mark and has to
      # be allowed through). 3x worker timeout = 30s, mid-way between 10s and 60s.
      # Setting this equal to the visibility timeout deadlocks every retry.
      STALE_AFTER_S = local.worker_timeout_s * 3

      # why: the verified From identity. Set for every worker, not just email, so
      # the three functions keep one env shape — the simulated senders ignore it.
      SES_SOURCE = aws_ses_email_identity.notify.email
    }
  }
}

# The poller: "Lambda service, read this queue and invoke this function".
resource "aws_lambda_event_source_mapping" "worker" {
  for_each = local.channels

  event_source_arn = aws_sqs_queue.channel[each.key].arn
  function_name    = aws_lambda_function.worker[each.key].arn

  # why: ADR-0003 §3 — one message per invoke while learning, so a poison
  # message can never drag a batch-mate through its retries.
  batch_size = 1

  # why: ADR-0003 §3 — the handler returns {"batchItemFailures": [...]} and the
  # poller deletes only what is NOT listed. Without this flag the return value
  # is ignored and the whole batch is deleted on any normal return.
  function_response_types = ["ReportBatchItemFailures"]
}
