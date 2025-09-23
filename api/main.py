from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .middleware import RequestLoggingMiddleware
from .routers import health, documents, requirements

app = FastAPI(title="Compliance Copilot API", version="0.1.0")

app.add_middleware(RequestLoggingMiddleware)

# CORS (allow local web)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000","http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["health"])
app.include_router(documents.router, tags=["documents"])
app.include_router(requirements.router, tags=["requirements"])
