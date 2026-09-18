from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import json

import pytest

from agent_foundry.domain.approval import HumanApproval
from agent_foundry.domain.artifact import AgentArtifact
from agent_foundry.domain.deployment import DeploymentAttempt, DeploymentOutcome, DeploymentPolicy
from agent_foundry.domain.evaluation import EvaluationOutcome, EvaluationPolicy
from agent_foundry.domain.specification import Environment
from agent_foundry.services.approval import ApprovalService
from agent_foundry.services.deployment import (
    DeploymentBackendError, DeploymentNotAuthorized, DeploymentService,
)
from agent_foundry.services.evaluation import EvaluationService


from test_approval import governance_store


class ApprovalSource:
    def __init__(self):
        self.records = {}

    def get_approval(self, digest):
        return self.records.get(digest)

    def save_approval(self, approval):
        self.records[approval.digest] = approval


@pytest.fixture
def approval_store():
    return ApprovalSource()


class FakeBackend:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def deploy(self, artifact, target_environment):
        self.calls.append((artifact, target_environment))
        if self.error is not None:
            raise self.error


@pytest.fixture
def inputs(approval_store, governance_store):
    artifact = AgentArtifact("a" * 64)
    evaluation_policy = EvaluationPolicy(artifact.source_specification_digest)
    evidence = EvaluationService().evaluate(artifact, evaluation_policy)
    approval = ApprovalService(approval_store, governance_store).approve(
        artifact, evaluation_policy, evidence, "human-1", Environment.TEST,
    )
    return artifact, DeploymentPolicy({Environment.TEST, Environment.PROD}), approval, {
        "evaluation_policy": evaluation_policy, "evaluation_evidence": evidence,
    }


def test_policy_is_immutable_canonical_and_copies_input():
    environments = [Environment.TEST, Environment.DEV, Environment.TEST]
    policy = DeploymentPolicy(environments)
    environments.append(Environment.PROD)
    reordered = DeploymentPolicy([Environment.DEV, Environment.TEST])
    assert policy.allowed_environments == frozenset({Environment.DEV, Environment.TEST})
    assert policy == reordered
    assert policy.digest == reordered.digest == sha256(
        b'{"allowed_environments":["DEV","TEST"]}'
    ).hexdigest()
    assert replace(policy, allowed_environments={Environment.PROD}).digest != policy.digest
    with pytest.raises(FrozenInstanceError):
        policy.allowed_environments = frozenset()


def test_success_binds_exact_inputs_and_calls_backend_once(inputs, governance_store, approval_store):
    artifact, policy, approval, upstream = inputs
    backend = FakeBackend()
    assert backend.calls == []
    attempt = DeploymentService(backend, approval_store, governance_store, governance_store).deploy(artifact, policy, Environment.TEST, approval.digest, **upstream)
    assert backend.calls == [(artifact, Environment.TEST)]
    assert backend.calls[0][0] is artifact
    assert attempt.outcome is DeploymentOutcome.SUCCESS
    assert attempt.reasons == ()
    assert attempt.artifact_digest == artifact.digest
    assert attempt.approval_digest == approval.digest
    assert attempt.deployment_policy_digest == policy.digest
    assert attempt.target_environment is Environment.TEST
    canonical = json.dumps({
        "artifact_digest": artifact.digest,
        "approval_digest": approval.digest,
        "deployment_policy_digest": policy.digest,
        "target_environment": "TEST",
        "outcome": "SUCCESS",
        "reasons": [],
    }, sort_keys=True, separators=(",", ":"))
    assert attempt.digest == replace(attempt).digest == sha256(canonical.encode()).hexdigest()


@pytest.mark.parametrize("changes", [
    {"artifact_digest": "b" * 64},
    {"approval_digest": "b" * 64},
    {"deployment_policy_digest": "b" * 64},
    {"target_environment": Environment.PROD},
    {"outcome": DeploymentOutcome.SUCCESS},
    {"reasons": ("changed",)},
])
def test_attempt_is_immutable_and_digest_binds_every_field(changes):
    reasons = ["failure"]
    attempt = DeploymentAttempt(
        "a" * 64, "c" * 64, "d" * 64, Environment.TEST, DeploymentOutcome.FAIL, reasons,
    )
    digest = attempt.digest
    reasons.append("changed")
    assert attempt.reasons == ("failure",)
    assert attempt.digest == digest
    assert replace(attempt, **changes).digest != digest
    for field, value in changes.items():
        with pytest.raises(FrozenInstanceError):
            setattr(attempt, field, value)


