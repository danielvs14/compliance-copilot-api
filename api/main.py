import logging
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .middleware import RequestLoggingMiddleware
from .middleware.logging import ACCESS_LOGGER_NAME
from .routers import auth, documents, health, permits, requirements, training

logging.basicConfig(
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
logging.getLogger(ACCESS_LOGGER_NAME).setLevel(logging.INFO)

app = FastAPI(title="Compliance Copilot API", version="0.1.0")

app.add_middleware(RequestLoggingMiddleware)

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
