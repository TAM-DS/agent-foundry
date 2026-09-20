"""Live DEV proof; approver and grantor provenance is supplied by external authentication.

This application records the explicitly requested proof approval through the
existing local approval model. It does not authenticate the approver or grantor.
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
from agent_foundry.domain.runtime import (
    RuntimePolicy, ToolAuthorizationOutcome, ToolPermission, ToolRequest,
)
from agent_foundry.domain.specification import AgentSpecification, Environment
from agent_foundry.persistence import SQLiteGovernanceStore
from agent_foundry.services.agent_build import AgentBuildService
from agent_foundry.services.approval import ApprovalService
from agent_foundry.services.deployment_executor import DeploymentExecutor
from agent_foundry.services.deployment_manifest import DeploymentManifestService
from agent_foundry.services.evaluation import EvaluationService
from agent_foundry.services.runtime_authorization import RuntimeAuthorizationService
from agent_foundry.services.tool_authorization import ToolAuthorizationService
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
    path: Path, manifest_digest: str, *, bucket: str, region: str, grantor_id: str,
) -> dict[str, object]:
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
        read = ToolPermission("files", "read")
        search = ToolPermission("search", "query")
        runtime_policy = RuntimePolicy({Environment.DEV}, {read, search})
        grant = RuntimeAuthorizationService(
            evidence_source=store, grant_store=store, lifecycle_store=store,
        ).issue(
            artifact=manifest.artifact, deployment_attempt_digest=attempt.digest,
            policy=runtime_policy, target_environment=Environment.DEV,
            permissions={read}, grantor_id=grantor_id,
        )
        if (
            store.get_runtime_grant(grant.digest) != grant
            or grant.artifact_digest != manifest.artifact.digest
            or grant.deployment_attempt_digest != attempt.digest
            or grant.runtime_policy_digest != runtime_policy.digest
            or grant.target_environment is not Environment.DEV
            or grant.permissions != {read}
            or grant.grantor_id != grantor_id
        ):
            raise VerificationFailed("Runtime grant postconditions failed.")
        lifecycle = store.get_lifecycle_state(manifest.artifact.digest, Environment.DEV)
        if lifecycle is not LifecycleState.OPERATING:
            raise VerificationFailed("Artifact is not OPERATING in DEV.")
        tools = ToolAuthorizationService(
            grant_store=store, decision_store=store, lifecycle_store=store,
        )
        allowed = tools.authorize(
            runtime_grant_digest=grant.digest, policy=runtime_policy,
            request=ToolRequest(manifest.artifact.digest, Environment.DEV, read),
        )
        denied = tools.authorize(
            runtime_grant_digest=grant.digest, policy=runtime_policy,
            request=ToolRequest(manifest.artifact.digest, Environment.DEV, search),
        )
        if (
            allowed.outcome is not ToolAuthorizationOutcome.ALLOW or allowed.reasons
            or denied.outcome is not ToolAuthorizationOutcome.DENY
            or denied.reasons != ("Requested permission is not explicitly granted.",)
            or store.get_tool_decision(allowed.digest) != allowed
            or store.get_tool_decision(denied.digest) != denied
        ):
            raise VerificationFailed("Tool authorization postconditions failed.")
        return {
            "runtime_policy_digest": runtime_policy.digest,
            "runtime_grant_digest": grant.digest,
            "grantor_id": grant.grantor_id,
            "granted_permissions": [
                {"tool": permission.tool, "action": permission.action}
                for permission in sorted(grant.permissions, key=lambda p: (p.tool, p.action))
            ],
            "allowed_tool_decision_digest": allowed.digest,
            "allowed_tool_outcome": allowed.outcome.value,
            "denied_tool_decision_digest": denied.digest,
            "denied_tool_outcome": denied.outcome.value,
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
    for name in ("bucket", "region", "approver-id", "grantor-id"):
        parser.add_argument(f"--{name}", required=True, type=_nonblank)
    args = parser.parse_args(argv)
    try:
        with TemporaryDirectory(prefix="agent-foundry-live-dev-") as directory:
            path = Path(directory) / "governance.sqlite"
            manifest_digest = _write_manifest(path, args.approver_id)
            proof = _execute_manifest(
                path, manifest_digest, bucket=args.bucket, region=args.region,
                grantor_id=args.grantor_id,
            )
    except Exception:
        # Exception text from SDKs or credentials providers is not proof output.
        print("Live DEV verification failed; no successful proof produced.", file=sys.stderr)
        return 1
    print(json.dumps(proof, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
