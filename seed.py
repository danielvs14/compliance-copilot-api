from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from api.db.session import SessionLocal
from api.models.documents import Document
from api.models.events import Event
from api.models.orgs import Org
from api.models.requirements import Requirement, RequirementStatusEnum
from api.services.extraction_pipeline import attach_translations, extract_requirement_drafts
from api.services.metrics import record_requirements_created
from api.services.parse_pdf import extract_text_from_pdf
from api.services.schedule import next_due_from_frequency

logger = logging.getLogger(__name__)

FIXTURE_PATH = Path(__file__).parent / "tests" / "fixtures" / "electrical_sample.pdf"
DEFAULT_ORG_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")


def run_seed(org_id: uuid.UUID | None = None) -> None:
    if not FIXTURE_PATH.exists():
        raise FileNotFoundError(f"Fixture not found at {FIXTURE_PATH}")

    org_uuid = org_id or DEFAULT_ORG_ID

    session = SessionLocal()
    start = datetime.now(timezone.utc)
    try:
        existing_org = session.query(Org).filter(Org.id == org_uuid).one_or_none()
        if existing_org is None:
            session.add(Org(id=org_uuid, name="Seed Org"))
            session.flush()

        existing = (
            session.query(Document)
            .filter(Document.storage_url == str(FIXTURE_PATH), Document.org_id == org_uuid)
            .one_or_none()
        )
        if existing:
            logger.info("Seed already applied for org %s", org_uuid)
            return

        text = extract_text_from_pdf(str(FIXTURE_PATH))
        if not text:
            raise RuntimeError("Failed to extract text from fixture PDF")

        drafts = extract_requirement_drafts(text)
        attach_translations(drafts)

        document = Document(
            org_id=org_uuid,
            name=FIXTURE_PATH.name,
            storage_url=str(FIXTURE_PATH),
            text_excerpt=text[:1000],
            extracted_at=datetime.now(timezone.utc),
        )
        session.add(document)
        session.flush()

        session.add(
            Event(
                org_id=org_uuid,
                document_id=document.id,
                type="upload",
                data={"fixture": True, "filename": FIXTURE_PATH.name},
            )
        )

        requirement_ids: list[str] = []
        for draft in drafts:
            due_date = None
            if draft.due_date:
                try:
                    due_date = datetime.fromisoformat(draft.due_date.replace("Z", "+00:00"))
                except ValueError:
                    due_date = None
            if not due_date and draft.frequency:
                due_date = next_due_from_frequency(draft.frequency)

            status = RequirementStatusEnum.REVIEW if draft.confidence < 0.5 else RequirementStatusEnum.OPEN

            requirement = Requirement(
                org_id=org_uuid,
                document_id=document.id,
                title_en=draft.title_en,
                title_es=draft.title_es or draft.title_en,
                description_en=draft.description_en,
                description_es=draft.description_es or draft.description_en,
                category=draft.category,
                frequency=draft.frequency,
                due_date=due_date,
                next_due=due_date,
                status=status,
                source_ref=draft.source_ref,
                confidence=draft.confidence,
                trade="electrical",
                attributes=draft.attributes | {"origin": draft.origin, "seed": True},
            )
            session.add(requirement)
            session.flush()
            requirement_ids.append(str(requirement.id))

        record_requirements_created(session, org_uuid, len(requirement_ids))

        latency_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        session.add(
            Event(
                org_id=org_uuid,
                document_id=document.id,
                type="extracted",
                data={"fixture": True, "requirement_ids": requirement_ids, "latency_ms": latency_ms},
            )
        )

        session.commit()
        logger.info("Seeded %s requirements for org %s (latency_ms=%s)", len(requirement_ids), org_uuid, latency_ms)
    finally:
        session.close()


if __name__ == "__main__":
    org_value = os.getenv("SEED_ORG_ID")
    chosen_org = uuid.UUID(org_value) if org_value else DEFAULT_ORG_ID
    run_seed(chosen_org)
