from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import AliasChoices, BaseModel, EmailStr, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AwsSettings(BaseModel):
    model_config = SettingsConfigDict(extra="ignore")

    access_key_id: Optional[str] = Field(default=None)
    secret_access_key: Optional[str] = Field(default=None)
    region: str = Field(default="us-east-1")
    s3_bucket: str = Field(default="compliance-copilot-dev")
    s3_endpoint_url: Optional[str] = Field(default=None)


_BASE_DIR = Path(__file__).resolve().parent.parent
_ROOT_ENV = _BASE_DIR.parent / ".env"
_API_ENV = _BASE_DIR / ".env"


def _load_env_files(paths: tuple[Path, ...]) -> None:
    """Minimal .env loader so settings work without python-dotenv."""
    for path in paths:
        if not path.exists():
            continue
        for raw_line in path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


_load_env_files((_API_ENV, _ROOT_ENV))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=tuple(str(path) for path in (_API_ENV, _ROOT_ENV)),
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    environment: str = Field(default="development", alias="APP_ENV")
    database_url: str = Field(default="postgresql+psycopg2://compliance:compliance@localhost:5433/compliance_copilot", alias="DATABASE_URL")
    app_url: str = Field(default="http://localhost:3000", alias="APP_URL")
    api_url: str = Field(default="http://localhost:8000", alias="API_URL")
    email_from: EmailStr = Field(default="noreply@example.com", alias="EMAIL_FROM")
    magic_link_secret: str = Field(default="dev-secret", alias="MAGIC_LINK_SECRET")
    magic_link_expiry_minutes: int = Field(default=15, alias="MAGIC_LINK_EXPIRY_MINUTES")
    session_secret: str = Field(default="dev-session-secret", alias="SESSION_SECRET")
    session_ttl_hours: int = Field(default=72, alias="SESSION_TTL_HOURS")
    cookie_name: str = Field(default="cc_session", alias="SESSION_COOKIE_NAME")
    cookie_domain: Optional[str] = Field(default=None, alias="SESSION_COOKIE_DOMAIN")
    allow_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"], alias="CORS_ALLOW_ORIGINS")

    aws: AwsSettings = Field(default_factory=AwsSettings)


    @model_validator(mode="after")
    def load_nested_env(self) -> "Settings":
        """Normalize list-like settings parsed from environment files."""
        self.aws = AwsSettings(
            access_key_id=os.getenv("AWS_ACCESS_KEY_ID", self.aws.access_key_id),
            secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", self.aws.secret_access_key),
            region=os.getenv("AWS_REGION", self.aws.region),
            s3_bucket=os.getenv("S3_BUCKET", self.aws.s3_bucket),
            s3_endpoint_url=os.getenv("S3_ENDPOINT_URL", self.aws.s3_endpoint_url),
        )

        raw_origins: str | None
        if isinstance(self.allow_origins, str):
            raw_origins = self.allow_origins
        else:
            raw_origins = os.getenv("CORS_ALLOW_ORIGINS")

        if raw_origins:
            self.allow_origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[arg-type]


settings = get_settings()
