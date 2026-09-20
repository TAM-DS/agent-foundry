"""Freeze proposed deployment evidence without granting authority or deploying."""

from typing import Protocol

from agent_foundry.domain.artifact import AgentArtifact
from agent_foundry.domain.deployment import DeploymentPolicy
from agent_foundry.domain.deployment_manifest import DeploymentManifest
from agent_foundry.domain.evaluation import EvaluationAttempt, EvaluationPolicy
from agent_foundry.domain.specification import Environment


class DeploymentManifestStore(Protocol):
    def save_deployment_manifest(self, manifest: DeploymentManifest) -> None: ...

    def get_deployment_manifest(self, digest: str) -> DeploymentManifest | None: ...


class DeploymentManifestService:
    def __init__(self, store: DeploymentManifestStore) -> None:
        self._store = store

    def freeze(
        self,
        artifact: AgentArtifact,
        evaluation_policy: EvaluationPolicy,
        evaluation_evidence: EvaluationAttempt,
        deployment_policy: DeploymentPolicy,
        target_environment: Environment,
        approval_digest: str,
    ) -> DeploymentManifest:
        manifest = DeploymentManifest(
            artifact, evaluation_policy, evaluation_evidence, deployment_policy,
            target_environment, approval_digest,
        )
        self._store.save_deployment_manifest(manifest)
        return manifest
