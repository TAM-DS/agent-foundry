"""The governed input to specification validation."""

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json


def _digest(value: dict) -> str:
    """Hash UTF-8 JSON with sorted keys and no insignificant whitespace."""
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


class Environment(Enum):
    DEV = "DEV"
    TEST = "TEST"
    PROD = "PROD"


@dataclass(frozen=True, slots=True)
class AgentSpecification:
    purpose: str
    requested_tools: tuple[str, ...]
    target_environment: Environment

    def __post_init__(self) -> None:
        if not isinstance(self.purpose, str):
            raise TypeError("purpose must be a string")
        if isinstance(self.requested_tools, str):
            raise TypeError("requested_tools must be a collection of strings")
        tools = tuple(self.requested_tools)
        if any(not isinstance(tool, str) for tool in tools):
            raise TypeError("requested_tools must contain strings")
        if not isinstance(self.target_environment, Environment):
            raise TypeError("target_environment must be an Environment")
        object.__setattr__(self, "requested_tools", tuple(sorted(set(tools))))

    @property
    def digest(self) -> str:
        return _digest({
            "purpose": self.purpose,
            "requested_tools": self.requested_tools,
            "target_environment": self.target_environment.value,
        })
