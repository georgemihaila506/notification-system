output "notify_url" {
  description = "POST here to request a notification."
  value       = "${aws_apigatewayv2_api.http.api_endpoint}/notifications"
}

output "topic_arn" {
  value = aws_sns_topic.notifications.arn
}
