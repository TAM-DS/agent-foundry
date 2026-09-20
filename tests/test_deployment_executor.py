from dataclasses import fields
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from agent_foundry.domain.artifact import AgentArtifact
from agent_foundry.domain.deployment import DeploymentAttempt, DeploymentOutcome, DeploymentPolicy
from agent_foundry.domain.deployment_manifest import DeploymentManifest
from agent_foundry.domain.evaluation import EvaluationPolicy
from agent_foundry.domain.lifecycle import LifecycleState
from agent_foundry.domain.specification import AgentSpecification, Environment
from agent_foundry.persistence import EvidenceIntegrityError, SQLiteGovernanceStore
from agent_foundry.services.agent_build import AgentBuildService
from agent_foundry.services.approval import ApprovalService
from agent_foundry.services.deployment import (
    DeploymentBackendError, DeploymentNotAuthorized, DeploymentService,
)
from agent_foundry.services.deployment_executor import DeploymentExecutor, DeploymentManifestNotFound
from agent_foundry.services.deployment_manifest import DeploymentManifestService, DeploymentManifestStore
from agent_foundry.services.evaluation import EvaluationService
from agent_foundry.services.specification_validation import SpecificationPolicy, SpecificationValidationService


class DigestSubclass(str):
    pass


@pytest.fixture
def manifest():
    artifact = AgentArtifact("a" * 64)
    policy = EvaluationPolicy(artifact.source_specification_digest)
    return DeploymentManifest(
        artifact, policy, EvaluationService().evaluate(artifact, policy),
        DeploymentPolicy({Environment.DEV}), Environment.DEV, "approval",
    )


@pytest.fixture
def dependencies(manifest):
    store = Mock(spec_set=DeploymentManifestStore)
    store.get_deployment_manifest.return_value = manifest
    service = Mock(spec_set=DeploymentService)
    return store, service


@pytest.mark.parametrize("digest,error", [
    (None, TypeError), (1, TypeError), (True, TypeError), (b"digest", TypeError),
    ([], TypeError), (object(), TypeError), (DigestSubclass("digest"), TypeError),
    ("", ValueError), (" ", ValueError), ("\n\t", ValueError),
])
def test_invalid_digest_never_looks_up_or_deploys(dependencies, digest, error):
    store, service = dependencies
    with pytest.raises(error):
        DeploymentExecutor(store, service).execute(digest)
    assert store.mock_calls == service.mock_calls == []


@pytest.mark.parametrize("digest", ["missing", " missing "])
def test_missing_manifest_uses_exact_input_and_never_deploys(dependencies, digest):
    store, service = dependencies
    store.get_deployment_manifest.return_value = None
    with pytest.raises(DeploymentManifestNotFound):
        DeploymentExecutor(store, service).execute(digest)
    assert store.mock_calls == [call.get_deployment_manifest(digest)]
    assert service.mock_calls == []


@pytest.mark.parametrize("kind", ["object", "compatible", "subclass"])
def test_noncanonical_manifest_rejected(dependencies, manifest, kind):
    class ExtendedManifest(DeploymentManifest):
        pass

    values = {field.name: getattr(manifest, field.name) for field in fields(manifest)}
    record = {
        "object": object(),
        "compatible": SimpleNamespace(**values, digest=manifest.digest),
        "subclass": ExtendedManifest(**values),
    }[kind]
    store, service = dependencies
    store.get_deployment_manifest.return_value = record
    with pytest.raises(TypeError, match="exact DeploymentManifest"):
        DeploymentExecutor(store, service).execute(manifest.digest)
    service.deploy.assert_not_called()


def test_digest_mismatch_never_deploys(dependencies):
    store, service = dependencies
    with pytest.raises(ValueError, match="digest"):
        DeploymentExecutor(store, service).execute("different")
    service.deploy.assert_not_called()


def test_only_recovered_fields_are_forwarded_by_identity(dependencies, manifest):
    store, service = dependencies
    attempt = DeploymentAttempt(
        manifest.artifact.digest, manifest.approval_digest, manifest.deployment_policy.digest,
        Environment.DEV, DeploymentOutcome.SUCCESS, (),
    )
    service.deploy.return_value = attempt
    assert DeploymentExecutor(store, service).execute(manifest.digest) is attempt
    assert store.mock_calls == [call.get_deployment_manifest(manifest.digest)]
    expected = dict(
        artifact=manifest.artifact, policy=manifest.deployment_policy,
        target_environment=manifest.target_environment, approval_digest=manifest.approval_digest,
        evaluation_policy=manifest.evaluation_policy, evaluation_evidence=manifest.evaluation_evidence,
    )
    assert service.mock_calls == [call.deploy(**expected)]
    assert all(service.deploy.call_args.kwargs[key] is value for key, value in expected.items())


