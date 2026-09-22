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

# rate: pk=user_id#window -> count, expires_at (ADR-0006)
#
# why its own table rather than a keyspace inside prefs: TTL below is a TABLE-WIDE
# setting. Putting counters in prefs would arm automatic deletion on durable user
# data, where one stray `expires_at` attribute silently removes someone's
# preferences. Idle PAY_PER_REQUEST tables cost nothing, so the separation is free.
resource "aws_dynamodb_table" "rate" {
  name         = "${var.project}-rate"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  attribute {
    name = "pk"
    type = "S"
  }

  # why: without this every user accumulates one row per hour forever. DynamoDB
  # deletes on its own schedule (typically within 48h of the timestamp), which is
  # fine — the counter stops being read the moment its window closes.
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}
