variable "domain_prefix" {
  description = "Globally unique Cognito domain prefix, e.g. rxauth-staging-<suffix>."
  type        = string
}

variable "callback_urls" {
  description = "Staging reviewer UI callback URLs (HTTPS). This client's id is the staging API audience."
  type        = list(string)
}

variable "logout_urls" {
  description = "Staging reviewer UI post-logout URLs (HTTPS)."
  type        = list(string)
}

variable "development_callback_urls" {
  description = "http://localhost callbacks for the separate development client (task 202)."
  type        = list(string)
  default     = []
}

variable "development_logout_urls" {
  description = "Post-logout URLs for the development client."
  type        = list(string)
  default     = []
}
