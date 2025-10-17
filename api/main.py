import logging
import sys

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

from .config import settings
from .middleware import RequestLoggingMiddleware
from .middleware.logging import ACCESS_LOGGER_NAME
from .routers import auth, documents, health, permits, requirements, training

logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(sys.stdout)])
logging.getLogger(ACCESS_LOGGER_NAME).setLevel(logging.INFO)

if settings.sentry_dsn and str(settings.sentry_dsn).strip().lower().startswith(("http://", "https://")):
    sentry_sdk.init(
        dsn=str(settings.sentry_dsn).strip(),
        environment=settings.environment,
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            SqlalchemyIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        traces_sample_rate=settings.sentry_traces_sample_rate,
        profiles_sample_rate=settings.sentry_profiles_sample_rate,
    )

app = FastAPI(title="Compliance Copilot API", version="0.1.0")

app.add_middleware(RequestLoggingMiddleware)

if settings.metrics_enabled:
    instrumentator = Instrumentator(should_group_status_codes=True, should_ignore_untemplated=True)
    instrumentator.instrument(app).expose(app, include_in_schema=False)

# CORS (allow local web)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["health"])
app.include_router(documents.router, tags=["documents"])
app.include_router(requirements.router, tags=["requirements"])
app.include_router(auth.router)
app.include_router(permits.router, tags=["permits"])
app.include_router(training.router, tags=["training"])
