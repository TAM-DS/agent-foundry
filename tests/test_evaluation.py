from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import json

import pytest

from agent_foundry.domain.artifact import AgentArtifact
from agent_foundry.domain.evaluation import (
    EvaluationAttempt, EvaluationOutcome, EvaluationPolicy,
)
from agent_foundry.services.evaluation import EvaluationService


def test_matching_artifact_and_canonical_digests_are_deterministic():
    artifact = AgentArtifact("a" * 64)
    policy = EvaluationPolicy(artifact.source_specification_digest)
    evidence = EvaluationService().evaluate(artifact, policy)
    repeated = EvaluationService().evaluate(artifact, EvaluationPolicy("a" * 64))
    assert evidence == repeated
    assert evidence.outcome is EvaluationOutcome.PASS
    assert evidence.reasons == ()
    assert evidence.artifact_digest == artifact.digest
    assert evidence.evaluation_policy_digest == policy.digest
    assert policy.digest == sha256(
        ('{"expected_specification_digest":"' + "a" * 64 + '"}').encode()
    ).hexdigest()
    canonical = json.dumps({
        "artifact_digest": artifact.digest,
        "evaluation_policy_digest": policy.digest,
        "outcome": "PASS",
        "reasons": [],
    }, sort_keys=True, separators=(",", ":"))
    assert evidence.digest == repeated.digest == sha256(canonical.encode()).hexdigest()


def test_mismatched_artifact_fails_and_later_success_preserves_failure():
    artifact = AgentArtifact("b" * 64)
    service = EvaluationService()
    policy = EvaluationPolicy("a" * 64)
    failed = service.evaluate(artifact, policy)
    digest = failed.digest
    passed = service.evaluate(artifact, EvaluationPolicy("b" * 64))
    assert passed.outcome is EvaluationOutcome.PASS
    assert failed.outcome is EvaluationOutcome.FAIL
    assert failed.reasons == ("Source specification digest does not match evaluation policy.",)
    assert failed == service.evaluate(artifact, policy)
    assert failed.digest == digest


@pytest.mark.parametrize("source", ["", "a" * 63, "a" * 65, "g" * 64, "a" * 63 + "\n"])
def test_malformed_digest_fails_even_if_policy_matches(source):
    artifact = AgentArtifact(source)
    evidence = EvaluationService().evaluate(artifact, EvaluationPolicy(source))
    assert evidence.outcome is EvaluationOutcome.FAIL
    assert evidence.reasons == ("Source specification digest must be a SHA-256 hex digest.",)


def test_multiple_failure_reasons_have_stable_order():
    evidence = EvaluationService().evaluate(AgentArtifact("bad"), EvaluationPolicy("a" * 64))
    assert evidence.reasons == (
        "Source specification digest must be a SHA-256 hex digest.",
        "Source specification digest does not match evaluation policy.",
    )


def test_records_are_immutable_and_copy_reasons():
    policy = EvaluationPolicy("a" * 64)
    reasons = ["failure"]
    evidence = EvaluationAttempt("b" * 64, policy.digest, EvaluationOutcome.FAIL, reasons)
    digest = evidence.digest
    reasons.append("changed")
    assert evidence.reasons == ("failure",)
    assert evidence.digest == digest
    for record, field, value in [
        (policy, "expected_specification_digest", "c" * 64),
        (evidence, "artifact_digest", "c" * 64),
        (evidence, "evaluation_policy_digest", "c" * 64),
        (evidence, "outcome", EvaluationOutcome.PASS),
        (evidence, "reasons", ()),
    ]:
        with pytest.raises(FrozenInstanceError):
            setattr(record, field, value)


@pytest.mark.parametrize("changes", [
    {"artifact_digest": "c" * 64},
    {"evaluation_policy_digest": "c" * 64},
    {"outcome": EvaluationOutcome.FAIL},
    {"reasons": ("changed",)},
])
def test_evidence_digest_binds_every_field(changes):
    evidence = EvaluationService().evaluate(AgentArtifact("a" * 64), EvaluationPolicy("a" * 64))
    assert replace(evidence, **changes).digest != evidence.digest
