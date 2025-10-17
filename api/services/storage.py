from __future__ import annotations

import io
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import BinaryIO, Optional

from botocore.exceptions import BotoCoreError, ClientError

from ..config import settings
from .aws import boto3_client


@dataclass
class StoredFile:
    key: str
    storage_url: str
    presigned_url: Optional[str] = None


class StorageService:
    def __init__(self, bucket: Optional[str] = None) -> None:
        self.bucket = bucket or settings.aws.s3_bucket
        self._client = boto3_client("s3")

    def _build_key(self, org_id: str | uuid.UUID, original_name: str) -> str:
        suffix = Path(original_name).suffix.lower() or ".bin"
        return f"{org_id}/{uuid.uuid4()}{suffix}"

    def upload_fileobj(
        self,
        org_id: str | uuid.UUID,
        file_obj: BinaryIO | bytes,
        filename: str,
        content_type: str,
        presign_ttl: timedelta | None = timedelta(hours=1),
    ) -> StoredFile:
        buffer: BinaryIO
        if isinstance(file_obj, (bytes, bytearray)):
            buffer = io.BytesIO(file_obj)
        else:
            buffer = file_obj
            buffer.seek(0)

        key = self._build_key(org_id, filename)
        extra_args = {"ContentType": content_type}

        try:
            self._client.upload_fileobj(buffer, self.bucket, key, ExtraArgs=extra_args)
        except (BotoCoreError, ClientError) as exc:
            raise RuntimeError(f"Failed to upload to S3: {exc}") from exc

        presigned_url = None
        if presign_ttl:
            presigned_url = self.generate_presigned_url(key, presign_ttl)

        return StoredFile(key=key, storage_url=f"s3://{self.bucket}/{key}", presigned_url=presigned_url)

    def generate_presigned_url(self, key: str, ttl: timedelta = timedelta(hours=1)) -> str:
        try:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=int(ttl.total_seconds()),
            )
        except (BotoCoreError, ClientError) as exc:
            raise RuntimeError(f"Failed to generate presigned URL: {exc}") from exc

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            raise RuntimeError(f"Failed to delete S3 object: {exc}") from exc

    def open_stream(self, key: str):
        try:
            obj = self._client.get_object(Bucket=self.bucket, Key=key)
            body = obj["Body"]
            metadata = {
                "content_type": obj.get("ContentType", "application/octet-stream"),
                "content_length": obj.get("ContentLength"),
            }

            def iterator(chunk_size: int = 1024 * 64):
                for chunk in body.iter_chunks(chunk_size):
                    if chunk:
                        yield chunk

            def closer():
                try:
                    body.close()
                except Exception:  # pragma: no cover - best effort
                    pass

            return iterator, metadata, closer
        except (BotoCoreError, ClientError) as exc:
            raise RuntimeError(f"Failed to download S3 object: {exc}") from exc


def get_storage_service() -> StorageService:
    return StorageService()
