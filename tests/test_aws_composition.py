from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

from agent_foundry.adapters.s3_deployment import S3DeploymentBackend
from agent_foundry.composition import aws
from agent_foundry.domain.deployment import DeploymentOutcome, DeploymentPolicy
from agent_foundry.domain.evaluation import EvaluationPolicy
from agent_foundry.domain.lifecycle import LifecycleState
from agent_foundry.domain.specification import AgentSpecification, Environment
from agent_foundry.persistence import SQLiteGovernanceStore
from agent_foundry.services.agent_build import AgentBuildService
from agent_foundry.services.approval import ApprovalService
from agent_foundry.services.deployment import DeploymentBackendError, DeploymentNotAuthorized
from agent_foundry.services.evaluation import EvaluationService
from agent_foundry.services.specification_validation import (
    SpecificationPolicy, SpecificationValidationService,
)
from test_s3_deployment import FakeS3Client


@pytest.fixture
def composed(monkeypatch):
    client = FakeS3Client()
    factory = Mock(return_value=client)
    monkeypatch.setattr(aws.boto3, "client", factory)
    with SQLiteGovernanceStore(":memory:") as store:
        service = aws.create_dev_deployment_service(
            governance_store=store,
            bucket_name="local-deployment-artifacts",
            region_name="us-west-2",
        )
        yield service, store, client, factory


@pytest.fixture
def chain(composed):
    _, store, _, _ = composed
    specification = AgentSpecification("Read files", ("read",), Environment.DEV)
    policy = SpecificationPolicy({"read"}, {Environment.DEV})
    validation = SpecificationValidationService().validate(specification, policy)
    artifact = AgentBuildService(store).build(specification, policy, validation)
    evaluation_policy = EvaluationPolicy(specification.digest)
    evidence = EvaluationService().evaluate(artifact, evaluation_policy)
    approval = ApprovalService(store, store).approve(
        artifact, evaluation_policy, evidence, "local-test-human", Environment.DEV,
    )
    return artifact, approval, {
        "evaluation_policy": evaluation_policy, "evaluation_evidence": evidence,
    }


def test_composition_uses_ambient_credentials_and_same_store(composed):
    service, store, client, factory = composed
    factory.assert_called_once_with("s3", region_name="us-west-2")
    assert isinstance(service._backend, S3DeploymentBackend)
    assert service._backend._s3_client is client
    assert service._approval_store is store
    assert service._deployment_store is store
    assert service._lifecycle_store is store
    assert client.calls == []


def test_complete_dev_chain_writes_and_verifies_exact_artifact(composed, chain):
    service, store, client, _ = composed
    artifact, approval, upstream = chain
    attempt = service.deploy(
        artifact, DeploymentPolicy({Environment.DEV}), Environment.DEV,
        approval.digest, **upstream,
    )
    assert attempt.outcome is DeploymentOutcome.SUCCESS
    assert store.get_approval(approval.digest) == approval
    assert store.get_deployment(attempt.digest) == attempt
    assert store.get_lifecycle_state(artifact.digest, Environment.DEV) is LifecycleState.DEPLOYED
    expected = (
        '{"artifact_digest":"' + artifact.digest
        + '","artifact_format":"agent-foundry-candidate-v1",'
        + '"source_specification_digest":"' + artifact.source_specification_digest + '"}'
    ).encode("utf-8")
    location = {
        "Bucket": "local-deployment-artifacts",
        "Key": f"dev/artifacts/{artifact.digest}.json",
    }
    assert client.calls == [
        ("put_object", {**location, "Body": expected, "ContentType": "application/json"}),
        ("get_object", location),
    ]
    assert client.stream.closed


def test_read_back_mismatch_records_failure_without_advancing_lifecycle(composed, chain):
    service, store, client, _ = composed
    artifact, approval, upstream = chain
    client.read_back = b"different artifact bytes"
    attempt = service.deploy(
        artifact, DeploymentPolicy({Environment.DEV}), Environment.DEV,
        approval.digest, **upstream,
    )
    assert attempt.outcome is DeploymentOutcome.FAIL
    assert store.get_deployment(attempt.digest) == attempt
    assert store.get_lifecycle_state(artifact.digest, Environment.DEV) is LifecycleState.APPROVED
    assert [name for name, _ in client.calls] == ["put_object", "get_object"]
    assert client.stream.closed


@pytest.mark.parametrize("operation", ["put_object", "get_object"])
def test_real_client_error_is_translated_by_composed_backend(composed, chain, operation):
    service, _, client, _ = composed
    artifact, _, _ = chain
    error = ClientError({"Error": {"Code": "AccessDenied", "Message": "Denied"}}, operation)
    client.fail_at = operation
    client.error = error
    with pytest.raises(DeploymentBackendError) as caught:
        service._backend.deploy(artifact, Environment.DEV)
    assert caught.value.__cause__ is error
    expected = ["put_object"] if operation == "put_object" else ["put_object", "get_object"]
    assert [name for name, _ in client.calls] == expected


@pytest.mark.parametrize("environment", [Environment.TEST, Environment.PROD])
def test_composition_adds_no_authority_for_other_environments(composed, chain, environment):
    service, store, client, _ = composed
    artifact, approval, upstream = chain
    with pytest.raises(DeploymentNotAuthorized, match="target environment"):
        service.deploy(
            artifact, DeploymentPolicy(set(Environment)), environment,
            approval.digest, **upstream,
        )
    with pytest.raises(DeploymentBackendError, match="only DEV"):
        service._backend.deploy(artifact, environment)
    assert client.calls == []
    assert store.get_lifecycle_state(artifact.digest, environment) is not LifecycleState.DEPLOYED


def test_composed_service_requires_approval_from_supplied_store(composed, chain):
    service, store, client, _ = composed
    artifact, _, upstream = chain
    with pytest.raises(DeploymentNotAuthorized, match="Human approval is required"):
        service.deploy(
            artifact, DeploymentPolicy({Environment.DEV}), Environment.DEV,
            "missing-approval", **upstream,
        )
    assert client.calls == []
    assert store.get_lifecycle_state(artifact.digest, Environment.DEV) is LifecycleState.APPROVED
