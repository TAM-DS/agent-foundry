output "artifact_bucket_name" {
  description = "Name of the deployment artifact bucket."
  value       = aws_s3_bucket.deployment_artifacts.id
}

output "artifact_bucket_arn" {
  description = "ARN of the deployment artifact bucket."
  value       = aws_s3_bucket.deployment_artifacts.arn
}
