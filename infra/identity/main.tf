locals {
  # Reviewed immutable identity; deliberately not configurable at invocation time.
  github_subject_prefix = "repo:TAM-DS@143246197/agent-foundry@1374972009"
  environments = {
    DEV  = "agent-foundry-github-dev"
    TEST = "agent-foundry-github-test"
    PROD = "agent-foundry-github-prod"
  }
  trusted_subjects = {
    for environment in keys(local.environments) :
    environment => "${local.github_subject_prefix}:environment:${environment}"
  }
  identity_tags = {
    Project   = "AgentFoundry"
    ManagedBy = "Terraform"
    Boundary  = "DeploymentIdentity"
  }
}

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]

  # AWS uses trusted CA handling for GitHub; no thumbprint discovery is needed.
  tags = local.identity_tags
}

resource "aws_iam_role" "github_deployment" {
  for_each = local.environments

  name                 = each.value
  description          = "GitHub ${each.key} deployment identity only; no deployment permissions."
  max_session_duration = 3600

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = aws_iam_openid_connect_provider.github.arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = local.trusted_subjects[each.key]
        }
      }
    }]
  })

  # Trust establishes identity eligibility only. No permissions are granted here.
  tags = merge(local.identity_tags, { Environment = each.key })
}
