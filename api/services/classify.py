from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence
from uuid import UUID

from sqlalchemy.orm import Session

from ..models.events import Event

ClassificationLabel = Literal["requirements", "permit", "training", "uncategorized"]


@dataclass
class ClassificationResult:
    label: ClassificationLabel
    confidence: float
    matches: list[str]


_PERMIT_KEYWORDS: Sequence[str] = (
    "permit",
    "license",
    "licence",
    "authorization",
    "approval",
    "bonding",
    "inspection certificate",
    "issuing authority",
)

_TRAINING_KEYWORDS: Sequence[str] = (
    "training",
    "certificate",
    "certification",
    "osha",
    "cpr",
    "ladder safety",
    "fall protection",
    "competency",
    "safety course",
)

_REQUIREMENT_KEYWORDS: Sequence[str] = (
    "requirement",
    "policy",
    "compliance",
    "standard",
    "inspection",
    "checklist",
)


def _score(text: str, keywords: Sequence[str]) -> tuple[int, list[str]]:
    matches: list[str] = []
    for keyword in keywords:
        if keyword in text:
            matches.append(keyword)
    return len(matches), matches


def _confidence_from_matches(match_count: int) -> float:
    if match_count <= 0:
        return 0.0
    return min(1.0, 0.35 + 0.2 * match_count)


def _has_expiration_signal(text: str) -> bool:
    tokens = ("expiration", "expires", "expire", "valid until", "renewal", "expiry")
    return any(token in text for token in tokens)


def _has_issue_signal(text: str) -> bool:
    tokens = ("issued", "issue date", "effective", "authorized")
    return any(token in text for token in tokens)


def classify_document(text: str, filename: str | None = None) -> ClassificationResult:
    """Return a coarse-grained classification with a lightweight confidence score."""

    haystack = text.lower()
    if filename:
        haystack = f"{filename.lower()}\n{haystack}"

    permit_score, permit_matches = _score(haystack, _PERMIT_KEYWORDS)
    training_score, training_matches = _score(haystack, _TRAINING_KEYWORDS)
    requirements_score, requirement_matches = _score(haystack, _REQUIREMENT_KEYWORDS)

    has_expiry = _has_expiration_signal(haystack)
    has_issue = _has_issue_signal(haystack)

    if permit_score == 0 and training_score == 0 and requirements_score == 0:
        return ClassificationResult(label="uncategorized", confidence=0.1, matches=[])

    permit_confidence = _confidence_from_matches(permit_score)
    training_confidence = _confidence_from_matches(training_score)

    permit_match = "permit" in permit_matches or "license" in permit_matches
    training_match = "training" in training_matches or "certificate" in training_matches

    permit_strong = permit_score >= 2 or (permit_match and has_expiry and has_issue)
    training_strong = training_score >= 2 or (training_match and has_expiry)

    requirements_confidence = _confidence_from_matches(requirements_score)

    if (
        permit_strong
        and permit_confidence >= 0.6
        and permit_score >= training_score
        and permit_score >= requirements_score + 1
    ):
        return ClassificationResult(
            label="permit",
            confidence=max(permit_confidence, 0.65 if has_expiry else 0.6),
            matches=permit_matches,
        )

    if (
        training_strong
        and training_confidence >= 0.6
        and training_score >= permit_score
        and training_score >= requirements_score + 1
    ):
        return ClassificationResult(
            label="training",
            confidence=max(training_confidence, 0.65 if has_expiry else 0.6),
            matches=training_matches,
        )

    if requirements_score and requirements_confidence >= 0.35:
        return ClassificationResult(
            label="requirements",
            confidence=requirements_confidence,
            matches=requirement_matches,
        )

    if permit_strong and permit_confidence >= 0.5:
        return ClassificationResult(
            label="permit",
            confidence=permit_confidence,
            matches=permit_matches,
        )

    if training_strong and training_confidence >= 0.5:
        return ClassificationResult(
            label="training",
            confidence=training_confidence,
            matches=training_matches,
        )

    return ClassificationResult(label="uncategorized", confidence=0.2, matches=[])


def get_override_for_hash(db: Session, org_id: UUID, file_hash: str) -> str | None:
    event = (
        db.query(Event)
        .filter(
            Event.org_id == org_id,
            Event.type == "classification_override",
            Event.data["file_hash"].astext == file_hash,
        )
        .order_by(Event.at.desc())
        .first()
    )
    if event and isinstance(event.data, dict):
        label = event.data.get("label")
        if isinstance(label, str):
            return label
    return None


def record_override_event(db: Session, org_id: UUID, document_id: UUID, file_hash: str, label: str) -> None:
    sanitized_label = label.lower()
    db.add(
        Event(
            org_id=org_id,
            document_id=document_id,
            requirement_id=None,
            type="classification_override",
            data={
                "file_hash": file_hash,
                "label": sanitized_label,
            },
        )
    )
