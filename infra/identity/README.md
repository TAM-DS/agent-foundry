# Federated AWS deployment identity

This root module defines one GitHub OIDC provider and three separate AWS trust
identities in account `276713393004`, using region `us-east-1`:
`agent-foundry-github-dev`, `agent-foundry-github-test`, and
`agent-foundry-github-prod`.

Running a workflow does not grant AWS authority. AWS must verify the GitHub
provider, audience `sts.amazonaws.com`, and an exact environment subject using
`StringEquals`. The reviewed immutable repository identity is deliberately
pinned in locals, including owner ID `143246197` and repository ID `1374972009`:

- DEV: `repo:TAM-DS@143246197/agent-foundry@1374972009:environment:DEV`
- TEST: `repo:TAM-DS@143246197/agent-foundry@1374972009:environment:TEST`
- PROD: `repo:TAM-DS@143246197/agent-foundry@1374972009:environment:PROD`

A different environment, a branch subject, or a subject without the matching
environment cannot satisfy that role's trust policy. Sessions are limited to
3600 seconds. All three live roles remain permissionless: zero managed policies
and zero inline permissions policies.
Later slices will add bounded permissions while preserving these trust boundaries.

## Authoritative state

S3 is the authoritative backend for identity infrastructure. State uses
`identity/terraform.tfstate` in bucket
`agent-foundry-terraform-state-276713393004-us-east-1` in `us-east-1`, with
encryption and native S3 locking enabled. Identity infrastructure has been applied;
the remote Terraform state is authoritative.

Using the S3 backend grants no AWS deployment permissions. Backend state and
deployment authority remain separate trust boundaries.

## Local verification

Invoke Terraform with temporary non-root credentials selected externally through
`AWS_PROFILE=agent-foundry-admin`. Credentials and secrets must never enter
Terraform configuration or tfvars. From the repository root:

```sh
terraform fmt -recursive
terraform fmt -check -recursive
git diff --check
git status --short
```

Post-apply verification reports zero drift and no resource changes. The provider
rejects any AWS account other than `276713393004`. Commit `.terraform.lock.hcl` for reproducibility;
generated working data, local state, and saved plans are ignored.

Outputs contain only identity ARNs and exact trusted subjects. Live GitHub OIDC
federation has not yet been proven and will be tested separately using the manual
DEV workflow `.github/workflows/verify-aws-oidc.yml`.
