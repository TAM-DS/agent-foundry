# Agent Foundry Architecture

## Purpose

Agent Foundry is a governed system for creating, evaluating, approving, deploying, and operating autonomous agents.

Its central design question is:

> How do you scale autonomy without scaling risk at the same rate?

The system separates autonomous capability from authority.

Creating an agent does not authorize that agent to operate.

Building an artifact does not approve it.

Passing an evaluation does not approve it.

Deployment does not grant unrestricted runtime authority.

Tool availability does not imply permission to invoke a tool.

---

## Design Thesis

Agent Foundry treats an autonomous agent as a governed artifact moving through explicit trust boundaries.

The lifecycle is:

REQUESTED
→ VALIDATED
→ BUILT
→ APPROVED
→ DEPLOYED
→ OPERATING

Evaluation is intentionally not a lifecycle authority state.

Evaluation produces evidence.

Approval consumes that evidence and grants authority to advance.

Likewise, failed deployment attempts and rejected runtime actions produce evidence without falsely advancing lifecycle state.

---

## Trust Boundaries

The system has six major trust boundaries.

### 1. Request Boundary

A request describes desired capability.

It is not authoritative.

A request may ask for:

- a purpose
- a model
- tools
- permissions
- an environment
- runtime behavior

Requested capability must still be validated against policy.

### 2. Specification Boundary

A validated specification defines what may be built.

Validation is deterministic.

A model may help interpret or construct a specification, but it may not decide that its own specification satisfies policy.

### 3. Build Boundary

A validated specification may produce a candidate agent artifact.

The built artifact must be immutable or content-addressable for governance purposes.

Evaluation and approval apply to the exact artifact that was built.

Changing the artifact invalidates prior evaluation and approval evidence for that artifact.

### 4. Evaluation Boundary

Evaluation determines whether a specific artifact meets declared criteria.

Evaluation does not grant deployment authority.

Evaluation attempts are append-only evidence.

A failed attempt remains part of history even if a later attempt passes.

### 5. Deployment Boundary

Only an explicitly approved artifact may be eligible for deployment.

Approval must bind to the exact artifact and relevant policy evidence.

Deployment must not silently substitute a different artifact.

A failed deployment attempt does not convert an APPROVED agent into DEPLOYED.

### 6. Runtime Boundary

Deployment means that software exists in an environment.

It does not mean that software has unlimited authority to act.

Runtime authority must be explicit and bounded by:

- environment
- agent identity
- artifact identity
- permitted tools
- permitted actions
- policy
- credential scope

---

## Lifecycle

### REQUESTED

A desired agent capability has been submitted.

No construction or runtime authority exists.

### VALIDATED

The requested specification satisfies deterministic schema and policy requirements.

Validation means:

> this specification is permitted to proceed to construction.

It does not mean:

> this agent is safe to deploy.

### BUILT

A candidate artifact has been constructed from the validated specification.

The artifact receives a stable identity or digest.

The artifact may be evaluated.

It has no deployment authority.

### APPROVED

An authorized human has approved a specific artifact after required evaluation evidence exists.

Approval binds to:

- exact artifact identity
- evaluation evidence
- policy context
- approver identity metadata
- target promotion context

Approval is not transferable to a modified artifact.

### DEPLOYED

The exact approved artifact has been successfully deployed into a declared environment.

Deployment evidence records what was actually deployed.

Deployment does not imply unrestricted runtime authority.

### OPERATING

A deployed artifact has received bounded runtime authority.

Its actions remain subject to deterministic policy enforcement.

---

## Attempts Versus State

Lifecycle state describes the highest authorized stage successfully reached.

Attempts describe what happened while trying to advance or operate.

Examples:

A failed evaluation:

BUILT
→ evaluation attempt FAIL
→ remains BUILT

A later successful evaluation:

BUILT
→ evaluation attempt PASS
→ remains BUILT until approval

A failed deployment:

APPROVED
→ deployment attempt FAIL
→ remains APPROVED

A successful deployment:

APPROVED
→ deployment attempt SUCCESS
→ DEPLOYED

A denied tool invocation:

OPERATING
→ tool request DENIED
→ remains OPERATING
→ denial evidence preserved

Failures must never be erased merely because a later attempt succeeds.

---

## Core Domain Records

### AgentRequest

Represents requested capability.

Contains the user's or system's intent before authority validation.

### AgentSpecification

Represents the normalized specification that may be validated.

Examples of governed fields include:

- purpose
- model policy
- requested tools
- requested permissions
- target environment
- behavioral constraints
- evaluation requirements

### AgentArtifact

Represents the exact built candidate.

Must have a stable artifact identifier or digest.

Evaluation, approval, and deployment evidence must reference this identity.

### EvaluationAttempt

Append-only evidence describing an evaluation of one exact artifact.

Records:

- artifact identity
- evaluation policy/version
- checks performed
- outcome
- evidence
- timestamp

### HumanApproval

Records explicit approval for one exact artifact.

Approval must not authorize a different artifact.

### DeploymentAttempt

Append-only evidence describing an attempt to deploy one exact approved artifact.

Records both success and failure truthfully.

### RuntimeGrant

Represents bounded authority for a deployed agent to operate.

A runtime grant may constrain:

- environment
- tools
- actions
- credential scope
- expiration
- policy version

### ToolInvocationAudit

Records consequential runtime tool requests and their outcomes.

A tool denial is evidence, not an error to hide.

---

## Core Services

