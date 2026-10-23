variable "name_prefix" {
  description = "Prefix for every resource name, e.g. rxauth-staging."
  type        = string
}

variable "domain_prefix" {
  description = "Globally unique prefix for the Cognito hosted sign-in domain."
  type        = string
}

variable "callback_urls" {
  description = "Deployed reviewer UI OAuth callback URLs. HTTPS only; localhost belongs on the development client."
  type        = list(string)

  validation {
    condition     = alltrue([for url in var.callback_urls : startswith(url, "https://")])
    error_message = "callback_urls must be HTTPS. Put http://localhost on development_callback_urls."
  }
}

variable "logout_urls" {
  description = "Deployed reviewer UI post-logout URLs. HTTPS only."
  type        = list(string)

  validation {
    condition     = alltrue([for url in var.logout_urls : startswith(url, "https://")])
    error_message = "logout_urls must be HTTPS."
  }
}

variable "development_callback_urls" {
  description = "Callback URLs for a separate local-development client. Empty creates no client; keep empty in production."
  type        = list(string)
  default     = []
}

variable "development_logout_urls" {
  description = "Post-logout URLs for the local-development client."
  type        = list(string)
  default     = []
}

variable "organization_claim" {
  description = "Access-token claim that carries the tenant. Must match RXAUTH_AUTH_ORGANIZATION_CLAIM."
  type        = string
  default     = "org_id"
}

variable "mfa_configuration" {
  description = "ON requires TOTP for every user. Task 207 owns the MFA policy; ON is the default."
  type        = string
  default     = "ON"

  validation {
    condition     = contains(["ON", "OPTIONAL"], var.mfa_configuration)
    error_message = "mfa_configuration must be ON or OPTIONAL; OFF is not offered."
  }
}

variable "log_retention_days" {
  description = "Trigger log retention. 90 days per docs/retention-policy.md."
  type        = number
  default     = 90
}

variable "synthetic_users" {
  description = <<-EOT
    Synthetic reviewer accounts, keyed by username. Synthetic identities only: never a real
    person's name or address. Groups must be application roles (auth.py).
  EOT
  type = map(object({
    email           = string
    organization_id = string
    groups          = list(string)
  }))
  default = {}

  validation {
    condition = alltrue([
      for user in values(var.synthetic_users) :
      can(regex("^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$", user.organization_id))
    ])
    error_message = "organization_id must match auth.SAFE_ID_PATTERN."
  }

  validation {
    condition = alltrue(flatten([
      for user in values(var.synthetic_users) : [
        for group in user.groups : contains(["case:read", "case:write", "case:review", "admin"], group)
      ]
    ]))
    error_message = "groups may only name application roles: case:read, case:write, case:review, admin."
  }
}
