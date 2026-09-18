"""Explicit lifecycle prerequisites for isolated legacy service tests.

End-to-end build provenance and failure ordering are exercised in test_lifecycle.
"""
from agent_foundry.domain.lifecycle import LifecycleState, LifecycleTransition


def record_state(store, artifact_digest, state=LifecycleState.BUILT, environment=None):
    store.save_lifecycle_transition(LifecycleTransition(
        artifact_digest, LifecycleState.BUILT, None, "isolated-build-evidence",
    ))
    if state is not LifecycleState.BUILT:
        store.save_lifecycle_transition(LifecycleTransition(
            artifact_digest, state, environment, "isolated-authority-evidence",
        ))
