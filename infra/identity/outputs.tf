output "github_oidc_provider_arn" {
  description = "GitHub OIDC provider trusted by the deployment identities."
  value       = aws_iam_openid_connect_provider.github.arn
}

output "dev_role_arn" {
  description = "DEV deployment identity ARN."
  value       = aws_iam_role.github_deployment["DEV"].arn
}

output "test_role_arn" {
  description = "TEST deployment identity ARN."
  value       = aws_iam_role.github_deployment["TEST"].arn
}

output "prod_role_arn" {
  description = "PROD deployment identity ARN."
  value       = aws_iam_role.github_deployment["PROD"].arn
}

output "trusted_oidc_subjects" {
  description = "Exact immutable GitHub environment subjects required by AWS."
  value       = local.trusted_subjects
}
