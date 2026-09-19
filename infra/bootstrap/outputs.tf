output "state_bucket_name" {
  description = "S3 bucket name for future Terraform remote state configuration."
  value       = aws_s3_bucket.terraform_state.id
}

output "state_bucket_arn" {
  description = "ARN of the Terraform state bucket."
  value       = aws_s3_bucket.terraform_state.arn
}
