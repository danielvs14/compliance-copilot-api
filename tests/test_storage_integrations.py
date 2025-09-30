from __future__ import annotations

import io

import boto3
import pytest

from api.config import settings
from api.db.session import SessionLocal
from api.models.permits import Permit
from api.models.training_certs import TrainingCert


@pytest.fixture()
def mock_s3_bucket():
    from moto import mock_aws

    with mock_aws():
        s3 = boto3.client("s3", region_name=settings.aws.region)
        bucket = "test-storage-bucket"
        s3.create_bucket(Bucket=bucket)
        previous_bucket = settings.aws.s3_bucket
        settings.aws.s3_bucket = bucket
        try:
            yield s3
        finally:
            settings.aws.s3_bucket = previous_bucket


def test_permit_upload_saves_to_s3(client, auth_context, mock_s3_bucket):
    payload = {
        "name": "City Permit",
        "permit_number": "PERM-001",
        "jurisdiction": "Austin",
        "expires_at": "2030-01-01T00:00:00Z",
    }
    response = client.post(
        "/permits/upload",
        data=payload,
        files={"file": ("permit.pdf", io.BytesIO(b"%PDF-1.7\n..."), "application/pdf")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["permit"]["name"] == "City Permit"
    assert data["permit"]["storage_url"].startswith("s3://")

    bucket, key = data["permit"]["storage_url"].replace("s3://", "").split("/", 1)
    head = mock_s3_bucket.head_object(Bucket=bucket, Key=key)
    assert head["ResponseMetadata"]["HTTPStatusCode"] == 200

    with SessionLocal() as session:
        permit = session.query(Permit).one()
        assert permit.storage_url == data["permit"]["storage_url"]


def test_training_upload_saves_to_s3(client, auth_context, mock_s3_bucket):
    payload = {
        "worker_name": "Maria",
        "certification_type": "OSHA 30",
        "authority": "OSHA",
    }
    response = client.post(
        "/training/upload",
        data=payload,
        files={"file": ("osha.pdf", io.BytesIO(b"%PDF-1.7\n..."), "application/pdf")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["training_cert"]["worker_name"] == "Maria"
    assert data["training_cert"]["storage_url"].startswith("s3://")

    bucket, key = data["training_cert"]["storage_url"].replace("s3://", "").split("/", 1)
    head = mock_s3_bucket.head_object(Bucket=bucket, Key=key)
    assert head["ResponseMetadata"]["HTTPStatusCode"] == 200

    with SessionLocal() as session:
        cert = session.query(TrainingCert).one()
        assert cert.storage_url == data["training_cert"]["storage_url"]
