from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .db.session import engine
from .models.base import Base
from .models.documents import Document
from .models.requirements import Requirement
from .models.events import Event
from .routers import health, documents, requirements

app = FastAPI(title="Compliance Copilot API", version="0.1.0")

# DB init 
Base.metadata.create_all(bind=engine)

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
