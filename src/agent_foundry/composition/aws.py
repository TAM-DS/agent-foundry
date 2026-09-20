"""Compose development deployment with the AWS SDK and ambient credentials."""

import boto3
from botocore.exceptions import ClientError

from agent_foundry.adapters.s3_deployment import S3DeploymentBackend
from agent_foundry.services.deployment import DeploymentService


def create_dev_deployment_service(
    *, governance_store, bucket_name: str, region_name: str,
) -> DeploymentService:
    s3_client = boto3.client("s3", region_name=region_name)
    backend = S3DeploymentBackend(s3_client, bucket_name, ClientError)
    return DeploymentService(
        backend=backend,
        approval_store=governance_store,
        deployment_store=governance_store,
        lifecycle_store=governance_store,
    )
