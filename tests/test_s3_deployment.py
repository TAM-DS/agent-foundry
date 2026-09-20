from io import BytesIO
import json

import pytest

from agent_foundry.adapters.s3_deployment import S3DeploymentBackend
from agent_foundry.domain.artifact import AgentArtifact
from agent_foundry.domain.specification import Environment
from agent_foundry.services.deployment import DeploymentBackendError


class FakeClientError(Exception):
    pass


class FakeS3Client:
    # Deliberately provides no list or delete operations.
    def __init__(self, *, read_back=None, fail_at=None, error=None):
        self.calls = []
        self.read_back = read_back
        self.fail_at = fail_at
        self.error = error
        self.objects = {}
        self.stream = None

    def put_object(self, **request):
        self.calls.append(("put_object", request))
        if self.fail_at == "put_object":
            raise self.error
        self.objects[(request["Bucket"], request["Key"])] = request["Body"]
        return {}

    def get_object(self, **request):
        self.calls.append(("get_object", request))
        if self.fail_at == "get_object":
            raise self.error
        stored = self.objects[(request["Bucket"], request["Key"])]
        self.stream = BytesIO(stored if self.read_back is None else self.read_back)
        return {"Body": self.stream}


@pytest.fixture
def artifact():
    return AgentArtifact("a" * 64)


def test_dev_writes_exact_canonical_payload_and_verifies_same_object(artifact):
    client = FakeS3Client()
    backend = S3DeploymentBackend(client, "deployment-artifacts", FakeClientError)
    expected = (
        '{"artifact_digest":"' + artifact.digest
        + '","artifact_format":"agent-foundry-candidate-v1",'
        + '"source_specification_digest":"' + artifact.source_specification_digest + '"}'
    ).encode("utf-8")
    key = f"dev/artifacts/{artifact.digest}.json"
    expected_calls = [
        ("put_object", {
            "Bucket": "deployment-artifacts", "Key": key,
            "Body": expected, "ContentType": "application/json",
        }),
        ("get_object", {"Bucket": "deployment-artifacts", "Key": key}),
    ]

    assert backend.deploy(artifact, Environment.DEV) is None
    assert client.calls == expected_calls
    assert client.stream.closed
    assert json.loads(expected) == {
        "artifact_format": "agent-foundry-candidate-v1",
        "artifact_digest": artifact.digest,
        "source_specification_digest": artifact.source_specification_digest,
    }
    assert backend.deploy(artifact, Environment.DEV) is None
    assert client.calls == expected_calls * 2


@pytest.mark.parametrize("read_back", [b"", b"wrong artifact", b"{}"])
def test_read_back_mismatch_fails_without_deleting_written_object(artifact, read_back):
    client = FakeS3Client(read_back=read_back)
    with pytest.raises(DeploymentBackendError, match="read-back"):
        S3DeploymentBackend(client, "bucket", FakeClientError).deploy(artifact, Environment.DEV)
    assert [name for name, _ in client.calls] == ["put_object", "get_object"]
    assert client.objects[("bucket", f"dev/artifacts/{artifact.digest}.json")]
    assert client.stream.closed


def test_equivalent_json_with_different_bytes_is_rejected(artifact):
    client = FakeS3Client(read_back=json.dumps({
        "artifact_format": "agent-foundry-candidate-v1",
        "artifact_digest": artifact.digest,
        "source_specification_digest": artifact.source_specification_digest,
    }, indent=2).encode("utf-8"))
    with pytest.raises(DeploymentBackendError, match="read-back"):
        S3DeploymentBackend(client, "bucket", FakeClientError).deploy(artifact, Environment.DEV)


@pytest.mark.parametrize("operation", ["put_object", "get_object"])
@pytest.mark.parametrize("client_error_types", [FakeClientError, (FakeClientError,)])
def test_expected_client_failure_is_translated_without_retry(
    artifact, operation, client_error_types
):
    error = FakeClientError("AccessDenied")
    client = FakeS3Client(fail_at=operation, error=error)
    with pytest.raises(DeploymentBackendError) as caught:
        S3DeploymentBackend(client, "bucket", client_error_types).deploy(artifact, Environment.DEV)
    assert caught.value.__cause__ is error
    expected = ["put_object"] if operation == "put_object" else ["put_object", "get_object"]
    assert [name for name, _ in client.calls] == expected


@pytest.mark.parametrize("environment", [Environment.TEST, Environment.PROD, "DEV", None])
def test_non_dev_target_is_rejected_before_any_s3_call(artifact, environment):
    client = FakeS3Client()
    with pytest.raises(DeploymentBackendError, match="only DEV"):
        S3DeploymentBackend(client, "bucket", FakeClientError).deploy(artifact, environment)
    assert client.calls == []


@pytest.mark.parametrize("operation", ["put_object", "get_object"])
@pytest.mark.parametrize("error_type", [TypeError, ValueError, RuntimeError])
def test_unexpected_errors_propagate(artifact, operation, error_type):
    error = error_type("programming error")
    client = FakeS3Client(fail_at=operation, error=error)
    with pytest.raises(error_type) as caught:
        S3DeploymentBackend(client, "bucket", FakeClientError).deploy(artifact, Environment.DEV)
    assert caught.value is error
