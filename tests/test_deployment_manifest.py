from dataclasses import FrozenInstanceError, asdict, fields, make_dataclass, replace
import json
import sqlite3
from unittest.mock import Mock, call

import pytest

from agent_foundry.domain.artifact import AgentArtifact
from agent_foundry.domain.deployment import DeploymentPolicy
from agent_foundry.domain.deployment_manifest import DeploymentManifest
from agent_foundry.domain.evaluation import EvaluationAttempt, EvaluationOutcome, EvaluationPolicy
from agent_foundry.domain.lifecycle import LifecycleState, LifecycleTransition
from agent_foundry.domain.specification import Environment, _digest
from agent_foundry.persistence import EvidenceConflictError, EvidenceIntegrityError, SQLiteGovernanceStore
from agent_foundry.services.deployment import DeploymentService
from agent_foundry.services.deployment_manifest import DeploymentManifestService, DeploymentManifestStore


def material():
    artifact = AgentArtifact("spec-é")
    policy = EvaluationPolicy("different-spec")
    return dict(
        artifact=artifact,
        evaluation_policy=policy,
        evaluation_evidence=EvaluationAttempt(
            artifact.digest, policy.digest, EvaluationOutcome.FAIL, ("mismatch", "reason-é"),
        ),
        deployment_policy=DeploymentPolicy({Environment.DEV, Environment.TEST}),
        target_environment=Environment.PROD,
        approval_digest="unrecorded-approval",
    )


def test_immutable_exact_fields_and_deterministic_identity():
    manifest = DeploymentManifest(**material())
    assert {field.name for field in fields(manifest)} == set(material())
    assert not hasattr(manifest, "__dict__")
    for field in fields(manifest):
        with pytest.raises(FrozenInstanceError):
            setattr(manifest, field.name, getattr(manifest, field.name))
    assert manifest == DeploymentManifest(**material())
    assert manifest.digest == DeploymentManifest(**material()).digest
    assert manifest.digest == _digest({
        "artifact_digest": manifest.artifact.digest,
        "evaluation_policy_digest": manifest.evaluation_policy.digest,
        "evaluation_evidence_digest": manifest.evaluation_evidence.digest,
        "deployment_policy_digest": manifest.deployment_policy.digest,
        "target_environment": "PROD",
        "approval_digest": "unrecorded-approval",
    })


@pytest.mark.parametrize("field,value", [
    ("artifact", AgentArtifact("other")),
    ("evaluation_policy", EvaluationPolicy("other")),
    ("evaluation_evidence", EvaluationAttempt("other", "policy", EvaluationOutcome.PASS, ())),
    ("deployment_policy", DeploymentPolicy({Environment.PROD})),
    ("target_environment", Environment.TEST),
    ("approval_digest", "other"),
])
def test_each_input_binds_identity(field, value):
    manifest = DeploymentManifest(**material())
    assert replace(manifest, **{field: value}).digest != manifest.digest


@pytest.mark.parametrize("field", list(material()))
def test_constructor_rejects_wrong_types(field):
    with pytest.raises(TypeError):
        DeploymentManifest(**(material() | {field: None}))


@pytest.mark.parametrize("field", [
    "artifact", "evaluation_policy", "evaluation_evidence", "deployment_policy",
])
def test_domain_record_subclasses_are_rejected_before_persistence(field):
    inputs = material()
    canonical = inputs[field]
    extended_type = make_dataclass(
        "ExtendedRecord", [("extra_state", str)],
        bases=(type(canonical),), frozen=True, slots=True,
    )
    inputs[field] = extended_type(
        **{item.name: getattr(canonical, item.name) for item in fields(canonical)},
        extra_state="noncanonical",
    )
    assert isinstance(inputs[field], type(canonical))
    assert asdict(inputs[field])["extra_state"] == "noncanonical"
    with pytest.raises(TypeError, match=field):
        DeploymentManifest(**inputs)
    store = Mock(spec_set=DeploymentManifestStore)
    with pytest.raises(TypeError, match=field):
        DeploymentManifestService(store).freeze(**inputs)
    store.save_deployment_manifest.assert_not_called()


def test_constructor_rejects_approval_digest_subclass():
    class ExtendedString(str):
        pass

    with pytest.raises(TypeError, match="approval_digest"):
        DeploymentManifest(**(material() | {"approval_digest": ExtendedString("approval")}))


@pytest.mark.parametrize("value", ["", " ", "\n\t"])
def test_constructor_rejects_blank_approval(value):
    with pytest.raises(ValueError):
        DeploymentManifest(**(material() | {"approval_digest": value}))


def test_freeze_only_saves_canonical_manifest():
    store = Mock(spec_set=DeploymentManifestStore)
    manifest = DeploymentManifestService(store).freeze(**material())
    assert type(manifest) is DeploymentManifest
    assert manifest == DeploymentManifest(**material())
    assert store.mock_calls == [call.save_deployment_manifest(manifest)]
    assert store.save_deployment_manifest.call_args.args[0] is manifest


def test_freeze_propagates_storage_failure():
    store = Mock(spec_set=DeploymentManifestStore)
    store.save_deployment_manifest.side_effect = OSError("unavailable")
    with pytest.raises(OSError, match="unavailable"):
        DeploymentManifestService(store).freeze(**material())
    store.save_deployment_manifest.assert_called_once()


