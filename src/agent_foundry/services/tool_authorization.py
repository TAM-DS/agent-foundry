"""Deterministic tool decisions using issued grants; never executes tools."""

from typing import Protocol

from agent_foundry.domain.runtime import (
    RuntimeGrant, RuntimePolicy, ToolAuthorizationDecision, ToolAuthorizationOutcome, ToolRequest,
)
from agent_foundry.services.runtime_authorization import RuntimeGrantStore


class ToolAuthorizationDecisionStore(Protocol):
    def save_tool_decision(self, decision: ToolAuthorizationDecision) -> None: ...

    def get_tool_decision(self, digest: str) -> ToolAuthorizationDecision | None: ...


class ToolAuthorizationService:
    def __init__(
        self, grant_store: RuntimeGrantStore, decision_store: ToolAuthorizationDecisionStore,
    ) -> None:
        self._grant_store = grant_store
        self._decision_store = decision_store

    def authorize(
        self, runtime_grant_digest: str, policy: RuntimePolicy, request: ToolRequest,
    ) -> ToolAuthorizationDecision:
        if not isinstance(runtime_grant_digest, str):
            raise TypeError("runtime_grant_digest must be a string")
        grant = self._grant_store.get_runtime_grant(runtime_grant_digest)
        reasons = []
        if not isinstance(grant, RuntimeGrant) or grant.digest != runtime_grant_digest:
            reasons.append("Authoritative issued runtime grant is required.")
        else:
            if grant.runtime_policy_digest != policy.digest:
                reasons.append("Runtime grant does not match runtime policy.")
            if request.artifact_digest != grant.artifact_digest:
                reasons.append("Request does not match grant artifact.")
            if request.target_environment is not grant.target_environment:
                reasons.append("Request does not match grant environment.")
            if request.permission not in grant.permissions:
                reasons.append("Requested permission is not explicitly granted.")
        if request.target_environment not in policy.allowed_environments:
            reasons.append("Environment is not allowed by runtime policy.")
        if request.permission not in policy.allowed_permissions:
            reasons.append("Requested permission is not allowed by runtime policy.")
        decision = ToolAuthorizationDecision(
            runtime_grant_digest, policy.digest, request.artifact_digest,
            request.target_environment, request.permission,
            ToolAuthorizationOutcome.DENY if reasons else ToolAuthorizationOutcome.ALLOW,
            tuple(reasons),
        )
        self._decision_store.save_tool_decision(decision)
        return decision
