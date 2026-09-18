"""Bounded runtime authority and immutable tool authorization evidence."""

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from agent_foundry.domain.specification import Environment, _digest


@dataclass(frozen=True, slots=True)
class ToolPermission:
    tool: str
    action: str

    def __post_init__(self) -> None:
        for field in ("tool", "action"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a nonblank string")

    @property
    def digest(self) -> str:
        return _digest({"tool": self.tool, "action": self.action})


def _permissions(values: Iterable[ToolPermission]) -> frozenset[ToolPermission]:
    permissions = frozenset(values)
    if any(not isinstance(value, ToolPermission) for value in permissions):
        raise TypeError("permissions must contain ToolPermission values")
    return permissions


def _permission_data(values: Iterable[ToolPermission]) -> list[dict[str, str]]:
    return [
        {"tool": value.tool, "action": value.action}
        for value in sorted(values, key=lambda value: (value.tool, value.action))
    ]


def _validate_context(artifact_digest: str, target_environment: Environment) -> None:
    if not isinstance(artifact_digest, str):
        raise TypeError("artifact_digest must be a string")
    if not isinstance(target_environment, Environment):
        raise TypeError("target_environment must be an Environment")


@dataclass(frozen=True, slots=True)
class RuntimePolicy:
    allowed_environments: frozenset[Environment]
    allowed_permissions: frozenset[ToolPermission]

    def __post_init__(self) -> None:
        environments = frozenset(self.allowed_environments)
        if any(not isinstance(env, Environment) for env in environments):
            raise TypeError("allowed_environments must contain Environment values")
        object.__setattr__(self, "allowed_environments", environments)
        object.__setattr__(self, "allowed_permissions", _permissions(self.allowed_permissions))

    @property
    def digest(self) -> str:
        return _digest({
            "allowed_environments": sorted(env.value for env in self.allowed_environments),
            "allowed_permissions": _permission_data(self.allowed_permissions),
        })


@dataclass(frozen=True, slots=True)
class RuntimeGrant:
    artifact_digest: str
    deployment_attempt_digest: str
    runtime_policy_digest: str
    target_environment: Environment
    permissions: frozenset[ToolPermission]
    grantor_id: str

    def __post_init__(self) -> None:
        _validate_context(self.artifact_digest, self.target_environment)
        for field in ("deployment_attempt_digest", "runtime_policy_digest"):
            if not isinstance(getattr(self, field), str):
                raise TypeError(f"{field} must be a string")
        if not isinstance(self.grantor_id, str) or not self.grantor_id.strip():
            raise ValueError("grantor_id must be a nonblank string")
        object.__setattr__(self, "permissions", _permissions(self.permissions))

    @property
    def digest(self) -> str:
        return _digest({
            "artifact_digest": self.artifact_digest,
            "deployment_attempt_digest": self.deployment_attempt_digest,
            "runtime_policy_digest": self.runtime_policy_digest,
            "target_environment": self.target_environment.value,
            "permissions": _permission_data(self.permissions),
            "grantor_id": self.grantor_id,
        })


@dataclass(frozen=True, slots=True)
class ToolRequest:
    artifact_digest: str
    target_environment: Environment
    permission: ToolPermission

    def __post_init__(self) -> None:
        _validate_context(self.artifact_digest, self.target_environment)
        if not isinstance(self.permission, ToolPermission):
            raise TypeError("permission must be a ToolPermission")


class ToolAuthorizationOutcome(Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"


@dataclass(frozen=True, slots=True)
class ToolAuthorizationDecision:
    runtime_grant_digest: str
    runtime_policy_digest: str
    artifact_digest: str
    target_environment: Environment
    requested_permission: ToolPermission
    outcome: ToolAuthorizationOutcome
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_context(self.artifact_digest, self.target_environment)
        for field in ("runtime_grant_digest", "runtime_policy_digest"):
            if not isinstance(getattr(self, field), str):
                raise TypeError(f"{field} must be a string")
        if not isinstance(self.requested_permission, ToolPermission):
            raise TypeError("requested_permission must be a ToolPermission")
        if not isinstance(self.outcome, ToolAuthorizationOutcome):
            raise TypeError("outcome must be a ToolAuthorizationOutcome")
        if isinstance(self.reasons, str):
            raise TypeError("reasons must be a collection of strings")
        reasons = tuple(self.reasons)
        if any(not isinstance(reason, str) for reason in reasons):
            raise TypeError("reasons must contain strings")
        object.__setattr__(self, "reasons", reasons)

    @property
    def digest(self) -> str:
        return _digest({
            "runtime_grant_digest": self.runtime_grant_digest,
            "runtime_policy_digest": self.runtime_policy_digest,
            "artifact_digest": self.artifact_digest,
            "target_environment": self.target_environment.value,
            "requested_permission": _permission_data((self.requested_permission,))[0],
            "outcome": self.outcome.value,
            "reasons": self.reasons,
        })
