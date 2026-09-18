from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import inspect
import json
import sqlite3

import pytest

from agent_foundry.domain.artifact import AgentArtifact
from agent_foundry.domain.deployment import DeploymentOutcome, DeploymentPolicy
from agent_foundry.domain.evaluation import EvaluationPolicy
from agent_foundry.domain.lifecycle import LifecycleState, LifecycleStore, LifecycleTransition
from agent_foundry.domain.runtime import RuntimePolicy, ToolAuthorizationOutcome, ToolPermission, ToolRequest
from agent_foundry.domain.specification import AgentSpecification, Environment
from agent_foundry.persistence import EvidenceIntegrityError, SQLiteGovernanceStore
from agent_foundry.services.agent_build import AgentBuildService, BuildNotAuthorized
from agent_foundry.services.approval import ApprovalNotAuthorized, ApprovalService
from agent_foundry.services.deployment import DeploymentBackendError, DeploymentNotAuthorized, DeploymentService
from agent_foundry.services.evaluation import EvaluationService
from agent_foundry.services.runtime_authorization import RuntimeAuthorizationService, RuntimeGrantNotAuthorized
from agent_foundry.services.specification_validation import SpecificationPolicy, SpecificationValidationService, ValidationOutcome
from agent_foundry.services.tool_authorization import ToolAuthorizationService


TEST, PROD = Environment.TEST, Environment.PROD
BUILT, APPROVED, DEPLOYED, OPERATING = LifecycleState
PERMISSION = ToolPermission("files", "read")


@pytest.fixture
def store(tmp_path):
    with SQLiteGovernanceStore(tmp_path / "lifecycle.sqlite") as value:
        yield value


@pytest.fixture
def build_inputs():
    specification = AgentSpecification("Read files", ("files",), TEST)
    policy = SpecificationPolicy({"files"}, {TEST})
    return specification, policy, SpecificationValidationService().validate(specification, policy)


class Backend:
    def __init__(self):
        self.calls = 0
        self.fail = False

    def deploy(self, artifact, environment):
        self.calls += 1
        if self.fail:
            raise DeploymentBackendError("failed")


class ObservedStore:
    """Fault injection observes committed authority at the lifecycle boundary."""

    def __init__(self, store):
        self.store = store
        self.fail_state = None
        self.fail_authority = None
        self.events = []

    def __getattr__(self, name):
        return getattr(self.store, name)

    def _save(self, kind, record):
        self.events.append(kind)
        if self.fail_authority == kind:
            raise OSError("authority write failed")
        getattr(self.store, f"save_{kind}")(record)

    def save_approval(self, record):
        self._save("approval", record)

    def save_deployment(self, record):
        self._save("deployment", record)

    def save_runtime_grant(self, record):
        self._save("runtime_grant", record)

    def save_lifecycle_transition(self, transition):
        self.events.append(transition.state)
        if transition.state is not BUILT:
            kind = {APPROVED: "approval", DEPLOYED: "deployment", OPERATING: "runtime_grant"}[transition.state]
            authority = getattr(self.store, f"get_{kind}")(transition.authority_digest)
            assert authority is not None
            assert authority.artifact_digest == transition.artifact_digest
            assert authority.target_environment is transition.target_environment
            if transition.state is DEPLOYED:
                assert authority.outcome is DeploymentOutcome.SUCCESS
        if self.fail_state is transition.state:
            raise OSError("lifecycle write failed")
        self.store.save_lifecycle_transition(transition)


class Chain:
    def __init__(self, store, build_inputs):
        self.store = ObservedStore(store)
        self.artifact = AgentBuildService(self.store).build(*build_inputs)
        self.policy = EvaluationPolicy(self.artifact.source_specification_digest)
        self.evidence = EvaluationService().evaluate(self.artifact, self.policy)
        self.backend = Backend()
        self.runtime_policy = RuntimePolicy({TEST, PROD}, {PERMISSION})

    def approve(self, env=TEST):
        return ApprovalService(self.store, self.store).approve(
            self.artifact, self.policy, self.evidence, "human", env,
        )

    def deploy(self, approval, env=TEST):
        return DeploymentService(self.backend, self.store, self.store, self.store).deploy(
            self.artifact, DeploymentPolicy({TEST, PROD}), env, approval.digest,
            evaluation_policy=self.policy, evaluation_evidence=self.evidence,
        )

    def issue(self, attempt, env=TEST):
        return RuntimeAuthorizationService(self.store, self.store, self.store).issue(
            self.artifact, attempt.digest, self.runtime_policy, env, {PERMISSION}, "human",
        )

    def authorize(self, grant, env=TEST):
        return ToolAuthorizationService(self.store, self.store, self.store).authorize(
            grant.digest, self.runtime_policy, ToolRequest(self.artifact.digest, env, PERMISSION),
        )

    def state(self, env=TEST):
        return self.store.get_lifecycle_state(self.artifact.digest, env)


