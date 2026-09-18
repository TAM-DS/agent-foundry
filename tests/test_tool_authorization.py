from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import json

import pytest

from agent_foundry.domain.runtime import ToolAuthorizationOutcome, ToolPermission, ToolRequest
from agent_foundry.domain.specification import Environment
from agent_foundry.services.tool_authorization import ToolAuthorizationService
from test_runtime_authorization import inputs, issue  # Shared local fakes and issuance fixture.


@pytest.fixture
def authorized(inputs):
    grant = issue(inputs)
    request = ToolRequest(grant.artifact_digest, grant.target_environment, ToolPermission("files", "read"))
    return grant, inputs[2], request, inputs[-1]


def test_exact_issued_permission_is_allowed_and_canonical(authorized):
    grant, policy, request, store = authorized
    service = ToolAuthorizationService(store)
    decision = service.authorize(grant.digest, policy, request)
    assert decision.outcome is ToolAuthorizationOutcome.ALLOW
    assert decision.reasons == ()
    canonical = json.dumps({
        "runtime_grant_digest": grant.digest, "runtime_policy_digest": policy.digest,
        "artifact_digest": request.artifact_digest, "target_environment": "TEST",
        "requested_permission": {"tool": "files", "action": "read"},
        "outcome": "ALLOW", "reasons": [],
    }, sort_keys=True, separators=(",", ":"))
    assert decision.digest == sha256(canonical.encode()).hexdigest()
    assert decision == service.authorize(grant.digest, policy, request)


@pytest.mark.parametrize(("change", "reason"), [
    ("omitted_tool", "not explicitly granted"), ("action", "not explicitly granted"),
    ("cross_pair", "not explicitly granted"), ("fabricated", "Authoritative issued"),
    ("missing", "Authoritative issued"), ("wrong_lookup", "Authoritative issued"),
    ("artifact", "match grant artifact"), ("environment", "match grant environment"),
    ("policy_permission", "not allowed by runtime policy"),
    ("policy_environment", "Environment is not allowed"),
    ("policy_expansion", "does not match runtime policy"),
])
def test_authority_boundaries_deny_deterministically(authorized, change, reason):
    grant, policy, request, store = authorized
    digest = grant.digest
    if change == "omitted_tool":
        request = replace(request, permission=ToolPermission("search", "query"))
        assert request.permission in policy.allowed_permissions
    elif change == "action":
        request = replace(request, permission=ToolPermission("files", "write"))
    elif change == "cross_pair":
        request = replace(request, permission=ToolPermission("files", "query"))
    elif change == "fabricated":
        fabricated = replace(grant, permissions=policy.allowed_permissions)
        digest = fabricated.digest
        request = replace(request, permission=ToolPermission("search", "query"))
        assert request.permission in fabricated.permissions
    elif change == "missing":
        digest = "unknown"
    elif change == "wrong_lookup":
        store.grants[digest] = replace(grant, permissions=policy.allowed_permissions)
    elif change == "artifact":
        request = replace(request, artifact_digest="other")
    elif change == "environment":
        request = replace(request, target_environment=Environment.PROD)
    elif change == "policy_permission":
        policy = replace(policy, allowed_permissions=set())
    elif change == "policy_environment":
        policy = replace(policy, allowed_environments=set())
    elif change == "policy_expansion":
        policy = replace(policy, allowed_environments={Environment.TEST, Environment.PROD})
    service = ToolAuthorizationService(store)
    decision = service.authorize(digest, policy, request)
    assert decision.outcome is ToolAuthorizationOutcome.DENY
    assert any(reason in entry for entry in decision.reasons)
    assert decision == service.authorize(digest, policy, request)
    assert decision.runtime_grant_digest == digest
    assert decision.runtime_policy_digest == policy.digest


def test_caller_cannot_supply_grant_object_as_authority(authorized):
    grant, policy, request, store = authorized
    with pytest.raises(TypeError, match="digest must be a string"):
        ToolAuthorizationService(store).authorize(grant, policy, request)


def test_denial_survives_later_allow_as_separate_immutable_evidence(authorized):
    grant, policy, request, store = authorized
    service = ToolAuthorizationService(store)
    denied = service.authorize("not-issued", policy, request)
    original_digest = denied.digest
    allowed = service.authorize(grant.digest, policy, request)
    assert denied.outcome is ToolAuthorizationOutcome.DENY
    assert allowed.outcome is ToolAuthorizationOutcome.ALLOW
    assert denied is not allowed
    assert denied.digest == original_digest != allowed.digest
    with pytest.raises(FrozenInstanceError):
        denied.outcome = ToolAuthorizationOutcome.ALLOW


@pytest.mark.parametrize("changes", [
    {"runtime_grant_digest": "other"}, {"runtime_policy_digest": "other"},
    {"artifact_digest": "other"}, {"target_environment": Environment.PROD},
    {"requested_permission": ToolPermission("files", "write")},
    {"outcome": ToolAuthorizationOutcome.DENY}, {"reasons": ["denied"]},
])
def test_decision_digest_binds_every_field_and_is_immutable(authorized, changes):
    grant, policy, request, store = authorized
    decision = ToolAuthorizationService(store).authorize(grant.digest, policy, request)
    assert replace(decision, **changes).digest != decision.digest
    for field, value in changes.items():
        with pytest.raises(FrozenInstanceError):
            setattr(decision, field, value)


def test_decision_copies_reasons_and_request_is_immutable(authorized):
    grant, policy, request, store = authorized
    decision = ToolAuthorizationService(store).authorize(grant.digest, policy, request)
    reasons = ["denied"]
    denied = replace(decision, outcome=ToolAuthorizationOutcome.DENY, reasons=reasons)
    digest = denied.digest
    reasons.clear()
    assert denied.reasons == ("denied",)
    assert denied.digest == digest
    for field in request.__dataclass_fields__:
        with pytest.raises(FrozenInstanceError):
            setattr(request, field, None)
