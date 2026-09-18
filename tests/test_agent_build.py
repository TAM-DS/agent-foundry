from dataclasses import FrozenInstanceError, replace

import pytest

from agent_foundry.domain.specification import AgentSpecification, Environment
from agent_foundry.services.agent_build import AgentBuildService, BuildNotAuthorized
from agent_foundry.services.specification_validation import (
    SpecificationPolicy, SpecificationValidationService, ValidationOutcome,
)


@pytest.fixture
def inputs():
    specification = AgentSpecification("Summarize", ("read",), Environment.DEV)
    policy = SpecificationPolicy({"read"}, {Environment.DEV})
    evidence = SpecificationValidationService().validate(specification, policy)
    return specification, policy, evidence


def test_validated_specification_builds_stable_immutable_artifact(inputs):
    specification, policy, evidence = inputs
    artifact = AgentBuildService().build(specification, policy, evidence)
    assert artifact.source_specification_digest == specification.digest
    assert artifact == AgentBuildService().build(specification, policy, evidence)
    assert artifact.digest == AgentBuildService().build(specification, policy, evidence).digest
    assert len(artifact.digest) == 64
    with pytest.raises(FrozenInstanceError):
        artifact.source_specification_digest = "changed"


def test_missing_evidence_cannot_authorize_build(inputs):
    specification, policy, _ = inputs
    with pytest.raises(BuildNotAuthorized, match="evidence is required"):
        AgentBuildService().build(specification, policy)
    with pytest.raises(BuildNotAuthorized, match="evidence is required"):
        AgentBuildService().build(specification, policy, None)


def test_failed_evidence_cannot_authorize_build(inputs):
    specification, policy, _ = inputs
    invalid = replace(specification, requested_tools=("write",))
    failed = SpecificationValidationService().validate(invalid, policy)
    with pytest.raises(BuildNotAuthorized, match="must be PASS"):
        AgentBuildService().build(invalid, policy, failed)


@pytest.mark.parametrize("changes", [
    {"purpose": "Different purpose"},
    {"requested_tools": ()},
    {"target_environment": Environment.TEST},
])
def test_evidence_cannot_authorize_different_specification(inputs, changes):
    specification, policy, evidence = inputs
    with pytest.raises(BuildNotAuthorized, match="match specification"):
        AgentBuildService().build(replace(specification, **changes), policy, evidence)


@pytest.mark.parametrize("changes", [
    {"allowed_tools": {"read", "search"}},
    {"allowed_environments": {Environment.DEV, Environment.TEST}},
])
def test_evidence_cannot_authorize_different_policy(inputs, changes):
    specification, policy, evidence = inputs
    with pytest.raises(BuildNotAuthorized, match="match policy"):
        AgentBuildService().build(specification, replace(policy, **changes), evidence)


def test_fabricated_pass_cannot_override_failed_policy(inputs):
    specification, policy, _ = inputs
    invalid = replace(specification, requested_tools=("write",))
    failed = SpecificationValidationService().validate(invalid, policy)
    forged = replace(failed, outcome=ValidationOutcome.PASS, reasons=())
    with pytest.raises(BuildNotAuthorized, match="policy evaluation"):
        AgentBuildService().build(invalid, policy, forged)


def test_different_valid_specification_has_different_artifact_digest(inputs):
    specification, policy, evidence = inputs
    changed = replace(specification, purpose="Another purpose")
    changed_evidence = SpecificationValidationService().validate(changed, policy)
    service = AgentBuildService()
    assert service.build(specification, policy, evidence).digest != service.build(
        changed, policy, changed_evidence,
    ).digest
