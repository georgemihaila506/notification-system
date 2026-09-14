# prefs:      pk=user_id -> channels list (+ rate counters keyed user#rate#window in M6)
# deliveries: pk=notification_id#channel -> status/attempts — the dedup ledger (ADR-0001)
resource "aws_dynamodb_table" "prefs" {
  name         = "${var.project}-prefs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  attribute {
    name = "pk"
    type = "S"
  }
}

resource "aws_dynamodb_table" "deliveries" {
  name         = "${var.project}-deliveries"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  attribute {
    name = "pk"
    type = "S"
  }
}
