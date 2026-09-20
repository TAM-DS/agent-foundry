# Agent Foundry Architecture

## Purpose

Agent Foundry is a governed system for creating, evaluating, approving, deploying, and operating autonomous agents.

Its central design question is:

> **How do you scale autonomy without scaling risk at the same rate?**

The answer is an explicit separation between **capability** and **authority**.

Creating an artifact does not approve it. Passing an evaluation does not approve it. Approval does not prove deployment occurred. Deployment does not grant runtime authority. Tool availability does not imply permission to use the tool.

The implementation is intentionally small. The point is not service count. The point is whether each consequential transition has an explicit authority source and durable evidence.

---

## Status

Agent Foundry v1 is functionally complete for its declared DEV proof.

The final live GitHub Actions run on 2026-09-20 proved the full governed path against real AWS infrastructure:

```text
BUILD
→ EVALUATE
→ APPROVE
→ FREEZE
→ DEPLOY
→ VERIFY
→ DEPLOYED
→ ISSUE RUNTIME GRANT
→ OPERATING
→ ALLOW / DENY TOOL REQUESTS
```

Final proof run: `35525913401`

The run completed with 399 tests passing before federation, a bounded DEV OIDC role, successful S3 deployment and exact read-back verification, explicit runtime authority, and deterministic tool authorization.

---

## Design Thesis

Agent Foundry treats an autonomous agent as a governed artifact moving through explicit trust boundaries.

The conceptual process is:

```text
REQUEST
→ VALIDATE
→ BUILD
→ EVALUATE
→ APPROVE
→ FREEZE EVIDENCE
→ DEPLOY
→ VERIFY EXTERNAL STATE
→ ISSUE RUNTIME AUTHORITY
→ OPERATE
```

The persisted lifecycle projection is deliberately narrower:

```text
BUILT
→ APPROVED
→ DEPLOYED
→ OPERATING
```

Validation and evaluation are not persisted authority states. They produce qualifying evidence.

That distinction is intentional:

> **Evaluation is evidence. Approval is authority.**

A passing evaluation does not create approval. A successful backend call does not create DEPLOYED state. A deployed artifact does not become OPERATING until explicit runtime authority is issued.

---

## Core Invariants

1. An unvalidated specification cannot be built.
2. A built artifact cannot approve itself.
3. Failed evaluation cannot satisfy approval prerequisites.
4. Passing evaluation does not create approval.
5. Approval for one artifact cannot authorize another artifact.
6. Frozen deployment evidence cannot be rewritten by the execution caller.
7. Deployment authority is independently revalidated at execution time.
8. Failed deployment cannot falsely create DEPLOYED state.
9. External deployment reality must be verified before DEPLOYED is recorded.
10. Deployment cannot implicitly create runtime authority.
11. Runtime policy sets a ceiling; the issued grant defines actual authority.
12. Tool availability does not imply tool permission.
13. Denied actions remain durable evidence.
14. Historical failures are not erased by later success.
15. The system may be conservatively behind reality; it must never be optimistically ahead of reality.

---

## Trust Boundaries

### 1. Specification Boundary

`AgentSpecification` expresses requested purpose, tools, and target environment.

A specification is intent, not authority.

`SpecificationValidationService` checks that intent against deterministic `SpecificationPolicy` rules before construction is allowed.

### 2. Build Boundary

`AgentBuildService` produces an immutable, content-addressed `AgentArtifact` only from qualifying validated input.

The artifact digest is part of the authority model. Evaluation, approval, deployment, and runtime evidence bind to that exact identity.

A materially different artifact requires new evidence.

### 3. Evaluation Boundary

`EvaluationService` produces `EvaluationAttempt` evidence under an `EvaluationPolicy`.

Evaluation can pass or fail.

Neither outcome creates approval.

A failed attempt remains part of history even if a later attempt succeeds.

### 4. Approval Boundary

`ApprovalService` records a `HumanApproval` only when qualifying evaluation evidence exists for the exact artifact and policy context.

The approval binds to artifact identity, evaluation evidence, evaluation policy, target environment, and approver provenance.

`approver_id` is provenance metadata. Human authentication is external to Agent Foundry v1.

### 5. Frozen Intent Boundary

`DeploymentManifestService` freezes the complete reviewed deployment package into a canonical `DeploymentManifest`.

The manifest binds:

- exact artifact
- evaluation policy
- evaluation evidence
- deployment policy
- target environment
- approval digest

Execution later begins from the manifest digest only.

> **The caller chooses which frozen package to execute. It does not get to redefine what that package contains.**

