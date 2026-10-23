# Staging identity provider (backlog task 201). Applied in the staging AWS account, us-east-1
# (ADR 003). Task 701's foundation will own the shared backend and account baseline; until it
# lands, this root stands alone with a partial S3 backend supplied at `terraform init`:
#
#   terraform init -backend-config=backend.hcl   # bucket, key, region, use_lockfile
#
# See docs/authentication.md, "Staging identity provider: Amazon Cognito".

terraform {
  required_version = ">= 1.6"

  backend "s3" {}

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.81, < 7.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"

  default_tags {
    tags = {
      project     = "rxauth-ai"
      environment = "staging"
      data        = "synthetic-only"
      backlog     = "201"
    }
  }
}

module "identity" {
  source = "../../modules/cognito-identity"

  name_prefix   = "rxauth-staging"
  domain_prefix = var.domain_prefix
  callback_urls = var.callback_urls
  logout_urls   = var.logout_urls

  development_callback_urls = var.development_callback_urls
  development_logout_urls   = var.development_logout_urls

  # Two organizations so task 205 can prove a reviewer in one never reaches the other, and two
  # reviewers in the first so task 202 can prove distinct sign-ins get distinct subjects.
  synthetic_users = {
    "reviewer-a1" = { email = "reviewer-a1@synthetic.example.test", organization_id = "synthetic-org-a", groups = ["case:read", "case:review"] }
    "reviewer-a2" = { email = "reviewer-a2@synthetic.example.test", organization_id = "synthetic-org-a", groups = ["case:read", "case:review"] }
    "intake-a1"   = { email = "intake-a1@synthetic.example.test", organization_id = "synthetic-org-a", groups = ["case:write"] }
    "admin-a1"    = { email = "admin-a1@synthetic.example.test", organization_id = "synthetic-org-a", groups = ["admin"] }
    "reviewer-b1" = { email = "reviewer-b1@synthetic.example.test", organization_id = "synthetic-org-b", groups = ["case:read", "case:review"] }
  }
}
