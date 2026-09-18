"""Conservative lifecycle projection; trusted writers record authority first."""

from dataclasses import dataclass
from enum import IntEnum
from typing import Protocol

from agent_foundry.domain.specification import Environment, _digest


class LifecycleState(IntEnum):
    BUILT = 1
    APPROVED = 2
    DEPLOYED = 3
    OPERATING = 4


@dataclass(frozen=True, slots=True)
class LifecycleTransition:
    artifact_digest: str
    state: LifecycleState
    target_environment: Environment | None
    authority_digest: str

    def __post_init__(self) -> None:
        for field in ("artifact_digest", "authority_digest"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a nonblank string")
        if not isinstance(self.state, LifecycleState):
            raise TypeError("state must be a LifecycleState")
        if self.state is LifecycleState.BUILT:
            if self.target_environment is not None:
                raise ValueError("BUILT must have no target environment")
        elif not isinstance(self.target_environment, Environment):
            raise TypeError("target_environment must be an Environment")

    @property
    def digest(self) -> str:
        return _digest({
            "artifact_digest": self.artifact_digest,
            "state": self.state.value,
            "target_environment": (
                self.target_environment.value if self.target_environment is not None else None
            ),
            "authority_digest": self.authority_digest,
        })


class LifecycleStore(Protocol):
    """Trusted append-only projection; write access belongs to governed services.

    Saving a transition is recording evidence, never an independent authorization.
    """

    def save_lifecycle_transition(self, transition: LifecycleTransition) -> None: ...

    def get_lifecycle_transition(self, digest: str) -> LifecycleTransition | None: ...

    def get_lifecycle_state(
        self, artifact_digest: str, target_environment: Environment | None,
    ) -> LifecycleState | None: ...