A manifest preserves reviewed intent. It does not manufacture execution authority.

### 6. Deployment Boundary

`DeploymentExecutor` recovers the frozen manifest and delegates authority decisions to `DeploymentService`.

`DeploymentService` independently retrieves authoritative approval evidence and revalidates the artifact, environment, deployment policy, evaluation evidence, evaluation policy, approval, and lifecycle prerequisites.

The executor does not catch, reinterpret, retry, or broaden authority failures.

The backend may act only after these checks succeed.

### 7. External Reality Boundary

`S3DeploymentBackend` writes canonical artifact bytes to the bounded DEV S3 prefix, reads the object back, and requires an exact byte match.

The lifecycle does not advance because `PutObject` returned successfully.

It advances only after externally observable state has been verified and a successful `DeploymentAttempt` has been persisted.

> **A successful API call is not deployment evidence. Verified external state is deployment evidence.**

### 8. Runtime Authority Boundary

`RuntimeAuthorizationService` requires authoritative successful `DeploymentAttempt` evidence by exact digest.

It verifies the deployment matches the artifact and environment, checks the runtime policy, checks the requested permission set, requires at least DEPLOYED lifecycle state, and then creates a `RuntimeGrant`.

The grant is persisted before the lifecycle transition to OPERATING is recorded.

`grantor_id` is provenance metadata. Authentication of the grantor is external to Agent Foundry v1.

### 9. Tool Authorization Boundary

`ToolAuthorizationService` deterministically compares a `ToolRequest` against both the issued `RuntimeGrant` and the current `RuntimePolicy`.

Possessing a tool is not permission to invoke it.

Policy permission is also not enough by itself.

The live proof intentionally used:

```text
RuntimePolicy:
    files/read
    search/query

RuntimeGrant:
    files/read

Decisions:
    files/read    → ALLOW
    search/query  → DENY
```

The denial proves the distinction between a policy ceiling and authority actually issued.

> **Policy defines the ceiling. The grant defines the authority actually issued.**

---

## Evidence Before State

Lifecycle state describes the highest authorized stage successfully reached.

Attempts describe what actually happened while trying to advance or operate.

The ordering rule is:

```text
perform consequential operation
→ preserve evidence
→ advance lifecycle only if the evidence authorizes it
```

Examples:

```text
BUILT
→ EvaluationAttempt FAIL
→ remains BUILT
```

```text
APPROVED
→ DeploymentAttempt FAIL
→ remains APPROVED
```

```text
DEPLOYED
→ RuntimeGrant persistence fails
→ remains DEPLOYED
```

```text
OPERATING
→ ToolAuthorizationDecision DENY
→ remains OPERATING
```

Failures are evidence, not states to hide.

---

## Canonical Evidence Model

SQLite is the trusted local persistence boundary for the v1 governance proof.

The store persists explicit record types including:

- `HumanApproval`
- `DeploymentManifest`
- `DeploymentAttempt`
- `RuntimeGrant`
- `ToolAuthorizationDecision`
- `LifecycleTransition`

Records are immutable dataclasses with content-derived digests.

On read, stored payloads are reconstructed into canonical domain types and revalidated against both their digest and canonical serialized form.

This rejects corrupted, malformed, lossy, or noncanonical evidence instead of silently normalizing it.

> **Compatible data is not necessarily canonical evidence.**

SQLite is not an identity provider. Trust in provenance depends on controlled write access to the evidence store and externally authenticated identities.

---

## Authority Model

AI components may:

- interpret requests
- propose specifications
- construct candidate material
- perform bounded evaluation work
- explain evidence
- recommend next actions

AI components may not:

- validate themselves against policy
- approve themselves
- change policy to permit themselves
- promote themselves
- expand their own permissions
- grant themselves runtime authority
- bypass tool authorization
- fabricate evidence

Externally authenticated actors may supply approval or grantor provenance.

Deterministic services remain responsible for enforcing system invariants even after an approval exists.

---

## Identity, Resource, and Authority

AWS infrastructure is intentionally split into independent roots so three facts remain distinct:

```text
identity ≠ resource ≠ authority
```

The identity root creates exact GitHub OIDC trust for separate DEV, TEST, and PROD role identities.

The deployment-artifact root creates the S3 resource boundary without granting anyone authority to use it.

The deployment-authority root grants the DEV role only the minimum object permissions required by the live proof:

```text
s3:GetObject
s3:PutObject
```

on:

```text
arn:aws:s3:::agent-foundry-deployment-artifacts-276713393004-us-east-1/dev/*
```

DEV has no bucket listing, deletion, IAM, Terraform-state, TEST-prefix, or PROD-prefix authority.

