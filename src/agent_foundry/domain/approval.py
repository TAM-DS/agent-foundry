"""Explicit human authority bound to exact artifact and evaluation evidence."""

from dataclasses import dataclass

from agent_foundry.domain.specification import Environment


@dataclass(frozen=True, slots=True)
class HumanApproval:
    artifact_digest: str
    evaluation_evidence_digest: str
    evaluation_policy_digest: str
    approver_id: str
    target_environment: Environment

    def __post_init__(self) -> None:
        for field in (
            "artifact_digest", "evaluation_evidence_digest",
            "evaluation_policy_digest", "approver_id",
        ):
            if not isinstance(getattr(self, field), str):
                raise TypeError(f"{field} must be a string")
        if not isinstance(self.target_environment, Environment):
            raise TypeError("target_environment must be an Environment")
