from dataclasses import FrozenInstanceError, replace
from unittest.mock import patch

import pytest

from agent_foundry.domain.approval import HumanApproval
from agent_foundry.domain.artifact import AgentArtifact
from agent_foundry.domain.evaluation import EvaluationAttempt, EvaluationOutcome, EvaluationPolicy
from agent_foundry.domain.specification import Environment
from agent_foundry.services.approval import ApprovalNotAuthorized, ApprovalService
from agent_foundry.services.evaluation import EvaluationService


@pytest.fixture
def inputs():
    artifact = AgentArtifact("a" * 64)
    policy = EvaluationPolicy("a" * 64)
    return artifact, policy, EvaluationService().evaluate(artifact, policy)


@pytest.mark.parametrize("environment", list(Environment))
def test_explicit_approval_binds_exact_evidence_artifact_policy_and_environment(inputs, environment):
    artifact, policy, evidence = inputs
    approval = ApprovalService().approve(artifact, policy, evidence, "human-1", environment)
    assert isinstance(approval, HumanApproval)
    assert approval.artifact_digest == artifact.digest
    assert approval.evaluation_evidence_digest == evidence.digest
    assert approval.evaluation_policy_digest == policy.digest
    assert approval.approver_id == "human-1"
    assert approval.target_environment is environment
    for field in approval.__dataclass_fields__:
        with pytest.raises(FrozenInstanceError):
            setattr(approval, field, "changed")


def test_pass_evidence_alone_does_not_create_approval(inputs):
    artifact, policy, _ = inputs
    with patch.object(ApprovalService, "approve", side_effect=AssertionError("Implicit approval")):
        with patch("agent_foundry.services.approval.HumanApproval") as constructor:
            evidence = EvaluationService().evaluate(artifact, policy)
            constructor.assert_not_called()
    assert isinstance(evidence, EvaluationAttempt)
    assert not isinstance(evidence, HumanApproval)
    assert evidence.outcome is EvaluationOutcome.PASS
    assert artifact == AgentArtifact("a" * 64)


def test_failed_evidence_cannot_authorize_approval(inputs):
    artifact, policy, _ = inputs
    policy = replace(policy, expected_specification_digest="b" * 64)
    evidence = EvaluationService().evaluate(artifact, policy)
    with pytest.raises(ApprovalNotAuthorized, match="must be PASS"):
        ApprovalService().approve(artifact, policy, evidence, "human-1", Environment.DEV)
    assert evidence.outcome is EvaluationOutcome.FAIL


def test_evidence_for_artifact_a_cannot_authorize_artifact_b(inputs):
    _, policy, evidence = inputs
    with pytest.raises(ApprovalNotAuthorized, match="match artifact"):
        ApprovalService().approve(AgentArtifact("b" * 64), policy, evidence, "human-1", Environment.DEV)


def test_evidence_for_policy_a_cannot_authorize_under_policy_b(inputs):
    artifact, _, evidence = inputs
    with pytest.raises(ApprovalNotAuthorized, match="match policy"):
        ApprovalService().approve(artifact, EvaluationPolicy("b" * 64), evidence, "human-1", Environment.DEV)


@pytest.mark.parametrize("source", ["b" * 64, "malformed"])
def test_fabricated_pass_cannot_override_failure(inputs, source):
    _, policy, _ = inputs
    artifact = AgentArtifact(source)
    failed = EvaluationService().evaluate(artifact, policy)
    fabricated = replace(failed, outcome=EvaluationOutcome.PASS, reasons=())
    with pytest.raises(ApprovalNotAuthorized, match="policy evaluation"):
        ApprovalService().approve(artifact, policy, fabricated, "human-1", Environment.DEV)


def test_altered_reasons_cannot_authorize_approval(inputs):
    artifact, policy, evidence = inputs
    with pytest.raises(ApprovalNotAuthorized, match="policy evaluation"):
        ApprovalService().approve(
            artifact, policy, replace(evidence, reasons=("fabricated",)), "human-1", Environment.DEV,
        )


@pytest.mark.parametrize("identity", ["", " ", "\n\t", None, 42])
def test_invalid_approver_identity_cannot_authorize(inputs, identity):
    with pytest.raises(ApprovalNotAuthorized, match="nonblank string"):
        ApprovalService().approve(*inputs, identity, Environment.DEV)


def test_missing_evidence_cannot_authorize(inputs):
    artifact, policy, _ = inputs
    with pytest.raises(ApprovalNotAuthorized, match="evidence is required"):
        ApprovalService().approve(artifact, policy, None, "human-1", Environment.DEV)


def test_target_environment_requires_existing_enum(inputs):
    with pytest.raises(ApprovalNotAuthorized, match="must be an Environment"):
        ApprovalService().approve(*inputs, "human-1", "DEV")


def test_approval_digest_is_canonical_and_deterministic(inputs):
    from hashlib import sha256
    import json

    approval = ApprovalService().approve(*inputs, "human-é", Environment.TEST)
    canonical = json.dumps({
        "artifact_digest": approval.artifact_digest,
        "evaluation_evidence_digest": approval.evaluation_evidence_digest,
        "evaluation_policy_digest": approval.evaluation_policy_digest,
        "approver_id": "human-é",
        "target_environment": "TEST",
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert approval.digest == replace(approval).digest == sha256(canonical.encode()).hexdigest()


@pytest.mark.parametrize("changes", [
    {"artifact_digest": "b" * 64},
    {"evaluation_evidence_digest": "b" * 64},
    {"evaluation_policy_digest": "b" * 64},
    {"approver_id": "human-2"},
    {"target_environment": Environment.PROD},
])
def test_approval_digest_binds_every_governed_field(inputs, changes):
    approval = ApprovalService().approve(*inputs, "human-1", Environment.TEST)
    assert replace(approval, **changes).digest != approval.digest
