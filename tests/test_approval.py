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
def test_explicit_approval_binds_exact_evidence_artifact_policy_and_environment(inputs, environment, governance_store):
    artifact, policy, evidence = inputs
    approval = ApprovalService(governance_store, governance_store).approve(artifact, policy, evidence, "human-1", environment)
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


def test_failed_evidence_cannot_authorize_approval(inputs, governance_store):
    artifact, policy, _ = inputs
    policy = replace(policy, expected_specification_digest="b" * 64)
    evidence = EvaluationService().evaluate(artifact, policy)
    with pytest.raises(ApprovalNotAuthorized, match="must be PASS"):
        ApprovalService(governance_store, governance_store).approve(artifact, policy, evidence, "human-1", Environment.DEV)
    assert evidence.outcome is EvaluationOutcome.FAIL


def test_evidence_for_artifact_a_cannot_authorize_artifact_b(inputs, governance_store):
    _, policy, evidence = inputs
    with pytest.raises(ApprovalNotAuthorized, match="match artifact"):
        ApprovalService(governance_store, governance_store).approve(AgentArtifact("b" * 64), policy, evidence, "human-1", Environment.DEV)


def test_evidence_for_policy_a_cannot_authorize_under_policy_b(inputs, governance_store):
    artifact, _, evidence = inputs
    with pytest.raises(ApprovalNotAuthorized, match="match policy"):
        ApprovalService(governance_store, governance_store).approve(artifact, EvaluationPolicy("b" * 64), evidence, "human-1", Environment.DEV)


@pytest.mark.parametrize("source", ["b" * 64, "malformed"])
def test_fabricated_pass_cannot_override_failure(inputs, source, governance_store):
    _, policy, _ = inputs
    artifact = AgentArtifact(source)
    failed = EvaluationService().evaluate(artifact, policy)
    fabricated = replace(failed, outcome=EvaluationOutcome.PASS, reasons=())
    with pytest.raises(ApprovalNotAuthorized, match="policy evaluation"):
        ApprovalService(governance_store, governance_store).approve(artifact, policy, fabricated, "human-1", Environment.DEV)


def test_altered_reasons_cannot_authorize_approval(inputs, governance_store):
    artifact, policy, evidence = inputs
    with pytest.raises(ApprovalNotAuthorized, match="policy evaluation"):
        ApprovalService(governance_store, governance_store).approve(
            artifact, policy, replace(evidence, reasons=("fabricated",)), "human-1", Environment.DEV,
        )


@pytest.mark.parametrize("identity", ["", " ", "\n\t", None, 42])
def test_invalid_approver_identity_cannot_authorize(inputs, identity, governance_store):
    with pytest.raises(ApprovalNotAuthorized, match="nonblank string"):
        ApprovalService(governance_store, governance_store).approve(*inputs, identity, Environment.DEV)


def test_missing_evidence_cannot_authorize(inputs, governance_store):
    artifact, policy, _ = inputs
    with pytest.raises(ApprovalNotAuthorized, match="evidence is required"):
        ApprovalService(governance_store, governance_store).approve(artifact, policy, None, "human-1", Environment.DEV)


def test_target_environment_requires_existing_enum(inputs, governance_store):
    with pytest.raises(ApprovalNotAuthorized, match="must be an Environment"):
        ApprovalService(governance_store, governance_store).approve(*inputs, "human-1", "DEV")


def test_approval_digest_is_canonical_and_deterministic(inputs, governance_store):
    from hashlib import sha256
    import json

    approval = ApprovalService(governance_store, governance_store).approve(*inputs, "human-é", Environment.TEST)
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
def test_approval_digest_binds_every_governed_field(inputs, changes, governance_store):
    approval = ApprovalService(governance_store, governance_store).approve(*inputs, "human-1", Environment.TEST)
    assert replace(approval, **changes).digest != approval.digest


@pytest.fixture
def governance_store(tmp_path):
    from agent_foundry.persistence import SQLiteGovernanceStore

    with SQLiteGovernanceStore(tmp_path / "evidence.sqlite") as store:
        from lifecycle_support import record_state

        record_state(store, AgentArtifact("a" * 64).digest)
        yield store
