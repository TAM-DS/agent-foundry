"""The minimal content-addressed build result."""

from dataclasses import dataclass

from agent_foundry.domain.specification import _digest


@dataclass(frozen=True, slots=True)
class AgentArtifact:
    source_specification_digest: str

    @property
    def digest(self) -> str:
        return _digest({
            "artifact_format": "agent-foundry-candidate-v1",
            "source_specification_digest": self.source_specification_digest,
        })