@pytest.fixture
def chain(store, build_inputs):
    return Chain(store, build_inputs)


def test_transition_is_immutable_and_canonical():
    record = LifecycleTransition("artifact", APPROVED, TEST, "authority-é")
    canonical = json.dumps({
        "artifact_digest": "artifact", "state": APPROVED.value,
        "target_environment": "TEST", "authority_digest": "authority-é",
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert record.digest == replace(record).digest == sha256(canonical.encode()).hexdigest()
    for field, value in {
        "artifact_digest": "other", "state": DEPLOYED,
        "target_environment": PROD, "authority_digest": "other",
    }.items():
        assert replace(record, **{field: value}).digest != record.digest
        with pytest.raises(FrozenInstanceError):
            setattr(record, field, value)
    assert list(LifecycleState) == [BUILT, APPROVED, DEPLOYED, OPERATING]
    assert BUILT < APPROVED < DEPLOYED < OPERATING
    assert "previous_state" not in record.__dataclass_fields__


@pytest.mark.parametrize("state", list(LifecycleState))
@pytest.mark.parametrize("environment", [None, TEST, "TEST", 1])
def test_transition_environment_contract(state, environment):
    valid = (state is BUILT and environment is None) or (state is not BUILT and environment is TEST)
    if valid:
        LifecycleTransition("a", state, environment, "v")
    else:
        with pytest.raises((TypeError, ValueError)):
            LifecycleTransition("a", state, environment, "v")


@pytest.mark.parametrize("field", ["artifact_digest", "authority_digest"])
@pytest.mark.parametrize("value", [None, 1, "", " \n", [], {}])
def test_invalid_digests(field, value):
    with pytest.raises(ValueError):
        replace(LifecycleTransition("a", BUILT, None, "v"), **{field: value})


@pytest.mark.parametrize("state", [None, 1, True, "BUILT", "custom"])
def test_invalid_states(state):
    with pytest.raises(TypeError):
        LifecycleTransition("a", state, None, "v")


def test_append_only_projection_requires_built_and_is_environment_specific(store):
    records = [LifecycleTransition("a", state, env, str(state)) for state, env in [
        (OPERATING, TEST), (APPROVED, PROD), (BUILT, None), (DEPLOYED, TEST), (APPROVED, TEST),
    ]]
    for record in records[:2]:
        store.save_lifecycle_transition(record)
    assert store.get_lifecycle_state("a", TEST) is None
    for record in records[2:]:
        store.save_lifecycle_transition(record)
    assert store.get_lifecycle_state("a", TEST) is OPERATING
    assert store.get_lifecycle_state("a", PROD) is APPROVED
    assert store.get_lifecycle_state("a", Environment.DEV) is BUILT
    assert store.get_lifecycle_state("a", None) is BUILT
    assert store.get_lifecycle_state("other", TEST) is None
    for record in records:
        assert store.get_lifecycle_transition(record.digest) == record
    assert {name for name in vars(LifecycleStore) if not name.startswith("_")} == {
        "save_lifecycle_transition", "get_lifecycle_transition", "get_lifecycle_state",
    }


def test_tampering_cannot_hide_from_state_query(tmp_path):
    path = tmp_path / "tampered.sqlite"
    with SQLiteGovernanceStore(path) as store:
        record = LifecycleTransition("a", BUILT, None, "v")
        store.save_lifecycle_transition(record)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE lifecycle_transitions SET payload = ?", ('{}',))
    with SQLiteGovernanceStore(path) as store:
        with pytest.raises(EvidenceIntegrityError):
            store.get_lifecycle_state("a", TEST)


def test_build_records_exact_validation_before_return(store, build_inputs):
    artifact = AgentBuildService(store).build(*build_inputs)
    record = LifecycleTransition(artifact.digest, BUILT, None, build_inputs[2].digest)
    assert store.get_lifecycle_transition(record.digest) == record
    assert store.get_lifecycle_state(artifact.digest, TEST) is BUILT
    evidence = build_inputs[2]
    canonical = json.dumps({
        "specification_digest": evidence.specification_digest,
        "policy_digest": evidence.policy_digest, "outcome": "PASS", "reasons": [],
    }, sort_keys=True, separators=(",", ":"))
    assert evidence.digest == sha256(canonical.encode()).hexdigest()
    for field, value in {
        "specification_digest": "other", "policy_digest": "other",
        "outcome": ValidationOutcome.FAIL, "reasons": ("failure",),
    }.items():
        assert replace(evidence, **{field: value}).digest != evidence.digest


@pytest.mark.parametrize("fabricated", [False, True])
def test_invalid_build_never_records_state(store, build_inputs, fabricated):
    specification, policy, _ = build_inputs
    specification = replace(specification, requested_tools=("forbidden",))
    evidence = SpecificationValidationService().validate(specification, policy)
    if fabricated:
        evidence = replace(evidence, outcome=ValidationOutcome.PASS, reasons=())
    observed = ObservedStore(store)
    with pytest.raises(BuildNotAuthorized):
        AgentBuildService(observed).build(specification, policy, evidence)
    assert observed.events == []
    assert store.get_lifecycle_state(AgentArtifact(specification.digest).digest, TEST) is None


def test_build_write_failure_prevents_return(store, build_inputs):
    observed = ObservedStore(store)
    observed.fail_state = BUILT
    with pytest.raises(OSError, match="lifecycle"):
        AgentBuildService(observed).build(*build_inputs)
    assert store.get_lifecycle_state(AgentArtifact(build_inputs[0].digest).digest, TEST) is None


def test_unbuilt_artifact_cannot_be_approved(store):
    artifact = AgentArtifact("a" * 64)
    policy = EvaluationPolicy(artifact.source_specification_digest)
    evidence = EvaluationService().evaluate(artifact, policy)
    observed = ObservedStore(store)
    with pytest.raises(ApprovalNotAuthorized, match="BUILT"):
        ApprovalService(observed, observed).approve(artifact, policy, evidence, "human", TEST)
    assert observed.events == []


@pytest.mark.parametrize("stage", [APPROVED, DEPLOYED, OPERATING])
@pytest.mark.parametrize("failure", ["authority", "lifecycle"])
def test_evidence_first_failures_leave_conservative_state(chain, stage, failure):
    prior = BUILT
    if stage >= DEPLOYED:
        approval = chain.approve()
        prior = APPROVED
    if stage >= OPERATING:
        attempt = chain.deploy(approval)
        prior = DEPLOYED
    chain.store.events.clear()
    kind = {APPROVED: "approval", DEPLOYED: "deployment", OPERATING: "runtime_grant"}[stage]
    if failure == "authority":
        chain.store.fail_authority = kind
    else:
        chain.store.fail_state = stage
    with pytest.raises(OSError, match=failure):
        if stage is APPROVED:
            chain.approve()
        elif stage is DEPLOYED:
            chain.deploy(approval)
        else:
            chain.issue(attempt)
    assert chain.state() is prior
    assert chain.store.events == ([kind] if failure == "authority" else [kind, stage])
    assert chain.backend.calls == (0 if stage is APPROVED else 1)
    table = {APPROVED: "approvals", DEPLOYED: "deployments", OPERATING: "runtime_grants"}[stage]
    rows = chain.store.store._connection.execute(f"SELECT digest FROM {table}").fetchall()
    if failure == "authority":
        assert rows == []
        return
    assert len(rows) == 1
    durable = getattr(chain.store, f"get_{kind}")(rows[0][0])
    transition = LifecycleTransition(chain.artifact.digest, stage, TEST, durable.digest)
    assert chain.store.get_lifecycle_transition(transition.digest) is None
    if stage is APPROVED:
        with pytest.raises(DeploymentNotAuthorized, match="APPROVED"):
            chain.deploy(durable)
        assert chain.backend.calls == 0
        chain.store.fail_state = None
        assert chain.approve() == durable
        assert chain.state() is APPROVED
        assert chain.store.get_lifecycle_transition(transition.digest) == transition
        assert chain.store.store._connection.execute("SELECT COUNT(*) FROM approvals").fetchone() == (1,)
    elif stage is DEPLOYED:
        with pytest.raises(RuntimeGrantNotAuthorized, match="DEPLOYED"):
            chain.issue(durable)
        assert chain.backend.calls == 1
    else:
        decision = chain.authorize(durable)
        assert decision.outcome is ToolAuthorizationOutcome.DENY
        assert "Artifact is not in OPERATING lifecycle state." in decision.reasons
        assert chain.store.get_tool_decision(decision.digest) == decision


def test_full_chain_exact_authority_environment_isolation_and_monotonicity(chain):
    approval = chain.approve()
    prod_approval = chain.approve(PROD)
    chain.backend.fail = True
    failed = chain.deploy(approval)
    assert failed.outcome is DeploymentOutcome.FAIL
    assert chain.state() is APPROVED
    chain.backend.fail = False
    succeeded = chain.deploy(approval)
    assert chain.state() is DEPLOYED
    assert chain.state(PROD) is APPROVED
    grant = chain.issue(succeeded)
    assert chain.state() is OPERATING
    assert chain.state(PROD) is APPROVED
    assert chain.store.events == [
        BUILT, "approval", APPROVED, "approval", APPROVED,
        "deployment", "deployment", DEPLOYED, "runtime_grant", OPERATING,
    ]
    for state, authority in [(APPROVED, approval), (DEPLOYED, succeeded), (OPERATING, grant)]:
        record = LifecycleTransition(chain.artifact.digest, state, TEST, authority.digest)
        assert chain.store.get_lifecycle_transition(record.digest) == record
    denied = chain.authorize(grant, PROD)
    assert denied.outcome is ToolAuthorizationOutcome.DENY
    assert any("OPERATING" in reason for reason in denied.reasons)
    allowed = chain.authorize(grant)
    assert allowed.outcome is ToolAuthorizationOutcome.ALLOW
    assert denied.digest != allowed.digest
    assert chain.store.get_tool_decision(denied.digest) == denied
    chain.backend.fail = True
    assert chain.deploy(approval).outcome is DeploymentOutcome.FAIL
    assert chain.state() is OPERATING
    assert chain.approve() == approval
    assert chain.state() is OPERATING
    assert chain.store.get_approval(prod_approval.digest) == prod_approval
    assert chain.store.get_deployment(failed.digest) == failed


@pytest.mark.parametrize("state", [None, BUILT, APPROVED, DEPLOYED, OPERATING])
def test_tool_queries_store_and_state_never_replaces_grant(chain, state):
    approval = chain.approve()
    attempt = chain.deploy(approval)
    grant = chain.issue(attempt)
    # A separate trusted projection simulates a lagging lifecycle store.
    with SQLiteGovernanceStore(":memory:") as projection:
        if state is not None:
            projection.save_lifecycle_transition(LifecycleTransition(chain.artifact.digest, BUILT, None, "v"))
        if state not in (None, BUILT):
            projection.save_lifecycle_transition(LifecycleTransition(chain.artifact.digest, state, TEST, "a"))
        service = ToolAuthorizationService(chain.store, chain.store, projection)
        request = ToolRequest(chain.artifact.digest, TEST, PERMISSION)
        decision = service.authorize(grant.digest, chain.runtime_policy, request)
        assert decision.outcome is (ToolAuthorizationOutcome.ALLOW if state is OPERATING else ToolAuthorizationOutcome.DENY)
        if state is not OPERATING:
            assert any("not in OPERATING" in reason for reason in decision.reasons)
        assert service.authorize("fabricated", chain.runtime_policy, request).outcome is ToolAuthorizationOutcome.DENY
        assert list(inspect.signature(service.authorize).parameters) == ["runtime_grant_digest", "policy", "request"]


@pytest.mark.parametrize("state", [None, BUILT, APPROVED])
def test_lower_projection_blocks_backend_and_runtime_despite_authority(chain, state):
    approval = chain.approve()
    attempt = chain.deploy(approval)
    chain.backend.calls = 0
    with SQLiteGovernanceStore(":memory:") as projection:
        if state is not None:
            projection.save_lifecycle_transition(LifecycleTransition(chain.artifact.digest, BUILT, None, "v"))
        if state is APPROVED:
            projection.save_lifecycle_transition(LifecycleTransition(chain.artifact.digest, APPROVED, TEST, approval.digest))
        if state in (None, BUILT):
            with pytest.raises(DeploymentNotAuthorized, match="APPROVED"):
                DeploymentService(chain.backend, chain.store, chain.store, projection).deploy(
                    chain.artifact, DeploymentPolicy({TEST}), TEST, approval.digest,
                    evaluation_policy=chain.policy, evaluation_evidence=chain.evidence,
                )
            assert chain.backend.calls == 0
        with pytest.raises(RuntimeGrantNotAuthorized, match="DEPLOYED"):
            RuntimeAuthorizationService(chain.store, chain.store, projection).issue(
                chain.artifact, attempt.digest, chain.runtime_policy, TEST, {PERMISSION}, "human",
            )
        assert chain.store.store._connection.execute("SELECT COUNT(*) FROM runtime_grants").fetchone() == (0,)


@pytest.mark.parametrize("environment,other", [(TEST, PROD), (PROD, TEST)])
def test_operating_projection_survives_reopen_without_cross_environment_advancement(tmp_path, environment, other):
    path = tmp_path / "reopen.sqlite"
    records = [LifecycleTransition("artifact", state, env, str(state)) for state, env in [
        (BUILT, None), (APPROVED, other), (OPERATING, environment), (DEPLOYED, environment),
    ]]
    with SQLiteGovernanceStore(path) as store:
        for record in records:
            store.save_lifecycle_transition(record)
            store.save_lifecycle_transition(record)
    with SQLiteGovernanceStore(path) as store:
        assert store.get_lifecycle_state("artifact", environment) is OPERATING
        assert store.get_lifecycle_state("artifact", other) is APPROVED
        for record in records:
            assert store.get_lifecycle_transition(record.digest) == record
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM lifecycle_transitions").fetchone() == (4,)