### SpecificationValidationService

Deterministically validates an AgentSpecification against declared policy.

The model cannot override this service.

### AgentBuildService

Constructs a candidate artifact only from a validated specification.

Construction does not grant approval.

### EvaluationService

Runs required evaluations against an exact artifact.

Produces append-only EvaluationAttempt evidence.

The service may use deterministic and bounded model-assisted evaluators.

Evaluation output cannot approve the artifact.

### ApprovalService

Records explicit human approval.

Approval requires qualifying evaluation evidence.

Approval binds to the exact artifact.

### DeploymentService

Deploys only an exact approved artifact.

Before deployment it verifies:

- approval exists
- approval matches artifact
- required policy still permits deployment
- target environment is authorized

Deployment attempts produce durable evidence.

### RuntimeAuthorizationService

Issues bounded runtime authority to an eligible deployed artifact.

Deployment alone cannot create unlimited runtime authority.

### ToolAuthorizationService

Deterministically decides whether a requested runtime tool invocation is permitted.

The agent may request a tool invocation.

The authorization layer decides whether the request may proceed.

---

## Authority Model

AI may:

- interpret requests
- propose specifications
- construct candidate artifacts
- perform bounded evaluation work
- explain results
- recommend next actions

AI may not:

- validate itself against policy
- approve itself
- change policy to permit itself
- promote itself
- expand its own permissions
- grant itself runtime authority
- bypass tool authorization
- fabricate evidence

Humans may authorize consequential promotion decisions.

Deterministic services enforce system policy even after human approval.

Approval permits a transition to be attempted.

Approval does not disable deterministic validation.

---

## Artifact Integrity

Evaluation and approval apply to an exact artifact.

Therefore:

artifact A
→ evaluated
→ approved

must never silently become:

artifact B
→ deployed using artifact A's approval

Any material artifact change requires new qualifying evidence.

Artifact identity is therefore part of the authority model, not merely build metadata.

---

## Policy Integrity

Evidence should identify the policy context under which a decision was made.

Where practical, consequential evidence should retain a stable policy version or digest.

This allows the system to answer:

> Which rules were in force when this decision occurred?

A later policy change must not rewrite historical truth.

---

## Tool Governance

Possessing a tool and having authority to use a tool are different facts.

Tool authorization must consider the active runtime grant and policy.

An operating agent must not be able to:

- add itself to an allowlist
- broaden credential scope
- invoke undeclared tools
- reinterpret denial as permission
- alter the policy responsible for authorizing its action

---

## Environment Model

Agent Foundry treats:

DEV
TEST
PROD

as separate trust boundaries.

Promotion must be explicit.

The same artifact identity should be traceable through promotion where practical.

Production must not depend on undocumented developer-machine state.

---

## AWS and Terraform

The domain architecture must remain independent of one cloud provider.

AWS is the first deployment target.

Terraform will define the AWS infrastructure required by the architecture.

Terraform is not included for demonstration value alone.

It exists to prove:

- reproducible infrastructure
- reviewed infrastructure changes
- explicit identity and permission boundaries
- environment separation
- deterministic promotion
- infrastructure evidence

Exact AWS services will be selected only when required by the runtime architecture.

The project will not add AWS services merely to increase apparent complexity.

---

## CI/CD

GitHub Actions will provide the automated promotion path.

The intended direction is:

Repository
→ Tests
→ Evaluation Evidence
→ Approval Boundary
→ Terraform Plan
→ Controlled Apply
→ Deployment Verification
→ Evidence

AWS authentication from CI should use short-lived federated identity rather than committed long-lived credentials.

Environment promotion must preserve the distinction between:

- code exists
- tests pass
- evaluation passes
- approval exists
- infrastructure may change
- agent may operate

---

## Evidence Model

Every consequential event should make it possible to determine:

- what object was acted upon
- what artifact was involved
- what policy applied
- what evidence existed
- who or what requested the action
- who or what authorized it
- whether the attempt succeeded
- what environment was affected
- what permissions were granted
- what actually happened

Evidence should be append-only where practical.

Historical failures must remain visible.

---

## Governance Invariants

The implementation must prove at least these invariants:

1. An unvalidated specification cannot be built.

2. A built artifact cannot approve itself.

3. A failed evaluation cannot satisfy an approval prerequisite.

4. A passing evaluation does not itself create approval.

5. Approval for one artifact cannot authorize a different artifact.

6. A failed deployment does not falsely mark an agent DEPLOYED.

7. Deployment does not automatically grant unrestricted runtime authority.

8. Tool availability does not imply tool permission.

9. A denied runtime action cannot be converted into permission by model interpretation.

10. Historical failed attempts remain preserved after later success.

---

## Initial Scope

The first version will prove the governance model before optimizing scale.

Initial implementation should demonstrate:

- durable agent lifecycle state
- deterministic specification validation
- candidate artifact construction
- evaluation attempts
- explicit human approval
- artifact-bound approval
- controlled deployment boundary
- bounded runtime authority
- tool authorization
- durable evidence
- failure-path tests

AWS deployment and Terraform will be introduced after the local governance core proves these invariants.

---

## Non-Goals

The first version is not intended to prove:

- maximum agent count
- every model provider
- every AWS service
- arbitrary plugin ecosystems
- unrestricted self-modification
- autonomous production promotion
- a generic agent framework for every use case

The goal is narrower:

> prove that autonomous capability can be created and promoted without allowing capability to become authority by accident.
