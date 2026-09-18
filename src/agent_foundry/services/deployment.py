"""Authorize an exact deployment attempt, then record its external outcome."""

from typing import Protocol

from agent_foundry.domain.approval import HumanApproval
from agent_foundry.domain.artifact import AgentArtifact
from agent_foundry.domain.deployment import (
    DeploymentAttempt, DeploymentOutcome, DeploymentPolicy,
)
from agent_foundry.domain.specification import Environment
from agent_foundry.domain.evaluation import EvaluationAttempt, EvaluationPolicy
from agent_foundry.services.approval import ApprovalNotAuthorized, ApprovalService


class DeploymentNotAuthorized(Exception):
    """Approval or policy does not authorize this deployment attempt."""


class DeploymentBackendError(Exception):
    """An expected deployment failure reported by the external target."""


class DeploymentBackend(Protocol):
    def deploy(self, artifact: AgentArtifact, target_environment: Environment) -> None:
        """Deploy this exact artifact; return on success or raise on failure."""
        ...


class DeploymentService:
    def __init__(self, backend: DeploymentBackend) -> None:
        self._backend = backend

    def deploy(
        self,
        artifact: AgentArtifact,
        policy: DeploymentPolicy,
        target_environment: Environment,
        approval: HumanApproval | None = None,
        *,
        evaluation_policy: EvaluationPolicy,
        evaluation_evidence: EvaluationAttempt,
    ) -> DeploymentAttempt:
        if not isinstance(approval, HumanApproval):
            raise DeploymentNotAuthorized("Human approval is required.")
        artifact_digest = artifact.digest
        if approval.artifact_digest != artifact_digest:
            raise DeploymentNotAuthorized("Approval does not match artifact.")
        if not isinstance(target_environment, Environment):
            raise DeploymentNotAuthorized("Target environment must be an Environment.")
        if target_environment is not approval.target_environment:
            raise DeploymentNotAuthorized("Approval does not match target environment.")
        if target_environment not in policy.allowed_environments:
            raise DeploymentNotAuthorized("Environment is not allowed by deployment policy.")
        if not isinstance(evaluation_policy, EvaluationPolicy):
            raise DeploymentNotAuthorized("Evaluation policy is required.")
        if not isinstance(evaluation_evidence, EvaluationAttempt):
            raise DeploymentNotAuthorized("Evaluation evidence is required.")
        if approval.evaluation_evidence_digest != evaluation_evidence.digest:
            raise DeploymentNotAuthorized("Approval does not match evaluation evidence.")
        if approval.evaluation_policy_digest != evaluation_policy.digest:
            raise DeploymentNotAuthorized("Approval does not match evaluation policy.")
        # Recheck the complete upstream chain, including deterministic evaluation.
        # Approver identity authentication remains external to this local slice.
        try:
            expected_approval = ApprovalService().approve(
                artifact, evaluation_policy, evaluation_evidence,
                approval.approver_id, approval.target_environment,
            )
        except ApprovalNotAuthorized as error:
            raise DeploymentNotAuthorized(str(error)) from error
        approval_digest = approval.digest
        if approval_digest != expected_approval.digest:
            raise DeploymentNotAuthorized("Approval digest does not match supplied approval.")
        if approval != expected_approval:
            raise DeploymentNotAuthorized("Approval does not match canonical approval.")
        policy_digest = policy.digest
        try:
            self._backend.deploy(artifact, target_environment)
        except DeploymentBackendError:
            outcome = DeploymentOutcome.FAIL
            reasons = ("Deployment backend reported failure.",)
        else:
            outcome = DeploymentOutcome.SUCCESS
            reasons = ()
        return DeploymentAttempt(
            artifact_digest=artifact_digest,
            approval_digest=approval_digest,
            deployment_policy_digest=policy_digest,
            target_environment=target_environment,
            outcome=outcome,
            reasons=reasons,
        )
