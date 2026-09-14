variable "region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "eu-north-1"
}

variable "project" {
  description = "Name prefix for all resources."
  type        = string
  default     = "notification-system"
}
