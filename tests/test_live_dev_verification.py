import json
import sqlite3
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

from agent_foundry.adapters.s3_deployment import S3DeploymentBackend
from agent_foundry.composition import aws
from agent_foundry.domain.deployment import DeploymentOutcome
from agent_foundry.domain.lifecycle import LifecycleState
from agent_foundry.domain.specification import AgentSpecification, Environment
from agent_foundry.persistence import SQLiteGovernanceStore
from agent_foundry.verification import live_dev
from test_s3_deployment import FakeS3Client


ARGS = ["--bucket", "local-proof", "--region", "us-east-1", "--approver-id", "github:tester"]


@pytest.fixture(autouse=True)
def local_client(monkeypatch):
    client = FakeS3Client()
    factory = Mock(return_value=client)
    monkeypatch.setattr(aws.boto3, "client", factory)
    return client, factory


def test_live_application_uses_fresh_recovery_and_real_chain(monkeypatch, capsys, local_client):
    client, factory = local_client
    stores, recovered, attempts, events = [], [], [], []

    class ObservedStore(SQLiteGovernanceStore):
        def __init__(self, path):
            if stores:
                with pytest.raises(sqlite3.ProgrammingError):
                    stores[0]._connection.execute("SELECT 1")
            super().__init__(path)
            stores.append(self)

        def get_deployment_manifest(self, digest):
            manifest = super().get_deployment_manifest(digest)
            recovered.append(manifest)
            events.append("recover")
            return manifest

        def get_approval(self, digest):
            events.append("approval")
            return super().get_approval(digest)

        def save_deployment(self, attempt):
            attempts.append(attempt)
            super().save_deployment(attempt)

    monkeypatch.setattr(live_dev, "SQLiteGovernanceStore", ObservedStore)
    writer = Mock(wraps=live_dev._write_manifest)
    execute_phase = Mock(wraps=live_dev._execute_manifest)
    monkeypatch.setattr(live_dev, "_write_manifest", writer)
    monkeypatch.setattr(live_dev, "_execute_manifest", execute_phase)
    execute = live_dev.DeploymentExecutor.execute

    def execute_digest(self, *args, **kwargs):
        assert len(args) == 1 and type(args[0]) is str and not kwargs
        return execute(self, *args, **kwargs)

    monkeypatch.setattr(live_dev.DeploymentExecutor, "execute", execute_digest)
    backend_deploy = S3DeploymentBackend.deploy

    def deploy(self, artifact, environment):
        events.append("backend")
        assert artifact is recovered[0].artifact
        assert environment is Environment.DEV
        return backend_deploy(self, artifact, environment)

    monkeypatch.setattr(S3DeploymentBackend, "deploy", deploy)
    spies = {}
    for service, method in (
        (live_dev.SpecificationValidationService, "validate"),
        (live_dev.AgentBuildService, "build"),
        (live_dev.EvaluationService, "evaluate"),
        (live_dev.ApprovalService, "approve"),
        (live_dev.DeploymentManifestService, "freeze"),
    ):
        original = getattr(service, method)
        spy = Mock()
        spies[method] = spy

        def observe(self, *args, _original=original, _spy=spy, **kwargs):
            _spy(*args, **kwargs)
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(service, method, observe)

    # Sentinels are dummy strings, never real credentials.
    secrets = {
        "AWS_ACCESS_KEY_ID": "dummy-access-key",
        "AWS_SECRET_ACCESS_KEY": "dummy-secret-key",
        "AWS_SESSION_TOKEN": "dummy-session-token",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "dummy-oidc-token",
    }
    for key, value in secrets.items():
        monkeypatch.setenv(key, value)
    assert live_dev.main(ARGS) == 0
    output = capsys.readouterr()
    proof = json.loads(output.out)
    assert not output.err
    assert len(stores) == 2 and stores[0] is not stores[1]
    assert events[:3] == ["recover", "approval", "backend"]
    assert all(spy.called for spy in spies.values())
    specification, policy, validation = spies["build"].call_args.args
    assert specification == AgentSpecification(
        "Live governed DEV deployment proof", ("read",), Environment.DEV,
    )
    assert policy == live_dev.SpecificationPolicy({"read"}, {Environment.DEV})
    assert validation.specification_digest == specification.digest
    manifest = recovered[0]
    assert manifest.artifact.source_specification_digest == specification.digest
    assert manifest.deployment_policy.allowed_environments == {Environment.DEV}
    assert execute_phase.call_args.args == (writer.call_args.args[0], manifest.digest)
    assert execute_phase.call_args.kwargs == {"bucket": "local-proof", "region": "us-east-1"}
    assert attempts[0].outcome is DeploymentOutcome.SUCCESS
    assert proof == {
        "manifest_digest": manifest.digest,
        "artifact_digest": manifest.artifact.digest,
        "approval_digest": manifest.approval_digest,
        "deployment_attempt_digest": attempts[0].digest,
        "outcome": "SUCCESS", "lifecycle": "DEPLOYED", "environment": "DEV",
        "s3_key": f"dev/artifacts/{manifest.artifact.digest}.json",
        "approver_id": "github:tester",
    }
    factory.assert_called_once_with("s3", region_name="us-east-1")
    expected = json.dumps({
        "artifact_digest": manifest.artifact.digest,
        "artifact_format": "agent-foundry-candidate-v1",
        "source_specification_digest": specification.digest,
    }, sort_keys=True, separators=(",", ":")).encode()
    location = {"Bucket": "local-proof", "Key": proof["s3_key"]}
    assert client.calls == [
        ("put_object", {**location, "Body": expected, "ContentType": "application/json"}),
        ("get_object", location),
    ]
    assert client.stream.closed
    for key, value in secrets.items():
        assert key not in output.out and value not in output.out


