from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

ACCESS_LOGGER_NAME = "compliance_copilot.access"


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Emit structured access logs for every request."""

    def __init__(self, app, logger: logging.Logger | None = None) -> None:
        super().__init__(app)
        self.logger = logger if logger is not None else logging.getLogger(ACCESS_LOGGER_NAME)

    async def dispatch(self, request: Request, call_next: Callable[[Request], Response]) -> Response:
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        request.state.request_id = request_id
        start = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = int((time.perf_counter() - start) * 1000)
            error_payload = {
                "event": "http_request_error",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": 500,
                "duration_ms": duration_ms,
            }
            if getattr(request.state, "user_id", None):
                error_payload["user_id"] = getattr(request.state, "user_id")
            if getattr(request.state, "org_id", None):
                error_payload["org_id"] = getattr(request.state, "org_id")

            self._log(error_payload, level=logging.ERROR)
            raise

        duration_ms = int((time.perf_counter() - start) * 1000)
        response.headers.setdefault("x-request-id", request_id)

        payload = {
            "event": "http_request",
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": duration_ms,
        }
        if getattr(request.state, "user_id", None):
            payload["user_id"] = getattr(request.state, "user_id")
        if getattr(request.state, "org_id", None):
            payload["org_id"] = getattr(request.state, "org_id")

        self._log(payload)

        return response

    def _log(self, payload: dict[str, object], level: int = logging.INFO) -> None:
        self.logger.log(level, json.dumps(payload, separators=(",", ":")))
