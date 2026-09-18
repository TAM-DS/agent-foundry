from test_approval import governance_store
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import json
from unittest.mock import patch

import pytest

from agent_foundry.domain.artifact import AgentArtifact
from agent_foundry.domain.deployment import DeploymentAttempt, DeploymentOutcome, DeploymentPolicy
from agent_foundry.domain.evaluation import EvaluationPolicy
from agent_foundry.domain.runtime import RuntimeGrant, RuntimePolicy, ToolPermission
from agent_foundry.domain.specification import Environment
from agent_foundry.services.approval import ApprovalService
from agent_foundry.services.deployment import DeploymentService
from agent_foundry.services.evaluation import EvaluationService
from agent_foundry.services.runtime_authorization import RuntimeAuthorizationService, RuntimeGrantNotAuthorized


class FakeEvidenceSource:
    def __init__(self, *attempts):
        self.attempts = {attempt.digest: attempt for attempt in attempts}

    def get_deployment(self, digest):
        return self.attempts.get(digest)


class FakeGrantStore:
    def __init__(self):
        self.grants = {}
        self.saved = []

    def save_runtime_grant(self, grant):
        self.saved.append(grant)
        self.grants[grant.digest] = grant

    def get_runtime_grant(self, digest):
        return self.grants.get(digest)


@pytest.fixture
def inputs():
    artifact = AgentArtifact("a" * 64)
    attempt = DeploymentAttempt(
        artifact.digest, "approval", "deployment-policy", Environment.TEST,
        DeploymentOutcome.SUCCESS, (),
    )
    policy = RuntimePolicy({Environment.TEST}, {ToolPermission("files", "read"), ToolPermission("search", "query")})
    source, store = FakeEvidenceSource(attempt), FakeGrantStore()
    return artifact, attempt, policy, source, store


def issue(inputs, **changes):
    artifact, attempt, policy, source, store = inputs
    arguments = dict(
        artifact=artifact, deployment_attempt_digest=attempt.digest, policy=policy,
        target_environment=Environment.TEST, permissions=[ToolPermission("files", "read")],
        grantor_id="human-é",
    )
    arguments.update(changes)
    return RuntimeAuthorizationService(source, store).issue(**arguments)


@pytest.mark.parametrize("field", ["tool", "action"])
@pytest.mark.parametrize("value", ["", " ", "\n\t", None, 42])
def test_permission_requires_nonblank_strings(field, value):
    with pytest.raises(ValueError, match="nonblank string"):
        ToolPermission(**({"tool": "files", "action": "read"} | {field: value}))


def test_permission_identity_is_exact_pair():
    permissions = {ToolPermission("files", "read"), ToolPermission("search", "write")}
    assert ToolPermission("files", "write") not in permissions
    assert ToolPermission("search", "read") not in permissions
    assert len({p.digest for p in permissions}) == 2
    assert ToolPermission("files", "read").digest == sha256(b'{"action":"read","tool":"files"}').hexdigest()
    with pytest.raises(FrozenInstanceError):
        ToolPermission("files", "read").action = "write"


def test_policy_is_immutable_canonical_and_copies_inputs():
    permissions = [ToolPermission("search", "query"), ToolPermission("files", "read")]
    environments = [Environment.TEST, Environment.DEV, Environment.TEST]
    policy = RuntimePolicy(environments, permissions + permissions)
    equivalent = RuntimePolicy(reversed(environments), reversed(permissions))
    permissions.clear()
    environments.clear()
    assert policy == equivalent
    canonical = b'{"allowed_environments":["DEV","TEST"],"allowed_permissions":[{"action":"read","tool":"files"},{"action":"query","tool":"search"}]}'
    assert policy.digest == equivalent.digest == sha256(canonical).hexdigest()
    for field in policy.__dataclass_fields__:
        with pytest.raises(FrozenInstanceError):
            setattr(policy, field, frozenset())
    assert replace(policy, allowed_environments={Environment.PROD}).digest != policy.digest
    assert replace(policy, allowed_permissions=set()).digest != policy.digest


