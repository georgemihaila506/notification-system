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

  environment {
    variables = {
      CHANNEL              = each.key
      PREFS_TABLE          = aws_dynamodb_table.prefs.name
      DELIVERIES_TABLE     = aws_dynamodb_table.deliveries.name
      VISIBILITY_TIMEOUT_S = aws_sqs_queue.channel[each.key].visibility_timeout_seconds
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
