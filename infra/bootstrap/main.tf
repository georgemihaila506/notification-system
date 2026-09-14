# Bootstrap — the S3 bucket holding the MAIN stack's state, plus the GitHub Actions
# deploy role. Own local state; applied once, rarely touched; deliberately separate
# so `terraform destroy` on the app never deletes its own state store or CI role.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  type    = string
  default = "eu-north-1"
}

variable "project" {
  type    = string
  default = "notification-system"
}

data "aws_caller_identity" "current" {}

# --- state bucket -------------------------------------------------------------
resource "aws_s3_bucket" "tfstate" {
  bucket = "${var.project}-tfstate-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

output "state_bucket" {
  value = aws_s3_bucket.tfstate.bucket
}

# --- GitHub Actions OIDC deploy role -----------------------------------------
# The OIDC identity provider is ACCOUNT-WIDE and already exists (created by the
# url-shortener bootstrap). Creating a second one for the same URL fails, so we
# look the existing one up instead.
data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

# GitHub mints IMMUTABLE subject claims for this account:
#   repo:<owner>@<owner-id>/<repo>@<repo-id>:<ref>
# The owner id (37144843) is fixed and unforgeable; the repo id isn't known until
# the repo exists, so it's wildcarded — safe, because nobody else can create a
# repo under this owner id.
variable "github_sub" {
  type    = string
  default = "repo:georgemihaila506@37144843/notification-system@*:*"
}

data "aws_iam_policy_document" "github_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [var.github_sub]
    }
  }
}

resource "aws_iam_role" "github_actions" {
  name               = "${var.project}-github-actions"
  assume_role_policy = data.aws_iam_policy_document.github_assume.json
}

# Service-scoped deploy permissions (not AdministratorAccess). A deploy role
# legitimately needs broad access across the services this project uses.
data "aws_iam_policy_document" "github_deploy" {
  statement {
    actions = [
      "lambda:*",
      "apigateway:*",
      "dynamodb:*",
      "sns:*",
      "sqs:*",
      "ses:*",
      "s3:*",
      "iam:*",
      "cloudwatch:*",
      "logs:*",
      "sts:GetCallerIdentity",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  name   = "${var.project}-github-deploy"
  role   = aws_iam_role.github_actions.id
  policy = data.aws_iam_policy_document.github_deploy.json
}

output "github_actions_role_arn" {
  value = aws_iam_role.github_actions.arn
}
