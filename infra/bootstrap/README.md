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

Supply temporary SSO credentials externally through
`AWS_PROFILE=agent-foundry-admin`. No profile or credentials are embedded in
Terraform. For configuration-only checks, run from the repository root:

```sh
terraform fmt -recursive
terraform fmt -check -recursive
git diff --check
git status --short
```

The authoritative S3 state contains the existing five resources directly related
to this bucket. There are no DynamoDB, KMS, IAM, compute,
networking, CloudWatch, application, or deployment resources.
Validation and planning do not authorize an apply.

## Durable state boundary

S3 is the authoritative Terraform backend for this bucket at
`bootstrap/terraform.tfstate` in `us-east-1`, with encryption enabled.
The original local state was successfully migrated. Local state and backup
files are no longer the active source of authority. Retain them only until
remote-only verification is complete, then remove the local copies securely.
Never commit state or credentials.

Independent verification confirmed that `terraform state list` returns exactly
the existing five bootstrap resources and `terraform plan` reports:
"No changes. Your infrastructure matches the configuration."
AWS HeadObject succeeds for the state object in
`agent-foundry-terraform-state-276713393004-us-east-1` at
`bootstrap/terraform.tfstate`, confirming `AES256` encryption and an S3
`VersionId`. Bucket versioning protects state history. No AWS resources were
created, changed, or destroyed by the migration.

This S3 backend uses native S3 locking with `use_lockfile = true`.
No DynamoDB locking is used.
Native locking uses the existing S3 store, avoiding a separate table and its
permissions and operational overhead. HashiCorp has deprecated DynamoDB-based
locking. Backend access must explicitly include the necessary state-object
permissions and GetObject, PutObject, and DeleteObject on the lock object;
creating the bucket does not itself grant backend or runtime authority.
See the [HashiCorp S3 backend documentation](https://developer.hashicorp.com/terraform/language/backend/s3).
