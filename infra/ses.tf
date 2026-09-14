# SES email identity — the address notifications are sent FROM and, while SES is in
# its sandbox, the only address they can be sent TO. Creating this resource makes AWS
# send a verification email; George clicks the link to complete it (M0).
#
# The address is a variable so it never lands in git: locally it comes from
# terraform.tfvars (gitignored); in CI it's the NOTIFY_EMAIL repo secret injected as
# the TF_VAR_notify_email environment variable.
variable "notify_email" {
  description = "Verified SES email identity (sender + sandbox recipient)."
  type        = string
  sensitive   = true
}

resource "aws_ses_email_identity" "notify" {
  email = var.notify_email
}