def test_explicit_issuance_saves_exact_narrower_canonical_grant(inputs):
    artifact, attempt, policy, _, store = inputs
    permissions = [ToolPermission("files", "read")] * 2
    grant = issue(inputs, permissions=permissions)
    permissions.clear()
    assert grant.permissions < policy.allowed_permissions
    assert store.saved == [grant]
    assert store.get_runtime_grant(grant.digest) is grant
    canonical = json.dumps({
        "artifact_digest": artifact.digest, "deployment_attempt_digest": attempt.digest,
        "runtime_policy_digest": policy.digest, "target_environment": "TEST",
        "permissions": [{"tool": "files", "action": "read"}], "grantor_id": "human-é",
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert grant.digest == replace(grant).digest == sha256(canonical.encode()).hexdigest()
    full = issue(inputs, permissions=list(policy.allowed_permissions) * 2)
    assert full.digest == replace(full, permissions=reversed(list(full.permissions))).digest


@pytest.mark.parametrize("changes", [
    {"artifact_digest": "other"}, {"deployment_attempt_digest": "other"},
    {"runtime_policy_digest": "other"}, {"target_environment": Environment.PROD},
    {"permissions": {ToolPermission("files", "write")}}, {"grantor_id": "human-2"},
])
def test_grant_immutable_and_digest_binds_every_field(inputs, changes):
    grant = issue(inputs)
    assert replace(grant, **changes).digest != grant.digest
    for field, value in changes.items():
        with pytest.raises(FrozenInstanceError):
            setattr(grant, field, value)


def test_deployment_success_does_not_issue_grant(inputs, governance_store):
    artifact, _, _, _, store = inputs
    evaluation_policy = EvaluationPolicy(artifact.source_specification_digest)
    evidence = EvaluationService().evaluate(artifact, evaluation_policy)
    approval = ApprovalService(governance_store).approve(artifact, evaluation_policy, evidence, "human", Environment.TEST)

    class Backend:
        def deploy(self, artifact, target_environment):
            pass

    with patch.object(RuntimeAuthorizationService, "issue", side_effect=AssertionError("implicit issuance")):
        with patch("agent_foundry.services.runtime_authorization.RuntimeGrant") as constructor:
            attempt = DeploymentService(Backend(), governance_store, governance_store).deploy(
                artifact, DeploymentPolicy({Environment.TEST}), Environment.TEST, approval.digest,
                evaluation_policy=evaluation_policy, evaluation_evidence=evidence,
            )
            constructor.assert_not_called()
    assert attempt.outcome is DeploymentOutcome.SUCCESS
    assert not isinstance(attempt, RuntimeGrant)
    assert store.saved == []


@pytest.mark.parametrize(("change", "reason"), [
    ("failed", "must be SUCCESS"), ("fabricated", "Authoritative"),
    ("wrong_lookup", "Authoritative"), ("object", "digest must be a string"),
    ("artifact", "match artifact"), ("environment", "match target environment"),
    ("invalid_environment", "must be an Environment"),
    ("policy_environment", "Environment is not allowed"),
    ("permission", "permission is not allowed"),
    ("cross_pair", "permission is not allowed"),
    ("invalid_permission", "ToolPermission values"),
])
def test_unauthorized_issuance_never_saves(inputs, change, reason):
    artifact, attempt, policy, source, store = inputs
    changes = {}
    if change == "failed":
        failed = replace(attempt, outcome=DeploymentOutcome.FAIL, reasons=("failed",))
        source.attempts[failed.digest] = failed
        changes["deployment_attempt_digest"] = failed.digest
    elif change == "fabricated":
        changes["deployment_attempt_digest"] = replace(attempt, approval_digest="fabricated").digest
    elif change == "wrong_lookup":
        source.attempts[attempt.digest] = replace(attempt, approval_digest="other")
    elif change == "object":
        changes["deployment_attempt_digest"] = attempt
    elif change == "artifact":
        changes["artifact"] = AgentArtifact("b" * 64)
    elif change == "environment":
        changes["target_environment"] = Environment.PROD
    elif change == "invalid_environment":
        changes["target_environment"] = "TEST"
    elif change == "policy_environment":
        changes["policy"] = replace(policy, allowed_environments={Environment.PROD})
    elif change == "permission":
        changes["permissions"] = [ToolPermission("files", "write")]
    elif change == "cross_pair":
        changes["permissions"] = [ToolPermission("files", "query")]
    elif change == "invalid_permission":
        changes["permissions"] = ["files"]
    with pytest.raises(RuntimeGrantNotAuthorized, match=reason):
        issue(inputs, **changes)
    assert store.saved == []


@pytest.mark.parametrize("identity", ["", " ", "\n\t", None, 42])
def test_blank_or_invalid_grantor_cannot_issue(inputs, identity):
    with pytest.raises(RuntimeGrantNotAuthorized, match="nonblank string"):
        issue(inputs, grantor_id=identity)
    assert inputs[-1].saved == []


def test_empty_grant_does_not_expand_permissions(inputs):
    assert issue(inputs, permissions=[]).permissions == frozenset()


@pytest.mark.parametrize("changes", [
    {"allowed_environments": ["TEST"]}, {"allowed_permissions": ["files"]},
])
def test_policy_rejects_invalid_types(inputs, changes):
    with pytest.raises(TypeError):
        replace(inputs[2], **changes)
