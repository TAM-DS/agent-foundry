# Agent Foundry Engineering Contract

## Governing Principle

Creating an agent does not authorize that agent to operate.

Agent Foundry separates the creation of autonomous capability from the authority to use that capability.

An agent may be correctly specified, successfully built, and technically functional while still lacking permission to operate.

## Authority Model

Agent Foundry treats these as separate concerns:

- specification
- validation
- construction
- evaluation
- approval
- deployment
- runtime authority

No earlier stage implicitly grants authority to a later stage.

Passing an evaluation does not approve deployment.

Deployment does not grant unrestricted runtime authority.

Possessing a tool does not imply permission to use it in every context.

## AI May

AI components may:

- interpret an agent specification
- propose an implementation
- construct candidate agent artifacts
- explain reasoning and design choices
- execute bounded evaluations
- summarize evaluation evidence
- recommend whether a candidate appears ready for review

## AI May Not

AI components may not:

- approve themselves for deployment
- promote themselves between environments
- grant themselves additional tools or permissions
- modify production policy to permit their own action
- bypass deterministic validation
- fabricate evaluation or deployment evidence
- treat successful construction as authorization to operate
- treat successful deployment as unlimited runtime authority

## Consequential Transitions

Consequential lifecycle transitions must pass through deterministic services or explicitly authorized human decisions.

The intended lifecycle is:

REQUESTED
→ VALIDATED
→ BUILT
→ EVALUATED
→ APPROVED
→ DEPLOYED
→ OPERATING

A failed validation or evaluation must not be converted into success by model interpretation.

A blocked transition remains blocked until the required evidence or authority changes.

## Evidence

Every consequential transition must produce durable evidence sufficient to answer:

1. What was requested?
2. What policy applied?
3. What was evaluated?
4. What passed or failed?
5. Who or what authorized the transition?
6. What artifact was deployed?
7. What permissions were granted?
8. What actually happened?

Evidence is not decoration. It is part of the system's trust model.

## Tool Authority

Tool access must be explicit and bounded.

An agent must not:

- infer permission from tool availability
- expand its own tool set
- reuse credentials outside their intended scope
- escalate runtime privileges
- invoke tools outside declared policy

Tool availability and tool authority are distinct.

## Environment Promotion

Development, test, and production are separate trust boundaries.

Promotion between environments must be explicit, reproducible, and evidenced.

Infrastructure changes must be defined through reviewed infrastructure-as-code where practical.

Production deployment must not depend on undocumented local state.

## Failure Semantics

Failure must remain truthful.

The system must preserve:

- failed validation
- failed evaluation
- denied approval
- blocked deployment
- runtime rejection

A failed attempt must not be rewritten as success merely because a later attempt succeeds.

## Testing

Tests must prove governance invariants, not only happy-path functionality.

Important boundaries must have failure tests.

Tests must not rely on uncontrolled external network access unless the test is explicitly an integration test.

## Secrets

Never commit:

- API keys
- AWS credentials
- access tokens
- private keys
- production secrets

Local and CI credentials must enter through approved secret-management mechanisms.

## Engineering Standard

Prefer the smallest architecture that proves the required boundary.

Do not add abstractions, cloud services, frameworks, or agents merely to increase apparent complexity.

Every major component must have a clear responsibility.

Every consequential action must have a clear authority source.

Every important claim must have evidence.
