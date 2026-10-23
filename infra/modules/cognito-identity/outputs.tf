locals {
  issuer = "https://cognito-idp.${data.aws_region.current.name}.amazonaws.com/${aws_cognito_user_pool.this.id}"
}

output "user_pool_id" {
  value = aws_cognito_user_pool.this.id
}

output "hosted_ui_domain" {
  value = "https://${aws_cognito_user_pool_domain.this.domain}.auth.${data.aws_region.current.name}.amazoncognito.com"
}

output "reviewer_ui_client_id" {
  value = aws_cognito_user_pool_client.reviewer_ui.id
}

# The secret is in state regardless, as a resource attribute; this output only spares a
# console lookup. Task 703 moves it into Secrets Manager for the UI service, and state access
# stays restricted to the operator role (backend.hcl.example).
output "reviewer_ui_client_secret" {
  value     = aws_cognito_user_pool_client.reviewer_ui.client_secret
  sensitive = true
}

output "development_client_id" {
  description = "Audience for a local API used with the development client; null when none exists."
  value       = one(aws_cognito_user_pool_client.development[*].id)
}

# Exactly what the API and worker need (docs/authentication.md). Cognito's issuer has no
# trailing slash, and the issuer comparison is exact.
output "api_settings" {
  value = {
    RXAUTH_AUTH_ENABLED            = "true"
    RXAUTH_AUTH_ISSUER             = local.issuer
    RXAUTH_AUTH_JWKS_URL           = "${local.issuer}/.well-known/jwks.json"
    RXAUTH_AUTH_AUDIENCE           = aws_cognito_user_pool_client.reviewer_ui.id
    RXAUTH_AUTH_AUDIENCE_CLAIM     = "client_id"
    RXAUTH_AUTH_REQUIRED_TOKEN_USE = "access"
    RXAUTH_AUTH_ALGORITHMS         = "RS256"
    RXAUTH_AUTH_ORGANIZATION_CLAIM = var.organization_claim
    RXAUTH_AUTH_ROLES_CLAIM        = "cognito:groups"
  }
}
