output "notify_url" {
  description = "POST here to request a notification."
  value       = "${aws_apigatewayv2_api.http.api_endpoint}/notifications"
}

output "topic_arn" {
  value = aws_sns_topic.notifications.arn
}

output "queue_urls" {
  description = "Per-channel queue URLs, for watching a drill."
  value       = { for k, q in aws_sqs_queue.channel : k => q.id }
}

output "dlq_urls" {
  description = "Per-channel DLQ URLs, for inspection and redrive."
  value       = { for k, q in aws_sqs_queue.dlq : k => q.id }
}