@pytest.mark.parametrize("approval", [None, object(), {}, "approved"])
def test_invalid_approval_never_calls_backend(inputs, approval, governance_store, approval_store):
    artifact, policy, _, upstream = inputs
    backend = FakeBackend()
    with pytest.raises(DeploymentNotAuthorized, match="Human approval is required"):
        DeploymentService(backend, approval_store, governance_store, governance_store).deploy(artifact, policy, Environment.TEST, approval, **upstream)
    assert backend.calls == []


def test_missing_approval_never_calls_backend(inputs, governance_store, approval_store):
    artifact, policy, _, upstream = inputs
    backend = FakeBackend()
    with pytest.raises(DeploymentNotAuthorized, match="Human approval is required"):
        DeploymentService(backend, approval_store, governance_store, governance_store).deploy(artifact, policy, Environment.TEST, **upstream)
    assert backend.calls == []


@pytest.mark.parametrize(("change", "reason"), [
    ("artifact", "match artifact"),
    ("environment", "match target environment"),
    ("policy", "not allowed"),
    ("invalid_environment", "must be an Environment"),
    ("digest", "digest does not match"),
])
def test_authorization_failure_never_calls_backend(inputs, change, reason, governance_store, approval_store):
    artifact, policy, approval, upstream = inputs
    environment = Environment.TEST
    if change == "artifact":
        artifact = AgentArtifact("b" * 64)
    elif change == "environment":
        environment = Environment.PROD
    elif change == "policy":
        policy = DeploymentPolicy({Environment.PROD})
    elif change == "invalid_environment":
        environment = "TEST"
    elif change == "digest":
        class IncorrectDigestApproval(HumanApproval):
            @property
            def digest(self):
                return "0" * 64

        approval = IncorrectDigestApproval(
            approval.artifact_digest, approval.evaluation_evidence_digest,
            approval.evaluation_policy_digest, approval.approver_id, approval.target_environment,
        )
    approval_store.save_approval(approval)
    backend = FakeBackend()
    with pytest.raises(DeploymentNotAuthorized, match=reason):
        DeploymentService(backend, approval_store, governance_store, governance_store).deploy(artifact, policy, environment, approval.digest, **upstream)
    assert backend.calls == []


def test_known_failure_is_evidence_and_later_success_preserves_it(inputs, governance_store, approval_store):
    artifact, policy, approval, upstream = inputs
    backend = FakeBackend(DeploymentBackendError("target unavailable"))
    service = DeploymentService(backend, approval_store, governance_store, governance_store)
    failed = service.deploy(artifact, policy, Environment.TEST, approval.digest, **upstream)
    original = replace(failed)
    digest = failed.digest
    assert len(backend.calls) == 1
    assert failed.outcome is DeploymentOutcome.FAIL
    assert failed.reasons == ("Deployment backend reported failure.",)
    assert failed.artifact_digest == artifact.digest
    assert failed.approval_digest == approval.digest
    assert failed.deployment_policy_digest == policy.digest
    assert failed.target_environment is Environment.TEST
    repeated = DeploymentService(FakeBackend(DeploymentBackendError("different detail")), approval_store, governance_store, governance_store).deploy(
        artifact, policy, Environment.TEST, approval.digest, **upstream,
    )
    assert repeated == failed
    assert repeated.digest == digest
    backend.error = None
    succeeded = service.deploy(artifact, policy, Environment.TEST, approval.digest, **upstream)
    assert len(backend.calls) == 2
    assert succeeded.outcome is DeploymentOutcome.SUCCESS
    assert succeeded.digest != digest
    assert failed == original
    assert failed.digest == digest
    assert failed.outcome is DeploymentOutcome.FAIL


