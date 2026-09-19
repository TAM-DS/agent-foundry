# Terraform state bootstrap

This root defines only the state bucket
`agent-foundry-terraform-state-276713393004-us-east-1` in account
`276713393004`, region `us-east-1`. It requires Terraform >= 1.16, < 2.0
and hashicorp/aws ~> 6.0; the generated lock file pins the selected provider.

Five resources create and harden one bucket: the bucket itself, versioning,
public access blocking, default SSE-S3 encryption (`AES256`), and
`BucketOwnerEnforced` ownership (ACLs disabled). The bucket has deterministic
Project, ManagedBy, and Boundary tags and `prevent_destroy = true`.
This lifecycle guard protects against Terraform destruction while the resource
configuration remains present; it does not prevent out-of-band deletion.

## Verification

Supply temporary SSO credentials externally through the
`agent-foundry-admin` AWS profile. No profile or credentials are embedded in
Terraform. Run from the repository root:

```sh
terraform fmt -recursive
terraform fmt -check -recursive
AWS_PROFILE=agent-foundry-admin terraform -chdir=infra/bootstrap init -backend=false
AWS_PROFILE=agent-foundry-admin terraform -chdir=infra/bootstrap validate
AWS_PROFILE=agent-foundry-admin terraform -chdir=infra/bootstrap plan -no-color
git diff --check
git status --short
```

For an empty state, expect 5 additions, 0 changes, and 0 destructions, all
directly related to this bucket. There are no DynamoDB, KMS, IAM, compute,
networking, CloudWatch, application, or deployment resources.
Validation and planning do not authorize an apply.

## Durable state boundary

No backend is configured yet. Terraform therefore defaults to local state;
`init -backend=false` does not establish remote state. This slice prepares
the configuration and does not apply it or change `infra/identity`.

A separately authorized bootstrap apply and subsequent backend configuration
and state migration are required before identity infrastructure is applied.
Preserve the initial bootstrap state securely until its migration to durable
remote storage has been verified; do not rely on disposable local state for
ongoing infrastructure authority. Never commit state or credentials.

Future S3 backends must enable native locking with `use_lockfile = true`.
Native locking uses the existing S3 store, avoiding a separate table and its
permissions and operational overhead. HashiCorp has deprecated DynamoDB-based
locking. Backend access must explicitly include the necessary state-object
permissions and GetObject, PutObject, and DeleteObject on the lock object;
creating the bucket does not itself grant backend or runtime authority.
See the [HashiCorp S3 backend documentation](https://developer.hashicorp.com/terraform/language/backend/s3).
