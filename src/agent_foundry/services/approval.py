"""Explicit approval gated by exact, independently rechecked evidence."""

from typing import Protocol

from agent_foundry.domain.lifecycle import LifecycleState, LifecycleStore, LifecycleTransition
from agent_foundry.domain.approval import HumanApproval
from agent_foundry.domain.artifact import AgentArtifact
from agent_foundry.domain.evaluation import (
    EvaluationAttempt, EvaluationOutcome, EvaluationPolicy,
)
from agent_foundry.domain.specification import Environment
from agent_foundry.services.evaluation import EvaluationService


class ApprovalNotAuthorized(Exception):
    """The supplied evidence or identity cannot authorize approval."""


class ApprovalStore(Protocol):
    """Authoritative approvals; write access belongs to the issuing service."""

    def save_approval(self, approval: HumanApproval) -> None: ...

    def get_approval(self, digest: str) -> HumanApproval | None: ...


class ApprovalService:
    def __init__(self, approval_store: ApprovalStore, lifecycle_store: LifecycleStore) -> None:
        self._approval_store = approval_store
        self._lifecycle_store = lifecycle_store

    def approve(
        self,
        artifact: AgentArtifact,
        policy: EvaluationPolicy,
        evidence: EvaluationAttempt,
        approver_id: str,
        target_environment: Environment,
    ) -> HumanApproval:
        """Record an explicit human decision; identity authentication is external."""
        approval = _canonical_approval(
            artifact, policy, evidence, approver_id, target_environment,
        )
        if self._lifecycle_store.get_lifecycle_state(artifact.digest, None) is not LifecycleState.BUILT:
            raise ApprovalNotAuthorized("Authoritative BUILT lifecycle state is required.")
        self._approval_store.save_approval(approval)
        self._lifecycle_store.save_lifecycle_transition(LifecycleTransition(
            artifact.digest, LifecycleState.APPROVED, target_environment, approval.digest,
        ))
        return approval


def _canonical_approval(
    artifact: AgentArtifact,
    policy: EvaluationPolicy,
    evidence: EvaluationAttempt,
    approver_id: str,
    target_environment: Environment,
) -> HumanApproval:
    """Recheck and construct canonical content without issuing authority."""
    if not isinstance(evidence, EvaluationAttempt):
        raise ApprovalNotAuthorized("Evaluation evidence is required.")
    if evidence.outcome is not EvaluationOutcome.PASS:
        raise ApprovalNotAuthorized("Evaluation outcome must be PASS.")
    if evidence.artifact_digest != artifact.digest:
        raise ApprovalNotAuthorized("Evaluation evidence does not match artifact.")
    if evidence.evaluation_policy_digest != policy.digest:
        raise ApprovalNotAuthorized("Evaluation evidence does not match policy.")
    # In-process evidence is not a signed capability. Recompute all checks
    # and compare the complete result, including reasons, before approval.
    if evidence != EvaluationService().evaluate(artifact, policy):
        raise ApprovalNotAuthorized("Evaluation evidence does not match policy evaluation.")
    if not isinstance(approver_id, str) or not approver_id.strip():
        raise ApprovalNotAuthorized("Approver identity must be a nonblank string.")
    if not isinstance(target_environment, Environment):
        raise ApprovalNotAuthorized("Target environment must be an Environment.")
    return HumanApproval(
        artifact_digest=artifact.digest,
        evaluation_evidence_digest=evidence.digest,
        evaluation_policy_digest=policy.digest,
        approver_id=approver_id,
        target_environment=target_environment,
    )
