from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
