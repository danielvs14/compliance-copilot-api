from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from botocore.exceptions import BotoCoreError, ClientError

from ..config import settings
from .aws import boto3_client

logger = logging.getLogger(__name__)


@dataclass
class EmailMessage:
    to: str
    subject: str
    text_body: str
    html_body: Optional[str] = None


class EmailClient:
    def send(self, message: EmailMessage) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class SesEmailClient(EmailClient):
    def __init__(self) -> None:
        self._client = boto3_client("ses")

    def send(self, message: EmailMessage) -> None:
        try:
            destination = {"ToAddresses": [message.to]}
            body: dict[str, dict[str, str]] = {"Text": {"Data": message.text_body}}
            if message.html_body:
                body["Html"] = {"Data": message.html_body}

            self._client.send_email(
                Source=settings.email_from,
                Destination=destination,
                Message={
                    "Subject": {"Data": message.subject},
                    "Body": body,
                },
            )
        except (BotoCoreError, ClientError) as exc:
            logger.error("SES send_email failed: %s", exc)
            raise RuntimeError("Failed to send email") from exc


class ConsoleEmailClient(EmailClient):
    def send(self, message: EmailMessage) -> None:
        logger.info("Email body:\n%s", message.text_body)
        logger.info("Sending email (console fallback) -> %s: %s", message.to, message.subject)
        logger.debug("Email body: %s", message.text_body)


def get_email_client() -> EmailClient:
    if settings.environment == "production":
        return SesEmailClient()

    # In non-prod, try SES but fall back to console on errors
    try:
        client = SesEmailClient()
        # make a dry run call to ensure credentials available
        client._client.get_account_sending_enabled()
        return client
    except Exception:  # pragma: no cover - fallback path
        logger.info("Falling back to ConsoleEmailClient")
        return ConsoleEmailClient()
