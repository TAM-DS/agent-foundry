"""Write and verify candidate artifacts in the development artifact bucket."""

import json

from agent_foundry.domain.artifact import AgentArtifact
from agent_foundry.domain.specification import Environment
from agent_foundry.services.deployment import DeploymentBackendError


class S3DeploymentBackend:
    """Use an injected S3 client; deployment authorization belongs to the service.

    The client must expose put_object and get_object. Injected client error
    types are translated; unexpected exceptions propagate. The adapter never
    retries or removes an unverified object.
    """

    def __init__(
        self,
        s3_client,
        bucket_name: str,
        client_error_types: type[Exception] | tuple[type[Exception], ...],
    ) -> None:
        self._s3_client = s3_client
        self._bucket_name = bucket_name
        self._client_error_types = client_error_types

    def deploy(self, artifact: AgentArtifact, target_environment: Environment) -> None:
        if target_environment is not Environment.DEV:
            raise DeploymentBackendError("S3 deployment supports only DEV.")

        artifact_digest = artifact.digest
        body = json.dumps(
            {
                "artifact_format": "agent-foundry-candidate-v1",
                "artifact_digest": artifact_digest,
                "source_specification_digest": artifact.source_specification_digest,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        key = f"dev/artifacts/{artifact_digest}.json"

        try:
            self._s3_client.put_object(
                Bucket=self._bucket_name,
                Key=key,
                Body=body,
                ContentType="application/json",
            )
            response = self._s3_client.get_object(Bucket=self._bucket_name, Key=key)
            stream = response["Body"]
            try:
                downloaded = stream.read()
            finally:
                stream.close()
        except self._client_error_types as error:
            raise DeploymentBackendError("S3 deployment client failure.") from error

        if downloaded != body:
            raise DeploymentBackendError("S3 artifact read-back bytes do not match.")
