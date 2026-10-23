# Staging reviewer identity on Amazon Cognito (backlog task 201, ADR 003).
#
# What the API needs from a token is fixed by auth.py: an asymmetric signature from a JWKS,
# the issuer, an audience, `sub`, an organization claim, and roles. Cognito supplies the
# signature, issuer, `sub` and `cognito:groups` itself. The audience is the app client
# (`client_id`, RXAUTH_AUTH_AUDIENCE_CLAIM), and the organization is added by the
# pre-token-generation trigger from an attribute nobody can change after creation.

data "aws_region" "current" {}

locals {
  roles = toset(["case:read", "case:write", "case:review", "admin"])
  memberships = merge([
    for username, user in var.synthetic_users : {
      for group in user.groups : "${username}/${group}" => { username = username, group = group }
    }
  ]...)
}

resource "aws_cognito_user_pool" "this" {
  name                = "${var.name_prefix}-reviewers"
  user_pool_tier      = "ESSENTIALS" # access-token customization needs Essentials or Plus
  deletion_protection = "ACTIVE"
  mfa_configuration   = var.mfa_configuration

  # Accounts are provisioned, never self-registered: an organization is assigned by an
  # administrator, not claimed by the person signing up.
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  software_token_mfa_configuration {
    enabled = true
  }

  password_policy {
    minimum_length                   = 14
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 3
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  auto_verified_attributes = ["email"]

  # Immutable: set once at creation. A user who could edit their own organization could
  # move themselves into another tenant.
  schema {
    name                     = "org_id"
    attribute_data_type      = "String"
    mutable                  = false
    required                 = false
    developer_only_attribute = false

    string_attribute_constraints {
      min_length = 1
      max_length = 128
    }
  }

  lambda_config {
    pre_token_generation_config {
      lambda_arn     = aws_lambda_function.pre_token_generation.arn
      lambda_version = "V2_0"
    }
  }
}

resource "aws_cognito_user_pool_domain" "this" {
  domain       = var.domain_prefix
  user_pool_id = aws_cognito_user_pool.this.id
}

resource "aws_cognito_user_group" "role" {
  for_each     = local.roles
  name         = each.value
  user_pool_id = aws_cognito_user_pool.this.id
  description  = "RxAuth application role ${each.value}; never crosses the organization boundary."
}

# The reviewer UI: a confidential server-side client (Next.js holds the secret, task 202)
# using the authorization-code flow. No password grant, no implicit flow.
resource "aws_cognito_user_pool_client" "reviewer_ui" {
  name         = "${var.name_prefix}-reviewer-ui"
  user_pool_id = aws_cognito_user_pool.this.id

  generate_secret                      = true
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = var.callback_urls
  logout_urls                          = var.logout_urls
  explicit_auth_flows                  = ["ALLOW_REFRESH_TOKEN_AUTH"]
  prevent_user_existence_errors        = "ENABLED"
  enable_token_revocation              = true

  # Short access tokens bound how long a revoked reviewer keeps API access.
  access_token_validity  = 15
  id_token_validity      = 15
  refresh_token_validity = 12
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "hours"
  }

  read_attributes  = ["email", "email_verified", "custom:org_id"]
  write_attributes = ["email"]
}

# Local development of the reviewer UI (task 202) gets its own client. Its client_id is not
# the API audience, so a secret on a developer laptop, or a code delivered to any process on
# localhost, yields tokens the deployed API refuses. A local API sets its audience to it.
resource "aws_cognito_user_pool_client" "development" {
  count        = length(var.development_callback_urls) > 0 ? 1 : 0
  name         = "${var.name_prefix}-reviewer-ui-development"
  user_pool_id = aws_cognito_user_pool.this.id

  generate_secret                      = true
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = var.development_callback_urls
  logout_urls                          = var.development_logout_urls
  explicit_auth_flows                  = ["ALLOW_REFRESH_TOKEN_AUTH"]
  prevent_user_existence_errors        = "ENABLED"
  enable_token_revocation              = true

  access_token_validity  = 15
  id_token_validity      = 15
  refresh_token_validity = 12
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "hours"
  }

  read_attributes  = ["email", "email_verified", "custom:org_id"]
  write_attributes = ["email"]
}

# Pre-token-generation trigger.

data "archive_file" "pre_token_generation" {
  type        = "zip"
  source_file = "${path.module}/lambda/pre_token_generation.py"
  output_path = "${path.module}/.build/pre_token_generation.zip"
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "pre_token_generation" {
  name               = "${var.name_prefix}-cognito-pre-token"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_cloudwatch_log_group" "pre_token_generation" {
  name              = "/aws/lambda/${var.name_prefix}-cognito-pre-token"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "pre_token_generation_logs" {
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.pre_token_generation.arn}:*"]
  }
}

# Logging only. The trigger reads nothing and calls nothing.
resource "aws_iam_role_policy" "pre_token_generation_logs" {
  name   = "logs"
  role   = aws_iam_role.pre_token_generation.id
  policy = data.aws_iam_policy_document.pre_token_generation_logs.json
}

resource "aws_lambda_function" "pre_token_generation" {
  function_name    = "${var.name_prefix}-cognito-pre-token"
  role             = aws_iam_role.pre_token_generation.arn
  runtime          = "python3.12"
  handler          = "pre_token_generation.handler"
  filename         = data.archive_file.pre_token_generation.output_path
  source_code_hash = data.archive_file.pre_token_generation.output_base64sha256
  timeout          = 5
  memory_size      = 128

  environment {
    variables = {
      ORGANIZATION_CLAIM = var.organization_claim
    }
  }

  depends_on = [aws_cloudwatch_log_group.pre_token_generation]
}

resource "aws_lambda_permission" "cognito" {
  statement_id  = "AllowCognitoInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.pre_token_generation.function_name
  principal     = "cognito-idp.amazonaws.com"
  source_arn    = aws_cognito_user_pool.this.arn
}

# Synthetic reviewer accounts. Terraform never knows a password: whoever holds a temporary
# password before first sign-in also enrols the TOTP device, so a password in state would let
# anyone who can read state take the account, MFA included. Cognito generates an unsent
# temporary password here; the operator sets a real one per person out of band
# (docs/authentication.md, verification runbook).

resource "aws_cognito_user" "synthetic" {
  for_each       = var.synthetic_users
  user_pool_id   = aws_cognito_user_pool.this.id
  username       = each.key
  message_action = "SUPPRESS"

  attributes = {
    email          = each.value.email
    email_verified = true
    org_id         = each.value.organization_id
  }
}

resource "aws_cognito_user_in_group" "synthetic" {
  for_each     = local.memberships
  user_pool_id = aws_cognito_user_pool.this.id
  username     = aws_cognito_user.synthetic[each.value.username].username
  group_name   = aws_cognito_user_group.role[each.value.group].name
}