def test_freeze_is_not_authorization_or_deployment(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("freeze must not authorize, deploy, or advance lifecycle")

    monkeypatch.setattr(DeploymentService, "deploy", forbidden)
    with SQLiteGovernanceStore(tmp_path / "evidence.sqlite") as store:
        artifact = material()["artifact"]
        store.save_lifecycle_transition(LifecycleTransition(
            artifact.digest, LifecycleState.BUILT, None, "build-evidence",
        ))
        monkeypatch.setattr(store, "save_lifecycle_transition", forbidden)
        monkeypatch.setattr(store, "get_approval", forbidden)
        monkeypatch.setattr(store, "save_deployment", forbidden)
        manifest = DeploymentManifestService(store).freeze(**material())
        assert store.get_lifecycle_state(artifact.digest, Environment.PROD) is LifecycleState.BUILT
        assert store.get_deployment_manifest(manifest.digest) == manifest
        assert store._connection.execute("SELECT COUNT(*) FROM deployments").fetchone() == (0,)
        assert store._connection.execute("SELECT COUNT(*) FROM approvals").fetchone() == (0,)


def test_complete_recovery_from_fresh_store_only_by_digest(tmp_path):
    path = tmp_path / "durable.sqlite"

    def write_then_discard_objects():
        with SQLiteGovernanceStore(path) as store:
            manifest = DeploymentManifestService(store).freeze(**material())
            store.save_deployment_manifest(manifest)
            return manifest.digest

    digest = write_then_discard_objects()
    # Only the digest survives the writer scope; no objects or child stores are
    # supplied to the reader. Independently constructed expectations are below.
    with SQLiteGovernanceStore(path) as reopened:
        restored = reopened.get_deployment_manifest(digest)
        assert restored.digest == digest
        assert restored == DeploymentManifest(**material())
        for field, expected in material().items():
            actual = getattr(restored, field)
            assert type(actual) is type(expected)
            assert actual == expected
        assert asdict(restored) == asdict(DeploymentManifest(**material()))
        assert reopened.get_deployment_manifest("missing") is None
        reopened.save_deployment_manifest(restored)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM deployment_manifests").fetchone() == (1,)


@pytest.mark.parametrize("tampering", [
    "artifact", "evaluation_policy", "evaluation_evidence", "deployment_policy",
    "target_environment", "approval_digest", "invalid_json", "extra_key", "nested_extra_key",
    "missing_key", "wrong_shape", "nested_wrong_shape", "invalid_enum", "duplicate_environment",
    "unsorted_environments", "whitespace", "duplicate_key", "wrong_digest",
])
def test_tampering_and_noncanonical_data_fail_without_overwrite(tmp_path, tampering):
    path = tmp_path / "evidence.sqlite"
    manifest = DeploymentManifest(**material())
    with SQLiteGovernanceStore(path) as store:
        store.save_deployment_manifest(manifest)
    digest = manifest.digest
    with sqlite3.connect(path) as connection:
        raw = connection.execute("SELECT payload FROM deployment_manifests").fetchone()[0]
        data = json.loads(raw)
        if tampering == "artifact":
            data["artifact"]["source_specification_digest"] = "tampered"
        elif tampering == "evaluation_policy":
            data["evaluation_policy"]["expected_specification_digest"] = "tampered"
        elif tampering == "evaluation_evidence":
            data["evaluation_evidence"]["reasons"].append("tampered")
        elif tampering == "deployment_policy":
            data["deployment_policy"]["allowed_environments"] = ["PROD"]
        elif tampering in ("target_environment", "approval_digest"):
            data[tampering] = "TEST"
        elif tampering == "extra_key":
            data["extra"] = True
        elif tampering == "nested_extra_key":
            data["artifact"]["extra"] = True
        elif tampering == "missing_key":
            del data["evaluation_policy"]
        elif tampering == "wrong_shape":
            data = []
        elif tampering == "nested_wrong_shape":
            data["evaluation_evidence"] = []
        elif tampering == "invalid_enum":
            data["evaluation_evidence"]["outcome"] = "UNKNOWN"
        elif tampering == "duplicate_environment":
            data["deployment_policy"]["allowed_environments"].append("TEST")
        elif tampering == "unsorted_environments":
            data["deployment_policy"]["allowed_environments"].reverse()
        corrupt = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        if tampering == "invalid_json":
            corrupt = "{"
        elif tampering == "whitespace":
            corrupt = " " + raw
        elif tampering == "duplicate_key":
            corrupt = '{"approval_digest":"unrecorded-approval",' + raw[1:]
        elif tampering == "wrong_digest":
            digest = "wrong-digest"
        connection.execute("UPDATE deployment_manifests SET digest = ?, payload = ?", (digest, corrupt))
    with SQLiteGovernanceStore(path) as store:
        with pytest.raises(EvidenceIntegrityError):
            store.get_deployment_manifest(digest)
        if digest == manifest.digest:
            with pytest.raises(EvidenceConflictError):
                store.save_deployment_manifest(manifest)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT payload FROM deployment_manifests").fetchone() == (corrupt,)
