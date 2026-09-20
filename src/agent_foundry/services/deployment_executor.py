"""Recover frozen deployment material and delegate authority enforcement."""

from agent_foundry.domain.deployment import DeploymentAttempt
from agent_foundry.domain.deployment_manifest import DeploymentManifest
from agent_foundry.services.deployment import DeploymentService
from agent_foundry.services.deployment_manifest import DeploymentManifestStore


class DeploymentManifestNotFound(Exception):
    """No durable deployment manifest exists for the requested digest."""


class DeploymentExecutor:
    def __init__(
        self, manifest_store: DeploymentManifestStore,
        deployment_service: DeploymentService,
    ) -> None:
        self._manifest_store = manifest_store
        self._deployment_service = deployment_service

    def execute(self, manifest_digest: str) -> DeploymentAttempt:
        if type(manifest_digest) is not str:
            raise TypeError("manifest_digest must be an exact str")
        if not manifest_digest.strip():
            raise ValueError("manifest_digest must be a nonblank string")
        manifest = self._manifest_store.get_deployment_manifest(manifest_digest)
        if manifest is None:
            raise DeploymentManifestNotFound(manifest_digest)
        if type(manifest) is not DeploymentManifest:
            raise TypeError("manifest must be an exact DeploymentManifest")
        if manifest.digest != manifest_digest:
            raise ValueError("Manifest digest does not match requested digest.")
        return self._deployment_service.deploy(
            artifact=manifest.artifact,
            policy=manifest.deployment_policy,
            target_environment=manifest.target_environment,
            approval_digest=manifest.approval_digest,
            evaluation_policy=manifest.evaluation_policy,
            evaluation_evidence=manifest.evaluation_evidence,
        )
