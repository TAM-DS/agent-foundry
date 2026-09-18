"""Construction gated by exact specification and policy evidence."""

from agent_foundry.domain.artifact import AgentArtifact
from agent_foundry.domain.specification import AgentSpecification
from agent_foundry.services.specification_validation import (
    SpecificationPolicy,
    SpecificationValidationService,
    ValidationEvidence,
    ValidationOutcome,
)


class BuildNotAuthorized(Exception):
    """The supplied evidence does not authorize this build."""


class AgentBuildService:
    def build(
        self,
        specification: AgentSpecification,
        policy: SpecificationPolicy,
        evidence: ValidationEvidence | None = None,
    ) -> AgentArtifact:
        if not isinstance(evidence, ValidationEvidence):
            raise BuildNotAuthorized("Validation evidence is required.")
        if evidence.outcome is not ValidationOutcome.PASS:
            raise BuildNotAuthorized("Validation outcome must be PASS.")
        if evidence.specification_digest != specification.digest:
            raise BuildNotAuthorized("Validation evidence does not match specification.")
        if evidence.policy_digest != policy.digest:
            raise BuildNotAuthorized("Validation evidence does not match policy.")
        # Evidence is ordinary in-process data, not a signed capability. Recheck
        # policy so fabricated or altered evidence cannot bypass validation.
        if evidence != SpecificationValidationService().validate(specification, policy):
            raise BuildNotAuthorized("Validation evidence does not match policy evaluation.")
        return AgentArtifact(source_specification_digest=specification.digest)
