from lifecycle_support import record_state
from agent_foundry.domain.lifecycle import LifecycleState, LifecycleTransition
from dataclasses import FrozenInstanceError, replace
import json
import sqlite3

import pytest

from agent_foundry.domain.approval import HumanApproval
from agent_foundry.domain.artifact import AgentArtifact
from agent_foundry.domain.deployment import DeploymentAttempt, DeploymentOutcome, DeploymentPolicy
from agent_foundry.domain.evaluation import EvaluationPolicy
from agent_foundry.domain.runtime import (
    RuntimeGrant, RuntimePolicy, ToolAuthorizationDecision, ToolAuthorizationOutcome,
    ToolPermission, ToolRequest,
)
from agent_foundry.domain.specification import Environment
from agent_foundry.persistence import EvidenceConflictError, EvidenceIntegrityError, SQLiteGovernanceStore
from agent_foundry.services.approval import ApprovalService
from agent_foundry.services.deployment import DeploymentBackendError, DeploymentNotAuthorized, DeploymentService
from agent_foundry.services.evaluation import EvaluationService
from agent_foundry.services.runtime_authorization import RuntimeAuthorizationService, RuntimeGrantNotAuthorized
from agent_foundry.services.tool_authorization import ToolAuthorizationService


PERMISSION = ToolPermission("files", "read")
RECORDS = [
    ("lifecycle_transitions", "lifecycle_transition", LifecycleTransition(
        "a", LifecycleState.BUILT, None, "validation",
    )),
    ("approvals", "approval", HumanApproval("a", "e", "p", "human-é", Environment.TEST)),
    ("deployments", "deployment", DeploymentAttempt(
        "a", "h", "p", Environment.TEST, DeploymentOutcome.FAIL, ("failure",),
    )),
    ("runtime_grants", "runtime_grant", RuntimeGrant(
        "a", "d", "p", Environment.TEST, {PERMISSION, ToolPermission("search", "query")}, "human-é",
    )),
    ("tool_decisions", "tool_decision", ToolAuthorizationDecision(
        "g", "p", "a", Environment.TEST, PERMISSION, ToolAuthorizationOutcome.DENY, ("denied",),
    )),
]


@pytest.mark.parametrize("table,kind,record", RECORDS)
def test_reopen_exact_record_idempotence_and_missing(tmp_path, table, kind, record):
    path = tmp_path / "evidence.sqlite"
    with SQLiteGovernanceStore(path) as store:
        save = getattr(store, f"save_{kind}")
        save(record)
        save(record)
        assert getattr(store, f"get_{kind}")("missing") is None
    with SQLiteGovernanceStore(path) as reopened:
        restored = getattr(reopened, f"get_{kind}")(record.digest)
        assert restored == record
        assert restored.digest == record.digest
        getattr(reopened, f"save_{kind}")(record)
    with sqlite3.connect(path) as connection:
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (1,)
        assert {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )} == {entry[0] for entry in RECORDS}


@pytest.mark.parametrize("table,kind,record", RECORDS)
@pytest.mark.parametrize("tampering", ["changed_field", "invalid_enum", "invalid_json", "extra_field", "wrong_shape"])
def test_tampering_rejected_and_conflict_never_overwrites(tmp_path, table, kind, record, tampering):
    path = tmp_path / "evidence.sqlite"
    with SQLiteGovernanceStore(path) as store:
        getattr(store, f"save_{kind}")(record)
    with sqlite3.connect(path) as connection:
        payload = json.loads(connection.execute(f"SELECT payload FROM {table}").fetchone()[0])
        if tampering == "changed_field":
            payload["artifact_digest"] = "tampered"
        elif tampering == "invalid_enum":
            payload["target_environment"] = "MOON"
        elif tampering == "extra_field":
            payload["unexpected"] = True
        elif tampering == "wrong_shape":
            payload = []
        corrupt = "{" if tampering == "invalid_json" else json.dumps(payload)
        connection.execute(f"UPDATE {table} SET payload = ?", (corrupt,))
    with SQLiteGovernanceStore(path) as store:
        with pytest.raises(EvidenceIntegrityError):
            getattr(store, f"get_{kind}")(record.digest)
        with pytest.raises(EvidenceConflictError):
            getattr(store, f"save_{kind}")(record)
    with sqlite3.connect(path) as connection:
        assert connection.execute(f"SELECT payload FROM {table}").fetchone() == (corrupt,)