@pytest.mark.parametrize("field", [
    "artifact", "policy", "deployment_policy", "target_environment", "approval_digest",
    "evaluation_policy", "evaluation_evidence",
])
def test_caller_overrides_rejected(dependencies, manifest, field):
    store, service = dependencies
    with pytest.raises(TypeError):
        DeploymentExecutor(store, service).execute(manifest.digest, **{field: None})
    assert store.mock_calls == service.mock_calls == []


def test_extra_positional_input_rejected(dependencies, manifest):
    store, service = dependencies
    with pytest.raises(TypeError):
        DeploymentExecutor(store, service).execute(manifest.digest, manifest.artifact)
    assert store.mock_calls == service.mock_calls == []


@pytest.mark.parametrize("stage", ["lookup", "deployment"])
@pytest.mark.parametrize("error_type", [DeploymentNotAuthorized, EvidenceIntegrityError, RuntimeError])
def test_errors_propagate_unchanged_without_retry(dependencies, manifest, stage, error_type):
    store, service = dependencies
    error = error_type("failure")
    operation = store.get_deployment_manifest if stage == "lookup" else service.deploy
    operation.side_effect = error
    with pytest.raises(error_type) as raised:
        DeploymentExecutor(store, service).execute(manifest.digest)
    assert raised.value is error
    store.get_deployment_manifest.assert_called_once_with(manifest.digest)
    assert service.deploy.call_count == (0 if stage == "lookup" else 1)


def freeze_then_discard(path):
    # Writer scope owns every original object; only the manifest digest escapes.
    with SQLiteGovernanceStore(path) as store:
        specification = AgentSpecification("Summarize", ("read",), Environment.DEV)
        specification_policy = SpecificationPolicy({"read"}, {Environment.DEV})
        validation = SpecificationValidationService().validate(specification, specification_policy)
        artifact = AgentBuildService(store).build(specification, specification_policy, validation)
        policy = EvaluationPolicy(specification.digest)
        evidence = EvaluationService().evaluate(artifact, policy)
        approval = ApprovalService(store, store).approve(
            artifact, policy, evidence, "test-human", Environment.DEV,
        )
        return DeploymentManifestService(store).freeze(
            artifact, policy, evidence, DeploymentPolicy({Environment.DEV}),
            Environment.DEV, approval.digest,
        ).digest


@pytest.mark.parametrize("fails", [False, True], ids=["success", "backend-failure"])
def test_execution_from_reopened_sqlite_only_by_digest(tmp_path, monkeypatch, fails):
    path = tmp_path / "governance.sqlite"
    manifest_digest = freeze_then_discard(path)
    with SQLiteGovernanceStore(path) as store:
        recovered = []
        events = []
        get_manifest = store.get_deployment_manifest
        get_approval = store.get_approval

        def recover(digest):
            events.append("manifest")
            manifest = get_manifest(digest)
            recovered.append(manifest)
            assert store.get_lifecycle_state(manifest.artifact.digest, Environment.DEV) is LifecycleState.APPROVED
            return manifest

        def approval_lookup(digest):
            events.append("approval")
            assert digest == recovered[0].approval_digest
            return get_approval(digest)

        def backend_deploy(artifact, environment):
            events.append("backend")
            assert artifact is recovered[0].artifact
            assert environment is Environment.DEV
            assert store.get_lifecycle_state(artifact.digest, environment) is LifecycleState.APPROVED
            if fails:
                raise DeploymentBackendError("local failure")

        monkeypatch.setattr(store, "get_deployment_manifest", Mock(side_effect=recover))
        monkeypatch.setattr(store, "get_approval", Mock(side_effect=approval_lookup))
        backend = Mock()
        backend.deploy.side_effect = backend_deploy
        service = DeploymentService(backend, store, store, store)
        produced = []
        real_deploy = service.deploy

        def deploy(**kwargs):
            produced.append(real_deploy(**kwargs))
            return produced[-1]

        monkeypatch.setattr(service, "deploy", deploy)
        attempt = DeploymentExecutor(store, service).execute(manifest_digest)
        assert attempt is produced[0]
        assert events == ["manifest", "approval", "backend"]
        store.get_deployment_manifest.assert_called_once_with(manifest_digest)
        store.get_approval.assert_called_once_with(recovered[0].approval_digest)
        backend.deploy.assert_called_once_with(recovered[0].artifact, Environment.DEV)
        assert attempt.outcome is (DeploymentOutcome.FAIL if fails else DeploymentOutcome.SUCCESS)
        expected_state = LifecycleState.APPROVED if fails else LifecycleState.DEPLOYED
        assert store.get_lifecycle_state(attempt.artifact_digest, Environment.DEV) is expected_state
        assert store.get_deployment(attempt.digest) == attempt
    with SQLiteGovernanceStore(path) as reader:
        assert reader.get_deployment(attempt.digest) == attempt
        assert reader.get_lifecycle_state(attempt.artifact_digest, Environment.DEV) is expected_state
