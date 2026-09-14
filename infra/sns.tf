# The single fan-out point (ADR-0002): ingest publishes once, with the user's allowed
# channels as a message attribute; per-channel SQS subscriptions (M3) filter on it.
resource "aws_sns_topic" "notifications" {
  name = "${var.project}-notifications"
}
