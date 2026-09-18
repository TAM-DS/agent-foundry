"""Issue explicit grants using authoritative deployment evidence."""

from typing import Iterable, Protocol

from agent_foundry.domain.lifecycle import LifecycleState, LifecycleStore, LifecycleTransition
from agent_foundry.domain.artifact import AgentArtifact
from agent_foundry.domain.deployment import DeploymentAttempt, DeploymentOutcome
from agent_foundry.domain.runtime import RuntimeGrant, RuntimePolicy, ToolPermission, _permissions
from agent_foundry.domain.specification import Environment


class DeploymentEvidenceSource(Protocol):
    def get_deployment(self, digest: str) -> DeploymentAttempt | None:
        """Retrieve authoritative deployment evidence by exact digest."""
        ...


class RuntimeGrantStore(Protocol):
    """Trusted issued grants; write access belongs to the issuing service."""

    def save_runtime_grant(self, grant: RuntimeGrant) -> None:
        """Save a legitimately issued grant."""
        ...

    def get_runtime_grant(self, digest: str) -> RuntimeGrant | None:
        """Retrieve an issued grant by exact digest, or None."""
        ...


class RuntimeGrantNotAuthorized(Exception):
    """Deployment evidence or runtime policy does not authorize issuance."""


class RuntimeAuthorizationService:
    def __init__(
        self, evidence_source: DeploymentEvidenceSource, grant_store: RuntimeGrantStore,
        lifecycle_store: LifecycleStore,
    ) -> None:
        self._evidence_source = evidence_source
        self._grant_store = grant_store
        self._lifecycle_store = lifecycle_store

    def issue(
        self,
        artifact: AgentArtifact,
        deployment_attempt_digest: str,
        policy: RuntimePolicy,
        target_environment: Environment,
        permissions: Iterable[ToolPermission],
        grantor_id: str,
    ) -> RuntimeGrant:
        """Grantor identity is metadata; authentication remains external."""
        if not isinstance(deployment_attempt_digest, str):
            raise RuntimeGrantNotAuthorized("Deployment attempt digest must be a string.")
        attempt = self._evidence_source.get_deployment(deployment_attempt_digest)
        if not isinstance(attempt, DeploymentAttempt) or attempt.digest != deployment_attempt_digest:
            raise RuntimeGrantNotAuthorized("Authoritative deployment evidence is required.")
        if attempt.outcome is not DeploymentOutcome.SUCCESS:
            raise RuntimeGrantNotAuthorized("Deployment outcome must be SUCCESS.")
        if attempt.artifact_digest != artifact.digest:
            raise RuntimeGrantNotAuthorized("Deployment evidence does not match artifact.")
        if not isinstance(target_environment, Environment):
            raise RuntimeGrantNotAuthorized("Target environment must be an Environment.")
        if attempt.target_environment is not target_environment:
            raise RuntimeGrantNotAuthorized("Deployment evidence does not match target environment.")
        if target_environment not in policy.allowed_environments:
            raise RuntimeGrantNotAuthorized("Environment is not allowed by runtime policy.")
        try:
            requested = _permissions(permissions)
        except TypeError as error:
            raise RuntimeGrantNotAuthorized("Requested permissions must be ToolPermission values.") from error
        if not requested <= policy.allowed_permissions:
            raise RuntimeGrantNotAuthorized("Requested permission is not allowed by runtime policy.")
        if not isinstance(grantor_id, str) or not grantor_id.strip():
            raise RuntimeGrantNotAuthorized("Grantor identity must be a nonblank string.")
        state = self._lifecycle_store.get_lifecycle_state(artifact.digest, target_environment)
        if state is None or state < LifecycleState.DEPLOYED:
            raise RuntimeGrantNotAuthorized("At least DEPLOYED lifecycle state is required.")
        grant = RuntimeGrant(
            artifact.digest, deployment_attempt_digest, policy.digest,
            target_environment, requested, grantor_id,
        )
        self._grant_store.save_runtime_grant(grant)
        self._lifecycle_store.save_lifecycle_transition(LifecycleTransition(
            artifact.digest, LifecycleState.OPERATING, target_environment, grant.digest,
        ))
        return grant
