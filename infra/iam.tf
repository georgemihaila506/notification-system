# Least privilege for the app's Lambdas: only the DynamoDB actions we use, only on our
# two tables; only sns:Publish, only on our topic; only the SQS actions the poller
# needs, only on our channel queues. (SES is added in M4.)
data "aws_iam_policy_document" "app_access" {
  statement {
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.prefs.arn, aws_dynamodb_table.deliveries.arn]
  }
  statement {
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.notifications.arn]
  }
  # why: the event source mapping polls AS the function's role. Receive to read,
  # Delete to acknowledge, GetQueueAttributes so the poller can scale its polling.
  statement {
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
    ]
    resources = [for q in aws_sqs_queue.channel : q.arn]
  }
}

resource "aws_iam_role_policy" "app_access" {
  name   = "${var.project}-app-access"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.app_access.json
}