@pytest.mark.parametrize("failure", ["put_object", "byte-mismatch"])
def test_backend_failure_exits_unsuccessfully(monkeypatch, capsys, local_client, failure):
    client, _ = local_client
    if failure == "put_object":
        client.fail_at = failure
        client.error = ClientError({"Error": {"Code": "AccessDenied", "Message": "secret-sentinel"}}, failure)
    else:
        client.read_back = b"wrong bytes"
    execute = live_dev.DeploymentExecutor.execute

    def observe(self, digest):
        attempt = execute(self, digest)
        assert attempt.outcome is DeploymentOutcome.FAIL
        assert self._manifest_store.get_deployment(attempt.digest) == attempt
        assert self._manifest_store.get_lifecycle_state(
            attempt.artifact_digest, Environment.DEV,
        ) is LifecycleState.APPROVED
        return attempt

    monkeypatch.setattr(live_dev.DeploymentExecutor, "execute", observe)
    assert live_dev.main(ARGS) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "failed" in output.err and "secret-sentinel" not in output.err


def test_missing_independent_approval_blocks_s3(monkeypatch, local_client, capsys):
    monkeypatch.setattr(SQLiteGovernanceStore, "get_approval", lambda *args: None)
    assert live_dev.main(ARGS) == 1
    assert local_client[0].calls == []
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("missing", ["attempt", "manifest", "approval", "lifecycle"])
def test_failed_postcondition_is_not_reported_as_success(monkeypatch, capsys, missing):
    execute = live_dev.DeploymentExecutor.execute

    def invalidate_after_execution(self, digest):
        attempt = execute(self, digest)
        method = {
            "attempt": "get_deployment", "manifest": "get_deployment_manifest",
            "approval": "get_approval", "lifecycle": "get_lifecycle_state",
        }[missing]
        monkeypatch.setattr(self._manifest_store, method, lambda *args: None)
        return attempt

    monkeypatch.setattr(live_dev.DeploymentExecutor, "execute", invalidate_after_execution)
    assert live_dev.main(ARGS) == 1
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("index", [1, 3, 5])
@pytest.mark.parametrize("value", ["", " \n\t", None])
def test_blank_or_missing_arguments_fail_before_client(local_client, index, value):
    args = ARGS.copy()
    if value is None:
        del args[index - 1:index + 1]
    else:
        args[index] = value
    with pytest.raises(SystemExit) as caught:
        live_dev.main(args)
    assert caught.value.code == 2
    local_client[1].assert_not_called()


@pytest.mark.parametrize("extra", [["--unknown"], ["extra"], ["--bucket"]])
def test_malformed_arguments_fail_before_client(local_client, extra):
    with pytest.raises(SystemExit) as caught:
        live_dev.main(ARGS + extra)
    assert caught.value.code == 2
    local_client[1].assert_not_called()
