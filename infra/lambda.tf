# Zip src/ as-is (stdlib + boto3 only). The zip root contains `notifier/`.
data "archive_file" "app" {
  type        = "zip"
  source_dir  = "${path.module}/../src"
  output_path = "${path.module}/build/app.zip"
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${var.project}-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "ingest" {
  function_name    = "${var.project}-ingest"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "notifier.handlers.ingest.handler"
  filename         = data.archive_file.app.output_path
  source_code_hash = data.archive_file.app.output_base64sha256

  environment {
    variables = {
      PREFS_TABLE      = aws_dynamodb_table.prefs.name
      DELIVERIES_TABLE = aws_dynamodb_table.deliveries.name
      TOPIC_ARN        = aws_sns_topic.notifications.arn
    }
  }
}