TEST and PROD remain permissionless for deployment.

Live negative tests proved that intended DEV Put/Get operations succeed while out-of-scope operations fail with `AccessDenied`.

---

## Terraform and State

Terraform is used to express and review infrastructure authority, not to increase apparent complexity.

The implemented roots are:

- `infra/bootstrap` — hardened S3 Terraform state boundary
- `infra/identity` — GitHub OIDC provider and isolated environment role identities
- `infra/deployment-artifacts` — hardened deployment artifact bucket
- `infra/deployment-authority` — narrowly scoped DEV object authority

Remote Terraform state is stored in S3 with native S3 locking.

Terraform plans are treated as reviewed consequences. A saved plan freezes the intended change that was reviewed; it does not freeze external reality, and it may still become stale or fail at apply time.

Infrastructure configuration never embeds static AWS credentials.

---

## CI/CD Authority Sequencing

The live GitHub Actions workflow contains two separate jobs.

The validation job has only repository read permission and cannot request an OIDC token.

Only after validation succeeds does the live-proof job become eligible to request GitHub OIDC identity for the DEV environment.

```text
VALIDATE
contents: read
no id-token authority
    ↓
399 tests pass
    ↓
LIVE-PROOF
DEV environment
id-token: write
    ↓
AWS verifies exact trust
    ↓
bounded DEV role session
    ↓
governed deployment + runtime proof
```

This avoids a subtle but important failure mode: merely placing credential acquisition after tests inside the same already-authorized job would sequence operations without sequencing authority.

> **Code must prove itself before it becomes eligible to request deployment identity.**

> **Ordering operations is not enough. Authority itself must be sequenced.**

---

## AWS Composition

The domain and service layers remain cloud-agnostic.

AWS-specific wiring lives at the edge in `composition/aws.py` and the S3 adapter.

`boto3` is the mechanism used by Python to call AWS. It does not define governance intent.

The separation is:

```text
Agent Foundry governance → decides what may happen
boto3 / S3 adapter       → performs the bounded mechanism
AWS IAM                  → enforces cloud authority
```

Credentials are obtained from the ambient AWS credential provider chain. The application does not embed static keys.

---

## Live DEV Proof

The final proof is intentionally manual via `workflow_dispatch`.

The workflow:

1. checks out the exact `main` commit
2. installs pinned tooling and frozen dependencies
3. runs the complete test suite before federation
4. assumes the bounded DEV role through GitHub OIDC
5. asserts the expected AWS account and role identity
6. creates and freezes local governance evidence
7. closes the writer store
8. reopens SQLite through a fresh store
9. executes only by deployment manifest digest
10. independently recovers approval and deployment evidence
11. writes the canonical artifact to real S3
12. reads it back and verifies exact bytes
13. records successful deployment evidence and reaches DEPLOYED
14. independently retrieves successful deployment evidence
15. issues a narrower RuntimeGrant and reaches OPERATING
16. proves one tool request ALLOW and one policy-permitted-but-ungranted request DENY
17. emits non-secret digest-based proof

Final run: `35525913401`

Final state: `OPERATING`

---

## Failure Semantics

Unexpected failures are not converted into optimistic state.

Key examples:

- backend deployment failure records FAIL and leaves lifecycle APPROVED
- byte mismatch after S3 read-back is deployment failure
- missing authoritative approval blocks deployment
- missing authoritative deployment evidence blocks runtime grant issuance
- runtime-grant persistence failure leaves lifecycle DEPLOYED
- ungranted tool requests produce DENY evidence
- corrupted persistence records are rejected
- unexpected exceptions propagate or produce sanitized verification failure rather than false success

The governing rule is:

> **The system may be conservatively behind reality. It must never be optimistically ahead of reality.**

---

## What v1 Does Not Claim

Agent Foundry v1 is intentionally not:

- an autonomous production-promotion system
- a production-scale distributed governance database
- an internal human identity provider
- a persistent agent runtime
- a Kubernetes platform
- a generic framework for every model provider
- a proof of maximum agent scale
- an unrestricted self-modifying agent system

`OPERATING` means explicit runtime authority has been issued and deterministic tool authorization is enforceable.

It does not mean a persistent EC2, Lambda, container, or Kubernetes workload is running.

This boundary is intentional.

---

## Engineering Standard

The implementation follows one constraint above all others:

> **Prefer the smallest architecture that proves the required boundary.**

No component exists merely for service bingo.

Every major component has one responsibility.

Every consequential action has an authority source.

Every important claim has evidence.

That is the architecture.
