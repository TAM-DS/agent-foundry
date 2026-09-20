"""Live DEV proof; approver provenance is supplied by external authentication.

This application records the explicitly requested proof approval through the
existing local approval model. It does not authenticate the human approver.
"""

import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from agent_foundry.composition.aws import create_dev_deployment_service
from agent_foundry.domain.deployment import DeploymentOutcome, DeploymentPolicy
from agent_foundry.domain.evaluation import EvaluationPolicy
from agent_foundry.domain.lifecycle import LifecycleState
from agent_foundry.domain.specification import AgentSpecification, Environment
from agent_foundry.persistence import SQLiteGovernanceStore
from agent_foundry.services.agent_build import AgentBuildService
from agent_foundry.services.approval import ApprovalService
from agent_foundry.services.deployment_executor import DeploymentExecutor
from agent_foundry.services.deployment_manifest import DeploymentManifestService
from agent_foundry.services.evaluation import EvaluationService
from agent_foundry.services.specification_validation import (
    SpecificationPolicy, SpecificationValidationService,
)


class VerificationFailed(Exception):
    """A required live proof postcondition was not satisfied."""


def _write_manifest(path: Path, approver_id: str) -> str:
    # Only the digest leaves this scope; writer A closes before recovery starts.
    with SQLiteGovernanceStore(path) as store:
        specification = AgentSpecification(
            "Live governed DEV deployment proof", ("read",), Environment.DEV,
        )
        policy = SpecificationPolicy({"read"}, {Environment.DEV})
        validation = SpecificationValidationService().validate(specification, policy)
        artifact = AgentBuildService(store).build(specification, policy, validation)
        evaluation_policy = EvaluationPolicy(specification.digest)
        evidence = EvaluationService().evaluate(artifact, evaluation_policy)
        approval = ApprovalService(store, store).approve(
            artifact, evaluation_policy, evidence, approver_id, Environment.DEV,
        )
        return DeploymentManifestService(store).freeze(
            artifact, evaluation_policy, evidence, DeploymentPolicy({Environment.DEV}),
            Environment.DEV, approval.digest,
        ).digest


def _execute_manifest(
    path: Path, manifest_digest: str, *, bucket: str, region: str,
) -> dict[str, str]:
    with SQLiteGovernanceStore(path) as store:
        service = create_dev_deployment_service(
            governance_store=store, bucket_name=bucket, region_name=region,
        )
        attempt = DeploymentExecutor(store, service).execute(manifest_digest)
        if attempt.outcome is not DeploymentOutcome.SUCCESS:
            raise VerificationFailed("Deployment did not succeed.")
        if store.get_deployment(attempt.digest) != attempt:
            raise VerificationFailed("Deployment attempt did not round-trip.")
        manifest = store.get_deployment_manifest(manifest_digest)
        if manifest is None or manifest.digest != manifest_digest:
            raise VerificationFailed("Manifest did not round-trip.")
        approval = store.get_approval(manifest.approval_digest)
        if approval is None or approval.digest != manifest.approval_digest:
            raise VerificationFailed("Independent approval is unavailable.")
        lifecycle = store.get_lifecycle_state(manifest.artifact.digest, Environment.DEV)
        if lifecycle is not LifecycleState.DEPLOYED:
            raise VerificationFailed("Artifact is not DEPLOYED in DEV.")
        return {
            "manifest_digest": manifest.digest,
            "artifact_digest": manifest.artifact.digest,
            "approval_digest": approval.digest,
            "deployment_attempt_digest": attempt.digest,
            "outcome": attempt.outcome.value,
            "lifecycle": lifecycle.name,
            "environment": Environment.DEV.value,
            "s3_key": f"dev/artifacts/{manifest.artifact.digest}.json",
            "approver_id": approval.approver_id,
        }


def _nonblank(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("must be nonblank")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ("bucket", "region", "approver-id"):
        parser.add_argument(f"--{name}", required=True, type=_nonblank)
    args = parser.parse_args(argv)
    try:
        with TemporaryDirectory(prefix="agent-foundry-live-dev-") as directory:
            path = Path(directory) / "governance.sqlite"
            manifest_digest = _write_manifest(path, args.approver_id)
            proof = _execute_manifest(
                path, manifest_digest, bucket=args.bucket, region=args.region,
            )
    except Exception:
        # Exception text from SDKs or credentials providers is not proof output.
        print("Live DEV verification failed; no successful proof produced.", file=sys.stderr)
        return 1
    print(json.dumps(proof, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
