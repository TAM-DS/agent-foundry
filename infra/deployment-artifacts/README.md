# Deployment artifact boundary

This Terraform root defines the durable deployment artifact bucket
`agent-foundry-deployment-artifacts-276713393004-us-east-1` in AWS account
`276713393004`, region `us-east-1`.

The existence of a deployment artifact store does not authorize an identity to publish to it.

This slice creates zero IAM authority. It defines no IAM policies, policy
attachments, inline role policies, or bucket policies granting access. DEV, TEST,
and PROD roles retain zero deployment permissions. A later slice will
independently grant DEV a narrowly bounded artifact capability while TEST and
PROD remain permissionless.

The bucket has versioning enabled, all four public-access blocks enabled,
SSE-S3 (`AES256`) default encryption, `BucketOwnerEnforced` ownership, and
Terraform `prevent_destroy = true`. Its deterministic tags are
`Project = AgentFoundry`, `ManagedBy = Terraform`, and
`Boundary = DeploymentArtifacts`.

State uses the existing bucket
`agent-foundry-terraform-state-276713393004-us-east-1` at
`deployment-artifacts/terraform.tfstate`, with encryption and S3 lockfiles enabled.
The provider restricts the account and region; credentials are supplied externally
without a hardcoded profile. Outputs expose `artifact_bucket_name` and
`artifact_bucket_arn`.

Run verification from the repository root using the existing administrative
profile:

```sh
terraform fmt -recursive
terraform fmt -check -recursive
AWS_PROFILE=agent-foundry-admin terraform -chdir=infra/deployment-artifacts init
AWS_PROFILE=agent-foundry-admin terraform -chdir=infra/deployment-artifacts validate
AWS_PROFILE=agent-foundry-admin terraform -chdir=infra/deployment-artifacts plan -no-color
git diff --check
git status --short
```

The reviewed plan contained exactly five additions: the S3 bucket, versioning,
public-access block, default encryption, and ownership controls, with zero
changes, zero destroys, and no IAM resources. That exact saved plan was applied.

Post-apply verification confirmed versioning enabled, all four public-access
blocks enabled, `AES256` default encryption, `BucketOwnerEnforced` ownership,
the expected deterministic tags, and no bucket policy. Remote Terraform state
exists at `deployment-artifacts/terraform.tfstate`, and Terraform reports zero
drift.

DEV, TEST, and PROD remain without managed or inline IAM permission policies.
The deployment target therefore exists independently of deployment authority.
