from dataclasses import FrozenInstanceError, replace
from hashlib import sha256

import pytest

from agent_foundry.domain.specification import AgentSpecification, Environment
from agent_foundry.services.specification_validation import (
    SpecificationPolicy, SpecificationValidationService, ValidationOutcome,
)


def test_validation_and_canonical_digests_are_deterministic():
    specification = AgentSpecification("Summarize", ("read",), Environment.DEV)
    policy = SpecificationPolicy({"read", "search"}, {Environment.TEST, Environment.DEV})
    reordered = SpecificationPolicy({"search", "read"}, {Environment.DEV, Environment.TEST})
    service = SpecificationValidationService()
    evidence = service.validate(specification, policy)
    assert evidence == service.validate(specification, reordered)
    assert evidence.outcome is ValidationOutcome.PASS
    assert evidence.reasons == ()
    assert evidence.specification_digest == sha256(
        b'{"purpose":"Summarize","requested_tools":["read"],"target_environment":"DEV"}'
    ).hexdigest()
    assert evidence.policy_digest == sha256(
        b'{"allowed_environments":["DEV","TEST"],"allowed_tools":["read","search"]}'
    ).hexdigest()


@pytest.mark.parametrize(("changes", "reason"), [
    ({"purpose": "  "}, "Purpose must not be blank."),
    ({"requested_tools": ("write",)}, "Tool is not allowed: write"),
    ({"target_environment": Environment.PROD}, "Environment is not allowed: PROD"),
])
def test_invalid_specification_produces_fail(changes, reason):
    specification = replace(AgentSpecification("Summarize", ("read",), Environment.DEV), **changes)
    policy = SpecificationPolicy({"read"}, {Environment.DEV})
    evidence = SpecificationValidationService().validate(specification, policy)
    assert evidence.outcome is ValidationOutcome.FAIL
    assert evidence.reasons == (reason,)
    assert evidence.specification_digest == specification.digest
    assert evidence.policy_digest == policy.digest


@pytest.mark.parametrize("tools", [
    ("search", "read"),
    ("read", "search", "read"),
    ("search", "read", "search", "read"),
])
def test_requested_tools_order_and_duplicates_do_not_change_specification(tools):
    specification = AgentSpecification("Summarize", ("read", "search"), Environment.DEV)
    equivalent = AgentSpecification("Summarize", tools, Environment.DEV)
    assert equivalent.requested_tools == ("read", "search")
    assert equivalent == specification
    assert equivalent.digest == specification.digest


@pytest.mark.parametrize("tools", [["read"], ["search", "read", "read"]])
def test_records_are_immutable_and_copy_mutable_inputs(tools):
    expected_tools = tuple(sorted(set(tools)))
    environments = {Environment.DEV}
    specification = AgentSpecification("Summarize", tools, Environment.DEV)
    policy = SpecificationPolicy(tools, environments)
    evidence = SpecificationValidationService().validate(specification, policy)
    tools.append("write")
    environments.add(Environment.PROD)
    assert specification.requested_tools == expected_tools
    assert policy.allowed_tools == frozenset(expected_tools)
    assert policy.allowed_environments == frozenset({Environment.DEV})
    for record, field, value in [
        (specification, "purpose", "Changed"),
        (specification, "requested_tools", ("write",)),
        (specification, "target_environment", Environment.PROD),
        (policy, "allowed_tools", frozenset({"write"})),
        (evidence, "outcome", ValidationOutcome.FAIL),
        (evidence, "reasons", ("changed",)),
    ]:
        with pytest.raises(FrozenInstanceError):
            setattr(record, field, value)


def test_failure_reasons_are_sorted_and_failure_survives_later_success():
    specification = AgentSpecification("Summarize", ("z", "a"), Environment.DEV)
    service = SpecificationValidationService()
    failed = service.validate(specification, SpecificationPolicy(set(), {Environment.DEV}))
    passed = service.validate(specification, SpecificationPolicy({"a", "z"}, {Environment.DEV}))
    assert passed.outcome is ValidationOutcome.PASS
    assert failed.outcome is ValidationOutcome.FAIL
    assert failed.reasons == ("Tool is not allowed: a", "Tool is not allowed: z")
