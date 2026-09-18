"""Immutable deployment policy and evidence of actual deployment outcomes."""

from dataclasses import dataclass
from enum import Enum

from agent_foundry.domain.specification import Environment, _digest


@dataclass(frozen=True, slots=True)
class DeploymentPolicy:
    allowed_environments: frozenset[Environment]

    def __post_init__(self) -> None:
        environments = frozenset(self.allowed_environments)
        if any(not isinstance(env, Environment) for env in environments):
            raise TypeError("allowed_environments must contain Environment values")
        object.__setattr__(self, "allowed_environments", environments)

    @property
    def digest(self) -> str:
        return _digest({
            "allowed_environments": sorted(env.value for env in self.allowed_environments),
        })


class DeploymentOutcome(Enum):
    SUCCESS = "SUCCESS"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class DeploymentAttempt:
    artifact_digest: str
    approval_digest: str
    deployment_policy_digest: str
    target_environment: Environment
    outcome: DeploymentOutcome
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        for field in ("artifact_digest", "approval_digest", "deployment_policy_digest"):
            if not isinstance(getattr(self, field), str):
                raise TypeError(f"{field} must be a string")
        if not isinstance(self.target_environment, Environment):
            raise TypeError("target_environment must be an Environment")
        if not isinstance(self.outcome, DeploymentOutcome):
            raise TypeError("outcome must be a DeploymentOutcome")
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
            "approval_digest": self.approval_digest,
            "deployment_policy_digest": self.deployment_policy_digest,
            "target_environment": self.target_environment.value,
            "outcome": self.outcome.value,
            "reasons": self.reasons,
        })
