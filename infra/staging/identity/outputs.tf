output "api_settings" {
  value = module.identity.api_settings
}

output "hosted_ui_domain" {
  value = module.identity.hosted_ui_domain
}

output "reviewer_ui_client_id" {
  value = module.identity.reviewer_ui_client_id
}

output "reviewer_ui_client_secret" {
  value     = module.identity.reviewer_ui_client_secret
  sensitive = true
}

output "development_client_id" {
  value = module.identity.development_client_id
}