def test_no_mutation_or_generic_repository_api(tmp_path):
    with SQLiteGovernanceStore(tmp_path / "evidence.sqlite") as store:
        public = {name for name in dir(store) if not name.startswith("_")}
        assert public == {"close", "get_lifecycle_state"} | {
            f"{operation}_{kind}" for operation in ("save", "get") for _, kind, _ in RECORDS
        }


def test_corrupt_database_fails_loudly(tmp_path):
    path = tmp_path / "corrupt.sqlite"
    path.write_bytes(b"not a SQLite database")
    with pytest.raises(EvidenceIntegrityError):
        SQLiteGovernanceStore(path)


class Backend:
    def __init__(self, error=None):
        self.calls = 0
        self.error = error

    def deploy(self, artifact, target_environment):
        self.calls += 1
        if self.error:
            raise self.error


@pytest.fixture
def chain():
    artifact = AgentArtifact("a" * 64)
    policy = EvaluationPolicy(artifact.source_specification_digest)
    evidence = EvaluationService().evaluate(artifact, policy)
    return artifact, policy, evidence


def deploy(store, chain, backend, digest, deployment_store=None):
    artifact, policy, evidence = chain
    return DeploymentService(backend, store, deployment_store or store, store).deploy(
        artifact, DeploymentPolicy({Environment.TEST}), Environment.TEST, digest,
        evaluation_policy=policy, evaluation_evidence=evidence,
    )


def issue(store, artifact, attempt, policy, grant_store=None):
    return RuntimeAuthorizationService(store, grant_store or store, store).issue(
        artifact, attempt.digest, policy, Environment.TEST, {PERMISSION}, "human",
    )


def test_authority_and_failure_history_survive_restarts(tmp_path, chain):
    path = tmp_path / "evidence.sqlite"
    artifact = chain[0]
    runtime_policy = RuntimePolicy({Environment.TEST}, {PERMISSION})
    backend = Backend(DeploymentBackendError("known failure"))
    with SQLiteGovernanceStore(path) as store:
        record_state(store, chain[0].digest)
        approval = ApprovalService(store, store).approve(*chain, "human", Environment.TEST)
        assert store.get_approval(approval.digest) == approval
    with SQLiteGovernanceStore(path) as store:
        failed = deploy(store, chain, backend, approval.digest)
        assert failed.outcome is DeploymentOutcome.FAIL
        assert backend.calls == 1
        backend.error = None
        succeeded = deploy(store, chain, backend, approval.digest)
        assert succeeded.outcome is DeploymentOutcome.SUCCESS
        assert backend.calls == 2
    with SQLiteGovernanceStore(path) as store:
        assert store.get_deployment(failed.digest) == failed
        assert store.get_deployment(succeeded.digest) == succeeded
        with pytest.raises(RuntimeGrantNotAuthorized, match="must be SUCCESS"):
            issue(store, artifact, failed, runtime_policy)
        fabricated_attempt = replace(succeeded, approval_digest="unrecorded")
        with pytest.raises(RuntimeGrantNotAuthorized, match="Authoritative"):
            issue(store, artifact, fabricated_attempt, runtime_policy)
        grant = issue(store, artifact, succeeded, runtime_policy)
    with SQLiteGovernanceStore(path) as store:
        assert store.get_runtime_grant(grant.digest) == grant
        request = ToolRequest(artifact.digest, Environment.TEST, PERMISSION)
        fabricated = replace(grant, grantor_id="unissued")
        service = ToolAuthorizationService(store, store, store)
        denied = service.authorize(fabricated.digest, runtime_policy, request)
        allowed = service.authorize(grant.digest, runtime_policy, request)
        assert denied.outcome is ToolAuthorizationOutcome.DENY
        assert allowed.outcome is ToolAuthorizationOutcome.ALLOW
        assert denied.digest != allowed.digest
        with pytest.raises(FrozenInstanceError):
            denied.outcome = ToolAuthorizationOutcome.ALLOW
    with SQLiteGovernanceStore(path) as store:
        for decision in (denied, allowed):
            restored = store.get_tool_decision(decision.digest)
            assert restored == decision
            assert restored.digest == decision.digest
        assert store.get_deployment(failed.digest) == failed


@pytest.mark.parametrize("authority", ["fabricated_digest", "object", "unknown"])
def test_unrecorded_approval_never_calls_backend(tmp_path, chain, authority):
    artifact, policy, evidence = chain
    approval = HumanApproval(artifact.digest, evidence.digest, policy.digest, "human", Environment.TEST)
    value = {"fabricated_digest": approval.digest, "object": approval, "unknown": "unknown"}[authority]
    backend = Backend()
    with SQLiteGovernanceStore(tmp_path / "evidence.sqlite") as store:
        with pytest.raises(DeploymentNotAuthorized):
            deploy(store, chain, backend, value)
    assert backend.calls == 0


