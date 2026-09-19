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
3600 seconds. These roles intentionally have zero deployment permissions: no
managed policies, inline permissions policies, or policy attachments are created.
Later slices will add bounded permissions while preserving these trust boundaries.

## Local verification

Invoke Terraform with temporary non-root credentials selected externally through
the `agent-foundry-admin` AWS profile. Credentials and secrets must never enter
Terraform configuration or tfvars. From the repository root:

```sh
terraform fmt -recursive
terraform fmt -check -recursive
AWS_PROFILE=agent-foundry-admin terraform -chdir=infra/identity init -backend=false
AWS_PROFILE=agent-foundry-admin terraform -chdir=infra/identity validate
AWS_PROFILE=agent-foundry-admin terraform -chdir=infra/identity plan
git diff --check
```

The expected plan contains exactly four creates: one OIDC provider and three IAM
roles. Stop if any other resource appears. The provider rejects any AWS account
other than `276713393004`. Commit `.terraform.lock.hcl` for reproducibility;
generated working data, local state, and saved plans are ignored.

No `terraform apply` has been performed for this slice. No remote backend is
configured. State architecture will be addressed separately before consequential
deployment infrastructure is built. Outputs contain only identity ARNs and exact
trusted subjects.
