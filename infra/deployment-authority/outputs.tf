output "authorized_role_name" {
  description = "Existing DEV role targeted by the bounded artifact policy."
  value       = data.aws_iam_role.dev.name
}

output "authorized_artifact_prefix" {
  description = "Object prefix targeted by the DEV GetObject and PutObject authority."
  value       = "s3://agent-foundry-deployment-artifacts-276713393004-us-east-1/dev/*"
}