class BrokenStore:
    def __init__(self):
        self.error = OSError("Evidence write failed")
        self.records = []

    def _fail(self, record):
        self.records.append(record)
        raise self.error

    save_approval = _fail
    save_deployment = _fail
    save_runtime_grant = _fail
    save_tool_decision = _fail


def test_approval_persistence_failure_prevents_issuance(chain, tmp_path):
    store = BrokenStore()
    with pytest.raises(OSError) as raised:
        with SQLiteGovernanceStore(tmp_path / "lifecycle.sqlite") as lifecycle:
            record_state(lifecycle, chain[0].digest)
            ApprovalService(store, lifecycle).approve(*chain, "human", Environment.TEST)
    assert raised.value is store.error
    assert len(store.records) == 1


@pytest.mark.parametrize("backend_error", [None, DeploymentBackendError("failed")])
def test_deployment_evidence_failure_propagates_without_retry(tmp_path, chain, backend_error):
    broken = BrokenStore()
    backend = Backend(backend_error)
    with SQLiteGovernanceStore(tmp_path / "evidence.sqlite") as store:
        record_state(store, chain[0].digest)
        approval = ApprovalService(store, store).approve(*chain, "human", Environment.TEST)
        with pytest.raises(OSError) as raised:
            deploy(store, chain, backend, approval.digest, broken)
        assert raised.value is broken.error
        assert backend.calls == 1
        assert len(broken.records) == 1
        attempt = broken.records[0]
        assert attempt.outcome is (DeploymentOutcome.FAIL if backend_error else DeploymentOutcome.SUCCESS)
        assert store.get_deployment(attempt.digest) is None


def test_unexpected_backend_error_propagates_without_evidence_or_retry(tmp_path, chain):
    error = RuntimeError("unexpected")
    backend, sink = Backend(error), BrokenStore()
    with SQLiteGovernanceStore(tmp_path / "evidence.sqlite") as store:
        record_state(store, chain[0].digest)
        approval = ApprovalService(store, store).approve(*chain, "human", Environment.TEST)
        with pytest.raises(RuntimeError) as raised:
            deploy(store, chain, backend, approval.digest, sink)
        assert raised.value is error
        assert backend.calls == 1
        assert sink.records == []


def test_grant_write_failure_is_not_issued_authority(tmp_path, chain):
    broken = BrokenStore()
    artifact = chain[0]
    policy = RuntimePolicy({Environment.TEST}, {PERMISSION})
    with SQLiteGovernanceStore(tmp_path / "evidence.sqlite") as store:
        record_state(store, chain[0].digest)
        approval = ApprovalService(store, store).approve(*chain, "human", Environment.TEST)
        attempt = deploy(store, chain, Backend(), approval.digest)
        with pytest.raises(OSError) as raised:
            issue(store, artifact, attempt, policy, broken)
        assert raised.value is broken.error
        grant, = broken.records
        assert store.get_runtime_grant(grant.digest) is None
        decision = ToolAuthorizationService(store, store, store).authorize(
            grant.digest, policy, ToolRequest(artifact.digest, Environment.TEST, PERMISSION),
        )
        assert decision.outcome is ToolAuthorizationOutcome.DENY


@pytest.mark.parametrize("issued", [True, False])
def test_decision_write_failure_prevents_return(tmp_path, chain, issued):
    broken = BrokenStore()
    policy = RuntimePolicy({Environment.TEST}, {PERMISSION})
    with SQLiteGovernanceStore(tmp_path / "evidence.sqlite") as store:
        record_state(store, chain[0].digest)
        approval = ApprovalService(store, store).approve(*chain, "human", Environment.TEST)
        attempt = deploy(store, chain, Backend(), approval.digest)
        grant = issue(store, chain[0], attempt, policy)
        with pytest.raises(OSError) as raised:
            ToolAuthorizationService(store, broken, store).authorize(
                grant.digest if issued else "unknown", policy,
                ToolRequest(chain[0].digest, Environment.TEST, PERMISSION),
            )
        assert raised.value is broken.error
        decision, = broken.records
        assert decision.outcome is (ToolAuthorizationOutcome.ALLOW if issued else ToolAuthorizationOutcome.DENY)
        assert store.get_tool_decision(decision.digest) is None
