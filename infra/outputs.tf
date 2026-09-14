output "notify_url" {
  description = "POST here to request a notification."
  value       = "${aws_apigatewayv2_api.http.api_endpoint}/notifications"
}
