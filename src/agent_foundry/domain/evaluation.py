"""Immutable evaluation criteria and content-addressed evidence."""

from dataclasses import dataclass
from enum import Enum

from agent_foundry.domain.specification import _digest


@dataclass(frozen=True, slots=True)
class EvaluationPolicy:
    expected_specification_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.expected_specification_digest, str):
            raise TypeError("expected_specification_digest must be a string")

    @property
    def digest(self) -> str:
        return _digest({
            "expected_specification_digest": self.expected_specification_digest,
        })


class EvaluationOutcome(Enum):
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class EvaluationAttempt:
    artifact_digest: str
    evaluation_policy_digest: str
    outcome: EvaluationOutcome
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_digest, str):
            raise TypeError("artifact_digest must be a string")
        if not isinstance(self.evaluation_policy_digest, str):
            raise TypeError("evaluation_policy_digest must be a string")
        if not isinstance(self.outcome, EvaluationOutcome):
            raise TypeError("outcome must be an EvaluationOutcome")
        if isinstance(self.reasons, str):
            raise TypeError("reasons must be a collection of strings")
        reasons = tuple(self.reasons)
        if any(not isinstance(reason, str) for reason in reasons):
            raise TypeError("reasons must contain strings")
        object.__setattr__(self, "reasons", reasons)

    @property
    def digest(self) -> str:
        return _digest({
            "artifact_digest": self.artifact_digest,
            "evaluation_policy_digest": self.evaluation_policy_digest,
            "outcome": self.outcome.value,
            "reasons": self.reasons,
        })
