# State lives in the S3 bucket created by ./bootstrap (versioned, private), with
# S3-native locking. Backend config must be literal — no variables here.
terraform {
  backend "s3" {
    bucket       = "notification-system-tfstate-944921954001"
    key          = "notification-system/terraform.tfstate"
    region       = "eu-north-1"
    encrypt      = true
    use_lockfile = true
  }
}
