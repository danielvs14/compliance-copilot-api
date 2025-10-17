from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from api.db.session import SessionLocal
from api.models import (
    Document,
    DocumentTemplate,
    Membership,
    MembershipRole,
    Org,
    Permit,
    Requirement,
    RequirementAnchorTypeEnum,
    RequirementFrequencyEnum,
    RequirementStatusEnum,
    RequirementTemplate,
    TrainingCert,
    User,
)
from api.services.metrics import record_requirements_created
from api.services.schedule import compute_next_due
from api.services.template_matching import compute_fingerprint

logger = logging.getLogger(__name__)

SEED_VERSION = "week5.v1"
SEED_BASE_TIME = datetime(2024, 8, 1, tzinfo=timezone.utc)
FIXTURES_DIR = Path(__file__).parent / "tests" / "fixtures"
SEED_FIXTURES_DIR = FIXTURES_DIR / "seed"


def seed_uuid(name: str) -> uuid.UUID:
    """Generate deterministic UUIDs scoped to the seed version."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"compliance-copilot/{SEED_VERSION}/{name}")


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_excerpt(path: str | None) -> str | None:
    if not path:
        return None
    file_path = Path(path)
    if not file_path.exists():
        logger.warning("Fixture missing for excerpt: %s", file_path)
        return None
    text = file_path.read_text(encoding="utf-8").strip()
    return text[:1000]


def require_role(value: str) -> MembershipRole:
    return MembershipRole(value)


def require_status(value: str) -> RequirementStatusEnum:
    return RequirementStatusEnum(value)


KNOWN_TEMPLATE_PAYLOADS: list[dict[str, Any]] = [
    {
        "key": "osha-3080",
        "title": "OSHA Form 3080",
        "version": "2024",
        "trade": "electrical",
        "text": """
        Occupational Safety and Health Administration (OSHA) requires employers to summarize workplace
        injuries and illnesses annually. The OSHA Form 300A must be certified by a company executive and posted
        in a prominent location each year from February 1 through April 30. Keep completed forms on file for
        five years. This excerpt describes the employer responsibilities for distributing OSHA Form 3080 notices.
        """,
        "requirements": [
            {
                "key": "osha-3080-post-summary",
                "title_en": "Post OSHA 300A Summary",
                "title_es": "Publicar el resumen OSHA 300A",
                "description_en": "Post the OSHA Form 300A in a conspicuous location from February 1 to April 30 each year.",
                "description_es": "Publica el formulario OSHA 300A en un lugar visible del 1 de febrero al 30 de abril de cada año.",
                "category": "Recordkeeping",
                "frequency": RequirementFrequencyEnum.ANNUAL,
                "anchor_type": RequirementAnchorTypeEnum.UPLOAD_DATE,
                "anchor_value": {},
                "attributes": {"source": "OSHA 3080", "section": "Posting Requirements"},
            },
            {
                "key": "osha-3080-retain-forms",
                "title_en": "Retain OSHA Logs",
                "title_es": "Conservar registros OSHA",
                "description_en": "Maintain completed OSHA injury and illness logs for five years and make them available upon request.",
                "description_es": "Conserva los registros de lesiones y enfermedades OSHA durante cinco años y ponlos a disposición cuando se soliciten.",
                "category": "Recordkeeping",
                "frequency": RequirementFrequencyEnum.ANNUAL,
                "anchor_type": RequirementAnchorTypeEnum.UPLOAD_DATE,
                "anchor_value": {},
                "attributes": {"source": "OSHA 3080", "section": "Retention"},
            },
        ],
    },
    {
        "key": "nfpa-70e",
        "title": "NFPA 70E Article 130",
        "version": "2024",
        "trade": "electrical",
        "text": """
        NFPA 70E Article 130.5 requires that an employer implement an energized electrical work permit
        process and perform an arc flash risk assessment prior to energized work. The assessment must be
        reviewed at least every five years or when major modifications occur. Boundaries and PPE requirements
        shall be communicated to qualified persons.
        """,
        "requirements": [
            {
                "key": "nfpa-70e-risk-assessment",
                "title_en": "Review Arc Flash Risk Assessment",
                "title_es": "Revisar evaluación de riesgo de arco eléctrico",
                "description_en": "Re-evaluate the facility arc flash risk assessment at least once every five years or after major system changes.",
                "description_es": "Reevalúa la evaluación de riesgo de arco eléctrico de la instalación al menos cada cinco años o después de cambios importantes.",
                "category": "Electrical Safety",
                "frequency": RequirementFrequencyEnum.EVERY_N_MONTHS,
                "anchor_type": RequirementAnchorTypeEnum.UPLOAD_DATE,
                "anchor_value": {"interval": 60, "months": 60},
                "attributes": {"source": "NFPA 70E", "section": "130.5"},
            },
            {
                "key": "nfpa-70e-energized-permit",
                "title_en": "Maintain Energized Work Permit Process",
                "title_es": "Mantener proceso de permiso de trabajo energizado",
                "description_en": "Ensure energized electrical work permits are issued and reviewed before any justified energized task.",
                "description_es": "Asegura que los permisos de trabajo energizado se emitan y revisen antes de cualquier tarea energizada justificada.",
                "category": "Electrical Safety",
                "frequency": RequirementFrequencyEnum.BEFORE_EACH_USE,
                "anchor_type": RequirementAnchorTypeEnum.UPLOAD_DATE,
                "anchor_value": {},
                "attributes": {"source": "NFPA 70E", "section": "130.2"},
            },
        ],
    },
    {
        "key": "state-license-renewal",
        "title": "State Electrical License Renewal",
        "version": "2024",
        "trade": "electrical",
        "text": """
        State licensing boards require electrical contractors to renew their master license annually. Renewal packets
        must include proof of continuing education, updated insurance certificates, and payment prior to expiration.
        Notifications are typically mailed 60 days before the license expiration date.
        """,
        "requirements": [
            {
                "key": "state-license-submit-renewal",
                "title_en": "Submit License Renewal Packet",
                "title_es": "Enviar paquete de renovación de licencia",
                "description_en": "Prepare and submit the state electrical license renewal packet 30 days before expiration.",
                "description_es": "Prepara y envía el paquete de renovación de la licencia eléctrica estatal 30 días antes del vencimiento.",
                "category": "Licensing",
                "frequency": RequirementFrequencyEnum.ANNUAL,
                "anchor_type": RequirementAnchorTypeEnum.UPLOAD_DATE,
                "anchor_value": {},
                "attributes": {"source": "State License", "section": "Renewal"},
            },
            {
                "key": "state-license-track-ceu",
                "title_en": "Track Continuing Education Credits",
                "title_es": "Registrar créditos de educación continua",
                "description_en": "Verify continuing education hours for licensed electricians and file evidence with the board.",
                "description_es": "Verifica las horas de educación continua de los electricistas con licencia y presenta la evidencia ante la junta.",
                "category": "Licensing",
                "frequency": RequirementFrequencyEnum.ANNUAL,
                "anchor_type": RequirementAnchorTypeEnum.UPLOAD_DATE,
                "anchor_value": {},
                "attributes": {"source": "State License", "section": "Education"},
            },
        ],
    },
]


def _normalize_frequency_key(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


_LEGACY_FREQUENCY_MAP = {
    "before_each_use": RequirementFrequencyEnum.BEFORE_EACH_USE,
    "before_use": RequirementFrequencyEnum.BEFORE_EACH_USE,
    "daily": RequirementFrequencyEnum.DAILY,
    "weekly": RequirementFrequencyEnum.WEEKLY,
    "monthly": RequirementFrequencyEnum.MONTHLY,
    "quarterly": RequirementFrequencyEnum.QUARTERLY,
    "annual": RequirementFrequencyEnum.ANNUAL,
    "annualy": RequirementFrequencyEnum.ANNUAL,
    "yearly": RequirementFrequencyEnum.ANNUAL,
    "one_time": RequirementFrequencyEnum.ONE_TIME,
}


def parse_frequency(value: Any) -> RequirementFrequencyEnum | None:
    if value is None:
        return None
    if isinstance(value, RequirementFrequencyEnum):
        return value
    token = str(value).strip()
    if not token:
        return None

    key = _normalize_frequency_key(token)
    mapped = _LEGACY_FREQUENCY_MAP.get(key)
    if mapped:
        return mapped

    try:
        return RequirementFrequencyEnum[token.upper()]
    except KeyError:
        logger.warning("Unknown frequency token in seed: %s", token)
        return None


def parse_anchor_type(value: Any) -> RequirementAnchorTypeEnum | None:
    if value is None:
        return None
    if isinstance(value, RequirementAnchorTypeEnum):
        return value
    token = str(value).strip()
    if not token:
        return None
    try:
        return RequirementAnchorTypeEnum[token.upper()]
    except KeyError:
        logger.warning("Unknown anchor type token in seed: %s", token)
        return None


SEED_PAYLOAD: list[dict[str, Any]] = [
    {
        "org": {
            "id": seed_uuid("org:brightline-electric"),
            "name": "Brightline Electric",
            "slug": "brightline-electric",
            "primary_trade": "electrical",
        },
        "users": [
            {
                "id": seed_uuid("user:alex-rivera"),
                "email": "alex.rivera@brightlineelectric.com",
                "full_name": "Alex Rivera",
                "preferred_locale": "en",
                "role": "owner",
            },
            {
                "id": seed_uuid("user:maria-santos"),
                "email": "maria.santos@brightlineelectric.com",
                "full_name": "Maria Santos",
                "preferred_locale": "es",
                "role": "admin",
            },
        ],
        "documents": [
            {
                "id": seed_uuid("document:brightline:arc-flash"),
                "name": "Arc Flash Readiness Playbook",
                "storage_url": "s3://seed-brightline/documents/arc-flash-readiness.pdf",
                "text_fixture": str(SEED_FIXTURES_DIR / "arc_flash_guidelines.txt"),
                "extracted_at": "2024-07-15T15:00:00+00:00",
                "requirements": [
                    {
                        "id": seed_uuid("requirement:brightline:ppe-kits"),
                        "title_en": "Issue Arc-Rated PPE Kits",
                        "title_es": "Entregar kits de EPP con clasificación de arco",
                        "description_en": "Stock and issue arc-rated face shields, gloves, and balaclavas before energized work begins.",
                        "description_es": "Mantén inventario y entrega caretas, guantes y pasamontañas con clasificación de arco antes de trabajos energizados.",
                        "category": "Electrical Safety",
                        "frequency": "before each use",
                        "due_date": None,
                        "source_ref": "OSHA 4472 §130.7(C)(1)",
                        "confidence": 0.83,
                        "status": "OPEN",
                        "attributes": {"origin": "seed", "seed": True},
                    },
                    {
                        "id": seed_uuid("requirement:brightline:arc-boundary"),
                        "title_en": "Barricade Arc Flash Boundary",
                        "title_es": "Delimitar la zona de arco eléctrico",
                        "description_en": "Set up barricades and signage around the calculated arc flash boundary prior to any energized maintenance tasks.",
                        "description_es": "Instala barricadas y señalización alrededor del límite calculado de arco antes de mantenimiento energizado.",
                        "category": "Electrical Safety",
                        "frequency": "before each use",
                        "due_date": None,
                        "source_ref": "OSHA 4472 §130.7(E)",
                        "confidence": 0.8,
                        "status": "OPEN",
                        "attributes": {"origin": "seed", "seed": True},
                    },
                    {
                        "id": seed_uuid("requirement:brightline:ir-log"),
                        "title_en": "Log Switchgear IR Scans",
                        "title_es": "Registrar escaneos infrarrojos de tableros",
                        "description_en": "Record thermographic scan findings for main switchgear and remediate hotspots promptly.",
                        "description_es": "Registra resultados de termografías en tableros principales y corrige puntos calientes de inmediato.",
                        "category": "Preventive Maintenance",
                        "frequency": "monthly",
                        "due_date": "2024-08-31T00:00:00+00:00",
                        "source_ref": "NFPA 70E Table 130.5(G)",
                        "confidence": 0.62,
                        "status": "REVIEW",
                        "attributes": {"origin": "seed", "seed": True},
                    },
                ],
            },
            {
                "id": seed_uuid("document:brightline:switchgear-maintenance"),
                "name": "Switchgear Maintenance Matrix",
                "storage_url": "s3://seed-brightline/documents/switchgear-maintenance.pdf",
                "text_fixture": str(SEED_FIXTURES_DIR / "lockout_tagout_brief.txt"),
                "extracted_at": "2024-07-01T12:00:00+00:00",
                "requirements": [
                    {
                        "id": seed_uuid("requirement:brightline:protective-devices"),
                        "title_en": "Inspect Protective Devices",
                        "title_es": "Inspeccionar dispositivos de protección",
                        "description_en": "Review breaker trip settings and protective relays to confirm coordination remains within design limits.",
                        "description_es": "Revisa ajustes de disparo y relevadores de protección para confirmar que la coordinación sigue dentro de los límites de diseño.",
                        "category": "Preventive Maintenance",
                        "frequency": "monthly",
                        "due_date": "2024-08-30T00:00:00+00:00",
                        "source_ref": "Facility SOP SG-12",
                        "confidence": 0.9,
                        "status": "OPEN",
                        "attributes": {"origin": "seed", "seed": True},
                    },
                    {
                        "id": seed_uuid("requirement:brightline:incident-energy-labels"),
                        "title_en": "Update Incident Energy Labels",
                        "title_es": "Actualizar etiquetas de energía incidente",
                        "description_en": "Recalculate and update incident energy labels after system modifications or at least annually.",
                        "description_es": "Recalcula y actualiza las etiquetas de energía incidente después de cambios en el sistema o al menos cada año.",
                        "category": "Labeling",
                        "frequency": "annual",
                        "due_date": "2025-01-15T00:00:00+00:00",
                        "source_ref": "OSHA 4472 §130.5(H)",
                        "confidence": 0.48,
                        "status": "REVIEW",
                        "attributes": {"origin": "seed", "seed": True},
                    },
                ],
            },
        ],
        "permits": [
            {
                "id": seed_uuid("permit:brightline:tx-license"),
                "name": "Texas Electrical Contractor License",
                "permit_number": "TX-ELC-45821",
                "permit_type": "License",
                "jurisdiction": "Texas Department of Licensing and Regulation",
                "issued_at": "2023-01-01T00:00:00+00:00",
                "expires_at": "2025-01-01T00:00:00+00:00",
                "storage_url": "s3://seed-brightline/permits/tx-contractor-license.pdf",
            },
            {
                "id": seed_uuid("permit:brightline:austin-panel-upgrade"),
                "name": "City of Austin Panel Upgrade Permit",
                "permit_number": "AUS-24-1187",
                "permit_type": "Permit",
                "jurisdiction": "City of Austin Development Services",
                "issued_at": "2024-04-10T00:00:00+00:00",
                "expires_at": "2024-11-15T00:00:00+00:00",
                "storage_url": "s3://seed-brightline/permits/austin-panel-upgrade.pdf",
            },
        ],
        "training_certs": [
            {
                "id": seed_uuid("training:brightline:jordan-lewis-osha30"),
                "worker_name": "Jordan Lewis",
                "certification_type": "OSHA 30 Electrical Safety",
                "authority": "OSHA",
                "issued_at": "2023-07-01T00:00:00+00:00",
                "expires_at": "2025-07-01T00:00:00+00:00",
                "storage_url": "s3://seed-brightline/training/jordan-lewis-osha30.pdf",
            },
        ],
    },
    {
        "org": {
            "id": seed_uuid("org:evergreen-renewables"),
            "name": "Evergreen Renewables",
            "slug": "evergreen-renewables",
            "primary_trade": "electrical",
        },
        "users": [
            {
                "id": seed_uuid("user:dante-harris"),
                "email": "dante.harris@evergreenrenewables.com",
                "full_name": "Dante Harris",
                "preferred_locale": "en",
                "role": "owner",
            },
            {
                "id": seed_uuid("user:sofia-chan"),
                "email": "sofia.chan@evergreenrenewables.com",
                "full_name": "Sofia Chan",
                "preferred_locale": "en",
                "role": "admin",
            },
        ],
        "documents": [
            {
                "id": seed_uuid("document:evergreen:lockout-tagout"),
                "name": "Lockout Tagout Checklist",
                "storage_url": "s3://seed-evergreen/documents/lockout-tagout-checklist.pdf",
                "text_fixture": str(SEED_FIXTURES_DIR / "lockout_tagout_brief.txt"),
                "extracted_at": "2024-06-20T15:00:00+00:00",
                "requirements": [
                    {
                        "id": seed_uuid("requirement:evergreen:panel-schedules"),
                        "title_en": "Verify Panel Schedules",
                        "title_es": "Verificar horarios de tableros",
                        "description_en": "Compare panel schedules against installed equipment and sign off on changes.",
                        "description_es": "Compara horarios de tableros con el equipo instalado y aprueba los cambios por escrito.",
                        "category": "Lockout/Tagout",
                        "frequency": "monthly",
                        "due_date": "2024-09-15T00:00:00+00:00",
                        "source_ref": "Company LOTO-04",
                        "confidence": 0.76,
                        "status": "OPEN",
                        "attributes": {"origin": "seed", "seed": True},
                    },
                    {
                        "id": seed_uuid("requirement:evergreen:breaker-inspections"),
                        "title_en": "Document Breaker Inspections",
                        "title_es": "Documentar inspecciones de interruptores",
                        "description_en": "Log annual breaker testing, torque checks, and corrective actions for audit readiness.",
                        "description_es": "Registra pruebas anuales, ajustes de torque y acciones correctivas de interruptores para estar listo ante auditorías.",
                        "category": "Lockout/Tagout",
                        "frequency": "annual",
                        "due_date": "2025-02-01T00:00:00+00:00",
                        "source_ref": "Company LOTO-05",
                        "confidence": 0.7,
                        "status": "OPEN",
                        "attributes": {"origin": "seed", "seed": True},
                    },
                ],
            },
            {
                "id": seed_uuid("document:evergreen:training-matrix"),
                "name": "Crew Training Matrix",
                "storage_url": "s3://seed-evergreen/documents/crew-training-matrix.pdf",
                "text_fixture": str(SEED_FIXTURES_DIR / "training_matrix_excerpt.txt"),
                "extracted_at": "2024-07-05T18:00:00+00:00",
                "requirements": [
                    {
                        "id": seed_uuid("requirement:evergreen:qualified-worker-training"),
                        "title_en": "Renew Qualified Worker Training",
                        "title_es": "Renovar capacitación de trabajadores calificados",
                        "description_en": "Schedule arc flash and shock hazard training refreshers for qualified persons.",
                        "description_es": "Programa repasos de capacitación sobre arco eléctrico y choques para el personal calificado.",
                        "category": "Training",
                        "frequency": "annual",
                        "due_date": "2024-12-01T00:00:00+00:00",
                        "source_ref": "Training Matrix 2024",
                        "confidence": 0.82,
                        "status": "OPEN",
                        "attributes": {"origin": "seed", "seed": True},
                    },
                    {
                        "id": seed_uuid("requirement:evergreen:cpr-cards"),
                        "title_en": "Refresh CPR and First Aid Cards",
                        "title_es": "Renovar credenciales de RCP y primeros auxilios",
                        "description_en": "Ensure field crews maintain active CPR/First Aid credentials aligned with OSHA 1926.23.",
                        "description_es": "Asegura que las cuadrillas mantengan vigentes las credenciales de RCP y primeros auxilios conforme a OSHA 1926.23.",
                        "category": "Training",
                        "frequency": "annual",
                        "due_date": "2024-11-15T00:00:00+00:00",
                        "source_ref": "Training Matrix 2024",
                        "confidence": 0.78,
                        "status": "OPEN",
                        "attributes": {"origin": "seed", "seed": True},
                    },
                    {
                        "id": seed_uuid("requirement:evergreen:tailboard-notes"),
                        "title_en": "Publish Daily Tailboard Notes",
                        "title_es": "Publicar notas diarias de charla de seguridad",
                        "description_en": "Capture daily job brief sign-offs with hazard review and mitigation steps.",
                        "description_es": "Captura firmas diarias de charlas de seguridad con revisión de riesgos y medidas de mitigación.",
                        "category": "Safe Work Practices",
                        "frequency": "daily",
                        "due_date": "2024-08-02T12:00:00+00:00",
                        "source_ref": "Training Matrix 2024",
                        "confidence": 0.75,
                        "status": "OPEN",
                        "attributes": {"origin": "seed", "seed": True},
                    },
                ],
            },
        ],
        "permits": [
            {
                "id": seed_uuid("permit:evergreen:wa-license"),
                "name": "Washington Electrical Contractor License",
                "permit_number": "WA-ELC-99321",
                "permit_type": "License",
                "jurisdiction": "WA Department of Labor & Industries",
                "issued_at": "2022-07-01T00:00:00+00:00",
                "expires_at": "2024-09-30T00:00:00+00:00",
                "storage_url": "s3://seed-evergreen/permits/wa-contractor-license.pdf",
            },
        ],
        "training_certs": [
            {
                "id": seed_uuid("training:evergreen:sofia-chan-cpr"),
                "worker_name": "Sofia Chan",
                "certification_type": "CPR / First Aid",
                "authority": "Red Cross",
                "issued_at": "2023-12-01T00:00:00+00:00",
                "expires_at": "2024-12-01T00:00:00+00:00",
                "storage_url": "s3://seed-evergreen/training/sofia-chan-cpr.pdf",
            },
            {
                "id": seed_uuid("training:evergreen:luis-ortega-mewp"),
                "worker_name": "Luis Ortega",
                "certification_type": "MEWP / Boom Lift Operator",
                "authority": "IPAF",
                "issued_at": "2024-04-15T00:00:00+00:00",
                "expires_at": "2024-10-15T00:00:00+00:00",
                "storage_url": "s3://seed-evergreen/training/luis-ortega-mewp.pdf",
            },
        ],
    },
]


def seed_users(session, org: Org, users: list[dict[str, Any]]) -> None:
    for payload in users:
        user = session.get(User, payload["id"])
        if user is None:
            user = (
                session.query(User)
                .filter(User.email == payload["email"])
                .one_or_none()
            )
            if user is not None and user.id != payload["id"]:
                logger.debug(
                    "Seed user id mismatch for %s (existing=%s expected=%s); reusing existing record",
                    payload["email"],
                    user.id,
                    payload["id"],
                )
        if user is None:
            user = User(id=payload["id"], email=payload["email"])
            session.add(user)
        else:
            user.email = payload["email"]
        user.full_name = payload.get("full_name")
        user.preferred_locale = payload.get("preferred_locale", "en")
        user.is_active = True

        session.flush([user])

        membership = (
            session.query(Membership)
            .filter(Membership.org_id == org.id, Membership.user_id == user.id)
            .one_or_none()
        )
        role = require_role(payload.get("role", "member"))
        if membership is None:
            membership = Membership(org_id=org.id, user_id=user.id, role=role)
            session.add(membership)
        else:
            membership.role = role


def seed_documents(session, org: Org, documents: list[dict[str, Any]]) -> int:
    created_requirements = 0
    for payload in documents:
        document = session.get(Document, payload["id"])
        if document is None:
            document = Document(id=payload["id"], org_id=org.id, name=payload["name"])
            session.add(document)
        document.org_id = org.id
        document.name = payload["name"]
        document.storage_url = payload.get("storage_url")
        document.text_excerpt = load_excerpt(payload.get("text_fixture"))
        document.extracted_at = parse_dt(payload.get("extracted_at"))
        session.flush()

        created_requirements += seed_requirements(session, org, document, payload.get("requirements", []))

    return created_requirements


def seed_requirements(
    session,
    org: Org,
    document: Document,
    requirements: list[dict[str, Any]],
) -> int:
    new_count = 0
    for payload in requirements:
        requirement = session.get(Requirement, payload["id"])
        if requirement is None:
            requirement = Requirement(id=payload["id"], org_id=org.id, document_id=document.id)
            session.add(requirement)
            new_count += 1
        requirement.org_id = org.id
        requirement.document_id = document.id
        requirement.title_en = payload["title_en"]
        requirement.title_es = payload.get("title_es", payload["title_en"])
        requirement.description_en = payload["description_en"]
        requirement.description_es = payload.get("description_es", payload["description_en"])
        requirement.category = payload.get("category")
        requirement.frequency = parse_frequency(payload.get("frequency"))

        due_date = parse_dt(payload.get("due_date"))
        requirement.due_date = due_date

        anchor_type = parse_anchor_type(payload.get("anchor_type"))
        anchor_value = dict(payload.get("anchor_value") or {})

        if anchor_type is None:
            if due_date is not None:
                anchor_type = RequirementAnchorTypeEnum.CUSTOM_DATE
                anchor_value.setdefault("date", due_date.isoformat())
            else:
                reference = document.created_at or SEED_BASE_TIME
                anchor_type = RequirementAnchorTypeEnum.UPLOAD_DATE
                anchor_value.setdefault("date", reference.isoformat())

        requirement.anchor_type = anchor_type
        requirement.anchor_value = anchor_value

        status = payload.get("status", RequirementStatusEnum.OPEN.value)
        requirement.status = require_status(status)
        requirement.source_ref = payload.get("source_ref", "seed")
        requirement.confidence = float(payload.get("confidence", 0.75))
        requirement.trade = org.primary_trade or "electrical"

        explicit_next_due = parse_dt(payload.get("next_due"))
        if explicit_next_due is not None:
            requirement.next_due = explicit_next_due
        else:
            requirement.next_due = compute_next_due(
                requirement.frequency,
                requirement.anchor_type,
                requirement.anchor_value,
                reference_time=SEED_BASE_TIME,
            )

        if requirement.due_date is None:
            requirement.due_date = requirement.next_due

        base_attributes = payload.get("attributes", {})
        attributes = {**base_attributes, "seed_version": SEED_VERSION}
        attributes.setdefault("document_name", document.name)
        requirement.attributes = attributes

    return new_count


def seed_permits(session, org: Org, permits: list[dict[str, Any]]) -> None:
    for payload in permits:
        permit = session.get(Permit, payload["id"])
        if permit is None:
            permit = Permit(id=payload["id"], org_id=org.id, name=payload["name"], storage_url=payload["storage_url"])
            session.add(permit)
        permit.org_id = org.id
        permit.name = payload["name"]
        permit.permit_number = payload.get("permit_number")
        permit.permit_type = payload.get("permit_type")
        permit.jurisdiction = payload.get("jurisdiction")
        permit.issued_at = parse_dt(payload.get("issued_at"))
        permit.expires_at = parse_dt(payload.get("expires_at"))
        permit.storage_url = payload["storage_url"]


def seed_training(session, org: Org, certs: list[dict[str, Any]]) -> None:
    for payload in certs:
        cert = session.get(TrainingCert, payload["id"])
        if cert is None:
            cert = TrainingCert(
                id=payload["id"],
                org_id=org.id,
                worker_name=payload["worker_name"],
                certification_type=payload["certification_type"],
                storage_url=payload["storage_url"],
            )
            session.add(cert)
        cert.org_id = org.id
        cert.worker_name = payload["worker_name"]
        cert.certification_type = payload["certification_type"]
        cert.authority = payload.get("authority")
        cert.issued_at = parse_dt(payload.get("issued_at"))
        cert.expires_at = parse_dt(payload.get("expires_at"))
        cert.storage_url = payload["storage_url"]


def seed_org_bundle(session, bundle: dict[str, Any]) -> int:
    org_payload = bundle["org"]
    org = session.get(Org, org_payload["id"])
    if org is None:
        org = Org(id=org_payload["id"], name=org_payload["name"], slug=org_payload.get("slug"))
        session.add(org)
    org.name = org_payload["name"]
    org.slug = org_payload.get("slug")
    org.primary_trade = org_payload.get("primary_trade", org.primary_trade or "electrical")
    session.flush()

    seed_users(session, org, bundle.get("users", []))
    created_requirements = seed_documents(session, org, bundle.get("documents", []))
    seed_permits(session, org, bundle.get("permits", []))
    seed_training(session, org, bundle.get("training_certs", []))

    if created_requirements:
        record_requirements_created(session, org.id, created_requirements)
    return created_requirements


def run_seed(target_org_id: uuid.UUID | None = None) -> None:
    session = SessionLocal()
    created_summary: dict[uuid.UUID, int] = {}
    try:
        seed_document_templates(session)
        for bundle in SEED_PAYLOAD:
            org_id = bundle["org"]["id"]
            if target_org_id and org_id != target_org_id:
                continue
            created = seed_org_bundle(session, bundle)
            created_summary[org_id] = created
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    if not created_summary:
        logger.info("No matching org found for seed request")
        return

    for org_id, created in created_summary.items():
        logger.info("Seed applied for org %s (new requirements=%s)", org_id, created)


def seed_document_templates(session) -> None:
    for payload in KNOWN_TEMPLATE_PAYLOADS:
        fingerprint = compute_fingerprint(payload["text"])
        template_id = seed_uuid(f"document_template:{payload['key']}")

        template = (
            session.query(DocumentTemplate)
            .filter(DocumentTemplate.fingerprint == fingerprint)
            .one_or_none()
        )

        if template is None:
            template = DocumentTemplate(
                id=template_id,
                title=payload["title"],
                version=payload["version"],
                trade=payload["trade"],
                fingerprint=fingerprint,
                metadata_json={"seed_key": payload["key"]},
            )
            session.add(template)
            session.flush()
        else:
            template.title = payload["title"]
            template.version = payload["version"]
            template.trade = payload["trade"]
            metadata = dict(template.metadata_json or {})
            metadata.update({"seed_key": payload["key"]})
            template.metadata_json = metadata

        # Replace requirement templates for deterministic seed
        template.requirement_templates[:] = []
        session.flush()

        for req_payload in payload["requirements"]:
            requirement = RequirementTemplate(
                id=seed_uuid(
                    f"requirement_template:{payload['key']}:{req_payload['key']}"
                ),
                document_template_id=template.id,
                title_en=req_payload["title_en"],
                title_es=req_payload["title_es"],
                description_en=req_payload["description_en"],
                description_es=req_payload["description_es"],
                category=req_payload.get("category"),
                frequency=req_payload.get("frequency"),
                anchor_type=req_payload.get("anchor_type"),
                anchor_value=dict(req_payload.get("anchor_value") or {}),
                attributes={
                    **dict(req_payload.get("attributes") or {}),
                    "seed_key": req_payload["key"],
                },
            )
            template.requirement_templates.append(requirement)


if __name__ == "__main__":
    org_value = os.getenv("SEED_ORG_ID")
    chosen_org = uuid.UUID(org_value) if org_value else None
    run_seed(chosen_org)
