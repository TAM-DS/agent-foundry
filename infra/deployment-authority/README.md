# Bounded DEV artifact authority

Successful federation establishes identity. This policy grants only the
minimum DEV artifact authority required for the governed operation.

This independent Terraform root references the existing
`agent-foundry-github-dev` role and defines exactly one inline IAM policy,
`agent-foundry-dev-artifact-access`. Its only Allow statement grants
`s3:GetObject` and `s3:PutObject` on exactly
`arn:aws:s3:::agent-foundry-deployment-artifacts-276713393004-us-east-1/dev/*`.
It grants no bucket-level access. Everything outside this narrow Allow remains
denied by default; no compensating explicit Deny is added.

| Identity | Target | Authority |
| --- | --- | --- |
| DEV | `dev/*` | GetObject, PutObject |
| DEV | `prod/*` | not authorized |
| DEV | `test/*` | not authorized |
| DEV | bucket listing | not authorized |
| TEST | deployment | no deployment authority |
| PROD | deployment | no deployment authority |

TEST and PROD remain unchanged and permissionless. The policy grants no access
to the Terraform state bucket and no object deletion, IAM, or STS permissions.
This root creates no roles, managed policies, policy attachments, bucket policies,
or other infrastructure. Existing identity, artifact, and bootstrap roots and
GitHub workflows remain unchanged.

The provider is restricted to account `276713393004` in `us-east-1`.
State uses the existing encrypted S3 backend at
`deployment-authority/terraform.tfstate` with S3 lockfiles. Credentials are
selected externally; the provider does not hardcode an AWS profile.

From the repository root, verify with:

```sh
terraform fmt -recursive
terraform fmt -check -recursive
AWS_PROFILE=agent-foundry-admin terraform -chdir=infra/deployment-authority init
AWS_PROFILE=agent-foundry-admin terraform -chdir=infra/deployment-authority validate
AWS_PROFILE=agent-foundry-admin terraform -chdir=infra/deployment-authority plan -no-color
git diff --check
git status --short
```

The reviewed plan contained exactly one managed resource addition,
`aws_iam_role_policy.dev_artifact_access`, with zero changes and zero destroys.
Reading the existing DEV role is not an infrastructure mutation. That exact
saved plan was applied.

Post-apply verification confirmed that DEV has exactly one inline permission
policy granting only `s3:GetObject` and `s3:PutObject` on the `dev/*` object
prefix. DEV has no managed policies. TEST and PROD remain without managed or
inline permission policies. Remote Terraform state exists at
`deployment-authority/terraform.tfstate`, and Terraform reports zero drift.

A later verification slice will prove the allowed and denied boundaries through
live GitHub OIDC.
