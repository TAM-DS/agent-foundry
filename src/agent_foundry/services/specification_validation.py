"""Deterministic policy evaluation and immutable evidence."""

from dataclasses import dataclass
from enum import Enum

from agent_foundry.domain.specification import AgentSpecification, Environment, _digest


@dataclass(frozen=True, slots=True)
class SpecificationPolicy:
    allowed_tools: frozenset[str]
    allowed_environments: frozenset[Environment]

    def __post_init__(self) -> None:
        if isinstance(self.allowed_tools, str):
            raise TypeError("allowed_tools must be a collection of strings")
        tools = frozenset(self.allowed_tools)
        environments = frozenset(self.allowed_environments)
        if any(not isinstance(tool, str) for tool in tools):
            raise TypeError("allowed_tools must contain strings")
        if any(not isinstance(env, Environment) for env in environments):
            raise TypeError("allowed_environments must contain Environment values")
        object.__setattr__(self, "allowed_tools", tools)
        object.__setattr__(self, "allowed_environments", environments)

    @property
    def digest(self) -> str:
        return _digest({
            "allowed_tools": sorted(self.allowed_tools),
            "allowed_environments": sorted(env.value for env in self.allowed_environments),
        })


class ValidationOutcome(Enum):
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class ValidationEvidence:
    specification_digest: str
    policy_digest: str
    outcome: ValidationOutcome
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "reasons", tuple(self.reasons))


class SpecificationValidationService:
    def validate(
        self, specification: AgentSpecification, policy: SpecificationPolicy,
    ) -> ValidationEvidence:
        reasons = []
        if not specification.purpose.strip():
            reasons.append("Purpose must not be blank.")
        for tool in sorted(set(specification.requested_tools) - policy.allowed_tools):
            reasons.append(f"Tool is not allowed: {tool}")
        if specification.target_environment not in policy.allowed_environments:
            reasons.append(
                f"Environment is not allowed: {specification.target_environment.value}"
            )
        return ValidationEvidence(
            specification_digest=specification.digest,
            policy_digest=policy.digest,
            outcome=ValidationOutcome.FAIL if reasons else ValidationOutcome.PASS,
            reasons=tuple(reasons),
        )
