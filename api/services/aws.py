from __future__ import annotations

from typing import Any

import boto3
from botocore.config import Config

from ..config import settings


def boto3_client(service: str) -> Any:
    kwargs: dict[str, Any] = {"region_name": settings.aws.region, "config": Config(retries={"max_attempts": 3})}
    if settings.aws.access_key_id and settings.aws.secret_access_key:
        kwargs["aws_access_key_id"] = settings.aws.access_key_id
        kwargs["aws_secret_access_key"] = settings.aws.secret_access_key
    if settings.aws.s3_endpoint_url and service == "s3":
        kwargs["endpoint_url"] = settings.aws.s3_endpoint_url
    return boto3.client(service, **kwargs)
