data "aws_iam_role" "dev" {
  name = "agent-foundry-github-dev"
}

resource "aws_iam_role_policy" "dev_artifact_access" {
  name = "agent-foundry-dev-artifact-access"
  role = data.aws_iam_role.dev.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "s3:GetObject",
        "s3:PutObject",
      ]
      Resource = "arn:aws:s3:::agent-foundry-deployment-artifacts-276713393004-us-east-1/dev/*"
    }]
  })
}
