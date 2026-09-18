"""SQLite is the trusted local recording boundary, not identity authentication.

Digests establish content identity. Provenance depends on controlled write access
to this store and its database file; human/agent authentication remains external.
"""

from dataclasses import asdict
from enum import Enum
import json
from pathlib import Path
import sqlite3

from agent_foundry.domain.lifecycle import LifecycleState, LifecycleTransition
from agent_foundry.domain.approval import HumanApproval
from agent_foundry.domain.deployment import DeploymentAttempt, DeploymentOutcome
from agent_foundry.domain.runtime import (
    RuntimeGrant, ToolAuthorizationDecision, ToolAuthorizationOutcome, ToolPermission,
)
from agent_foundry.domain.specification import Environment


class EvidenceConflictError(Exception):
    """An existing digest has different content; nothing was overwritten."""


class EvidenceIntegrityError(Exception):
    """Stored evidence cannot be reconstructed and verified."""


def _json_default(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, frozenset):
        return [asdict(item) for item in sorted(value, key=lambda item: (item.tool, item.action))]
    raise TypeError("Unsupported evidence value")


def _payload(record):
    return json.dumps(asdict(record), default=_json_default, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False, allow_nan=False)


class SQLiteGovernanceStore:
    """Five explicit record APIs. Identical saves are idempotent and append-only."""

    def __init__(self, path: str | Path) -> None:
        self._connection = sqlite3.connect(path)
        try:
            with self._connection:
                for table in ("approvals", "deployments", "runtime_grants", "tool_decisions",
                              "lifecycle_transitions"):
                    self._connection.execute(
                        f"CREATE TABLE IF NOT EXISTS {table} "
                        "(digest TEXT PRIMARY KEY NOT NULL, payload TEXT NOT NULL)"
                    )
        except sqlite3.DatabaseError as error:
            self.close()
            raise EvidenceIntegrityError("Cannot initialize evidence database") from error

    def close(self) -> None:
        self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def _insert(self, table: str, digest: str, payload: str) -> None:
        # INSERT's uniqueness constraint arbitrates concurrent writers. Never replace.
        with self._connection:
            try:
                self._connection.execute(
                    f"INSERT INTO {table} (digest, payload) VALUES (?, ?)", (digest, payload),
                )
            except sqlite3.IntegrityError as error:
                row = self._connection.execute(
                    f"SELECT payload FROM {table} WHERE digest = ?", (digest,),
                ).fetchone()
                if row != (payload,):
                    raise EvidenceConflictError(f"Conflicting evidence in {table}: {digest}") from error

    def _read(self, table: str, digest: str):
        try:
            row = self._connection.execute(
                f"SELECT payload FROM {table} WHERE digest = ?", (digest,),
            ).fetchone()
            if row is None:
                return None
            data = json.loads(row[0])
            if not isinstance(data, dict):
                raise ValueError("Evidence payload must be an object")
            if table != "lifecycle_transitions" or data["target_environment"] is not None:
                data["target_environment"] = Environment(data["target_environment"])
            if table == "lifecycle_transitions":
                data["state"] = LifecycleState(data["state"])
                record = LifecycleTransition(**data)
            elif table == "approvals":
                record = HumanApproval(**data)
            elif table == "deployments":
                data["outcome"] = DeploymentOutcome(data["outcome"])
                record = DeploymentAttempt(**data)
            elif table == "runtime_grants":
                data["permissions"] = frozenset(ToolPermission(**item) for item in data["permissions"])
                record = RuntimeGrant(**data)
            else:
                data["outcome"] = ToolAuthorizationOutcome(data["outcome"])
                data["requested_permission"] = ToolPermission(**data["requested_permission"])
                record = ToolAuthorizationDecision(**data)
            # Canonical comparison also rejects lossy reconstruction (extra JSON keys,
            # duplicate permissions, or malformed collection shapes).
            if record.digest != digest or _payload(record) != row[0]:
                raise ValueError("Stored evidence does not match its canonical identity")
            return record
        except (sqlite3.DatabaseError, ValueError, TypeError, KeyError) as error:
            raise EvidenceIntegrityError(f"Invalid evidence in {table}: {digest}") from error

    def save_approval(self, approval: HumanApproval) -> None:
        if type(approval) is not HumanApproval:
            raise TypeError("Expected canonical HumanApproval")
        self._insert("approvals", approval.digest, _payload(approval))

    def get_approval(self, digest: str) -> HumanApproval | None:
        return self._read("approvals", digest)

    def save_deployment(self, attempt: DeploymentAttempt) -> None:
        if type(attempt) is not DeploymentAttempt:
            raise TypeError("Expected canonical DeploymentAttempt")
        self._insert("deployments", attempt.digest, _payload(attempt))

    def get_deployment(self, digest: str) -> DeploymentAttempt | None:
        return self._read("deployments", digest)

    def save_runtime_grant(self, grant: RuntimeGrant) -> None:
        if type(grant) is not RuntimeGrant:
            raise TypeError("Expected canonical RuntimeGrant")
        self._insert("runtime_grants", grant.digest, _payload(grant))

    def get_runtime_grant(self, digest: str) -> RuntimeGrant | None:
        return self._read("runtime_grants", digest)

    def save_tool_decision(self, decision: ToolAuthorizationDecision) -> None:
        if type(decision) is not ToolAuthorizationDecision:
            raise TypeError("Expected canonical ToolAuthorizationDecision")
        self._insert("tool_decisions", decision.digest, _payload(decision))

    def get_tool_decision(self, digest: str) -> ToolAuthorizationDecision | None:
        return self._read("tool_decisions", digest)

    def save_lifecycle_transition(self, transition: LifecycleTransition) -> None:
        if type(transition) is not LifecycleTransition:
            raise TypeError("Expected canonical LifecycleTransition")
        self._insert("lifecycle_transitions", transition.digest, _payload(transition))

    def get_lifecycle_transition(self, digest: str) -> LifecycleTransition | None:
        return self._read("lifecycle_transitions", digest)

    def get_lifecycle_state(
        self, artifact_digest: str, target_environment: Environment | None,
    ) -> LifecycleState | None:
        if not isinstance(artifact_digest, str) or not artifact_digest.strip():
            raise ValueError("artifact_digest must be a nonblank string")
        if target_environment is not None and not isinstance(target_environment, Environment):
            raise TypeError("target_environment must be an Environment or None")
        try:
            digests = self._connection.execute("SELECT digest FROM lifecycle_transitions").fetchall()
        except sqlite3.DatabaseError as error:
            raise EvidenceIntegrityError("Cannot read lifecycle transitions") from error
        # Verify canonical records before filtering: corrupted fields must not hide
        # a transition from integrity checks. No mutable current-state cache exists.
        transitions = [self.get_lifecycle_transition(row[0]) for row in digests]
        matching = [record for record in transitions if record.artifact_digest == artifact_digest]
        if not any(record.state is LifecycleState.BUILT for record in matching):
            return None
        return max(
            (record.state for record in matching if record.target_environment is target_environment),
            default=LifecycleState.BUILT,
        )