def test_unexpected_exception_propagates_without_retry(inputs, governance_store, approval_store):
    artifact, policy, approval, upstream = inputs
    error = RuntimeError("programming error")
    backend = FakeBackend(error)
    with pytest.raises(RuntimeError) as raised:
        DeploymentService(backend, approval_store, governance_store, governance_store).deploy(artifact, policy, Environment.TEST, approval.digest, **upstream)
    assert raised.value is error
    assert len(backend.calls) == 1


@pytest.mark.parametrize("environments", [["TEST"], [None]])
def test_policy_rejects_invalid_environment_values(environments):
    with pytest.raises(TypeError, match="Environment values"):
        DeploymentPolicy(environments)


@pytest.mark.parametrize("changes", [
    {"artifact_digest": None}, {"approval_digest": None}, {"deployment_policy_digest": None},
    {"target_environment": "TEST"}, {"outcome": "SUCCESS"},
    {"reasons": "failure"}, {"reasons": [None]},
])
def test_attempt_rejects_invalid_field_types(changes):
    attempt = DeploymentAttempt("a", "b", "c", Environment.TEST, DeploymentOutcome.SUCCESS, ())
    with pytest.raises(TypeError):
        replace(attempt, **changes)


@pytest.mark.parametrize(("change", "reason"), [
    ("fake_approval_evidence", "Approval does not match evaluation evidence"),
    ("fake_approval_policy", "Approval does not match evaluation policy"),
    ("different_evidence", "Approval does not match evaluation evidence"),
    ("different_policy", "Approval does not match evaluation policy"),
    ("wrong_artifact", "Evaluation evidence does not match artifact"),
    ("wrong_policy", "Evaluation evidence does not match policy"),
    ("failed_evidence", "Evaluation outcome must be PASS"),
    ("altered_pass", "does not match policy evaluation"),
    ("fabricated_pass", "does not match policy evaluation"),
    ("blank_approver", "nonblank string"),
    ("missing_policy", "Evaluation policy is required"),
    ("missing_evidence", "Evaluation evidence is required"),
])
def test_upstream_chain_rejection_never_calls_backend(inputs, change, reason, governance_store, approval_store):
    artifact, policy, approval, upstream = inputs
    evaluation_policy = upstream["evaluation_policy"]
    evidence = upstream["evaluation_evidence"]
    if change == "fake_approval_evidence":
        approval = replace(approval, evaluation_evidence_digest="0" * 64)
    elif change == "fake_approval_policy":
        approval = replace(approval, evaluation_policy_digest="0" * 64)
    elif change == "different_evidence":
        evidence = EvaluationService().evaluate(AgentArtifact("b" * 64), evaluation_policy)
    elif change == "different_policy":
        evaluation_policy = EvaluationPolicy("b" * 64)
    elif change == "wrong_artifact":
        evidence = replace(evidence, artifact_digest="b" * 64)
    elif change == "wrong_policy":
        evidence = replace(evidence, evaluation_policy_digest="b" * 64)
    elif change == "failed_evidence":
        evidence = replace(evidence, outcome=EvaluationOutcome.FAIL)
    elif change == "altered_pass":
        evidence = replace(evidence, reasons=("fabricated reason",))
    elif change == "fabricated_pass":
        evaluation_policy = EvaluationPolicy("b" * 64)
        failed = EvaluationService().evaluate(artifact, evaluation_policy)
        assert failed.outcome is EvaluationOutcome.FAIL
        evidence = replace(failed, outcome=EvaluationOutcome.PASS, reasons=())
    elif change == "blank_approver":
        approval = replace(approval, approver_id=" ")
    elif change == "missing_policy":
        evaluation_policy = None
    elif change == "missing_evidence":
        evidence = None
    if change in {
        "wrong_artifact", "wrong_policy", "failed_evidence", "altered_pass", "fabricated_pass",
    }:
        # Even a self-consistent approval bound to fabricated evidence must fail.
        approval = HumanApproval(
            artifact.digest, evidence.digest, evaluation_policy.digest,
            "human-1", Environment.TEST,
        )
    approval_store.save_approval(approval)
    backend = FakeBackend()
    with pytest.raises(DeploymentNotAuthorized, match=reason):
        DeploymentService(backend, approval_store, governance_store, governance_store).deploy(
            artifact, policy, Environment.TEST, approval.digest,
            evaluation_policy=evaluation_policy, evaluation_evidence=evidence,
        )
    assert backend.calls == []
