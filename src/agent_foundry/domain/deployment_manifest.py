"""Frozen deployment material, never deployment authority."""

from dataclasses import dataclass

from agent_foundry.domain.artifact import AgentArtifact
from agent_foundry.domain.deployment import DeploymentPolicy
from agent_foundry.domain.evaluation import EvaluationAttempt, EvaluationPolicy
from agent_foundry.domain.specification import Environment, _digest


@dataclass(frozen=True, slots=True)
class DeploymentManifest:
    """Exact proposed inputs referencing, not replacing, authoritative approval.

    Execution must retrieve HumanApproval from the authoritative approval store
    and let DeploymentService independently verify authority and lifecycle state.
    Construction validates shape only, including for failed or unauthorized inputs.
    """

    artifact: AgentArtifact
    evaluation_policy: EvaluationPolicy
    evaluation_evidence: EvaluationAttempt
    deployment_policy: DeploymentPolicy
    target_environment: Environment
    approval_digest: str

    def __post_init__(self) -> None:
        for field, expected in (
            ("artifact", AgentArtifact),
            ("evaluation_policy", EvaluationPolicy),
            ("evaluation_evidence", EvaluationAttempt),
            ("deployment_policy", DeploymentPolicy),
            ("target_environment", Environment),
            ("approval_digest", str),
        ):
            if type(getattr(self, field)) is not expected:
                raise TypeError(f"{field} must be a {expected.__name__}")
        if not self.approval_digest.strip():
            raise ValueError("approval_digest must be a nonblank string")

    @property
    def digest(self) -> str:
        return _digest({
            "artifact_digest": self.artifact.digest,
            "evaluation_policy_digest": self.evaluation_policy.digest,
            "evaluation_evidence_digest": self.evaluation_evidence.digest,
            "deployment_policy_digest": self.deployment_policy.digest,
            "target_environment": self.target_environment.value,
            "approval_digest": self.approval_digest,
        })
