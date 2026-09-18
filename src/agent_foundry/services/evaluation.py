"""Deterministic evaluation produces evidence, never approval."""

from agent_foundry.domain.artifact import AgentArtifact
from agent_foundry.domain.evaluation import (
    EvaluationAttempt, EvaluationOutcome, EvaluationPolicy,
)


class EvaluationService:
    def evaluate(
        self, artifact: AgentArtifact, policy: EvaluationPolicy,
    ) -> EvaluationAttempt:
        reasons = []
        source = artifact.source_specification_digest
        if (
            not isinstance(source, str)
            or len(source) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in source)
        ):
            reasons.append("Source specification digest must be a SHA-256 hex digest.")
        if source != policy.expected_specification_digest:
            reasons.append("Source specification digest does not match evaluation policy.")
        return EvaluationAttempt(
            artifact_digest=artifact.digest,
            evaluation_policy_digest=policy.digest,
            outcome=EvaluationOutcome.FAIL if reasons else EvaluationOutcome.PASS,
            reasons=tuple(reasons),
        )
