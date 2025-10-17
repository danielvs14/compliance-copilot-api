from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, List, Sequence

from ..config import settings
from .llm_extract import RequirementLLMOut, extract_requirements_from_text
from .regex_fallback import regex_fallback_requirements
from .trade_rules import RequirementDraft, apply_trade_rules
from .translation import translate_batch_to_spanish
from ..models.requirements import RequirementFrequencyEnum

logger = logging.getLogger(__name__)

MAX_CHARS_PER_CHUNK = 4000
MAX_CHUNKS = 3
CACHE_FILE_SUFFIX = ".json"
ACTION_KEYWORDS = (
    "must",
    "shall",
    "ensure",
    "inspect",
    "review",
    "verify",
    "record",
    "document",
    "submit",
    "provide",
    "maintain",
    "wear",
    "issue",
    "barricade",
    "log",
    "schedule",
)
FREQUENCY_SYNONYMS: dict[RequirementFrequencyEnum, tuple[str, ...]] = {
    RequirementFrequencyEnum.BEFORE_EACH_USE: (
        "before each use",
        "before use",
        "prior to use",
        "each use",
        "every use",
        "before each shift",
    ),
    RequirementFrequencyEnum.DAILY: (
        "daily",
        "each day",
        "every day",
        "per day",
        "per shift",
        "each shift",
    ),
    RequirementFrequencyEnum.WEEKLY: (
        "weekly",
        "each week",
        "every week",
    ),
    RequirementFrequencyEnum.MONTHLY: (
        "monthly",
        "each month",
        "every month",
        "per month",
    ),
    RequirementFrequencyEnum.ANNUAL: (
        "annual",
        "annually",
        "each year",
        "every year",
        "yearly",
    ),
}


def chunk_text(text: str) -> List[str]:
    if len(text) <= MAX_CHARS_PER_CHUNK:
        return [text]
    chunk_count = min(MAX_CHUNKS, math.ceil(len(text) / MAX_CHARS_PER_CHUNK))
    chunk_length = math.ceil(len(text) / chunk_count)
    return [text[i : i + chunk_length] for i in range(0, len(text), chunk_length)][:chunk_count]


def normalize_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _resolve_cache_dir() -> Path | None:
    raw_dir = getattr(settings, "extraction_cache_dir", None)
    if not raw_dir:
        return None
    try:
        path = Path(raw_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path
    except Exception as exc:  # pragma: no cover - log & continue
        logger.debug("Unable to initialise extraction cache at %s: %s", raw_dir, exc)
        return None


def _cache_file(cache_dir: Path, chunk_hash: str) -> Path:
    return cache_dir / f"{chunk_hash}{CACHE_FILE_SUFFIX}"


def _load_cached_items(cache_dir: Path, chunk_hash: str) -> list[dict[str, Any]] | None:
    cache_file = _cache_file(cache_dir, chunk_hash)
    if not cache_file.exists():
        return None
    try:
        return json.loads(cache_file.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - cache corruption shouldn't fail run
        logger.warning("Failed to read extraction cache %s: %s", cache_file, exc)
        return None


def _store_cached_items(cache_dir: Path, chunk_hash: str, payload: Sequence[dict[str, Any]]) -> None:
    cache_file = _cache_file(cache_dir, chunk_hash)
    try:
        cache_file.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as exc:  # pragma: no cover - cache write shouldn't break pipeline
        logger.debug("Failed to persist extraction cache %s: %s", cache_file, exc)


def _llm_to_draft(item: RequirementLLMOut, origin: str, chunk_idx: int) -> RequirementDraft:
    return RequirementDraft(
        title_en=item.title.strip(),
        description_en=item.description.strip(),
        category=item.category.strip() if item.category else None,
        frequency=item.frequency.strip() if item.frequency else None,
        due_date=item.due_date.strip() if item.due_date else None,
        source_ref=item.source_ref.strip(),
        confidence=item.confidence,
        origin=origin,
        attributes={"chunk": chunk_idx, "source": origin},
    )


def dedupe_drafts(drafts: Iterable[RequirementDraft]) -> List[RequirementDraft]:
    seen: dict[tuple[str, str, str], RequirementDraft] = {}
    for draft in drafts:
        title_key = normalize_key(draft.title_en)
        source_key = normalize_key(draft.source_ref)
        description_key = normalize_key(draft.description_en)[:160]
        key = (title_key, source_key, description_key)
        existing = seen.get(key)
        if existing is None or draft.confidence > existing.confidence:
            seen[key] = draft
    return list(seen.values())


def _context_for(draft: RequirementDraft) -> str:
    return f"{draft.title_en} {draft.description_en}".lower()


def _normalize_frequency(raw: str | None, *, context: str) -> RequirementFrequencyEnum | None:
    search_space = f"{(raw or '').lower()} {context}"
    for canonical, synonyms in FREQUENCY_SYNONYMS.items():
        if any(term in search_space for term in synonyms):
            return canonical
    return None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _normalize_due_date(
    frequency: RequirementFrequencyEnum | None,
    raw_due: str | None,
    base_time: datetime,
) -> str | None:
    if not frequency:
        return None
    if frequency == RequirementFrequencyEnum.BEFORE_EACH_USE:
        return None

    parsed = _parse_iso(raw_due)
    if parsed and parsed >= base_time:
        normalized = parsed.astimezone(timezone.utc).replace(second=0, microsecond=0)
        return normalized.isoformat()
    return None


def _is_actionable(context: str, confidence: float) -> bool:
    if confidence < 0.35:
        return False
    return any(keyword in context for keyword in ACTION_KEYWORDS)


def _prepare_draft(draft: RequirementDraft, base_time: datetime) -> RequirementDraft | None:
    context = _context_for(draft)
    frequency = _normalize_frequency(draft.frequency, context=context)
    triage_flags = list(draft.triage_flags)

    if frequency:
        draft.frequency = frequency
        draft.due_date = _normalize_due_date(frequency, draft.due_date, base_time)
    else:
        draft.frequency = None
        triage_flags.append("missing_frequency")

    if not _is_actionable(context, draft.confidence):
        return None

    if frequency:
        draft.attributes.setdefault("normalized_frequency", frequency.value)
        if draft.due_date:
            draft.attributes["normalized_due_date"] = draft.due_date

    if triage_flags:
        draft.triage_flags = triage_flags
        draft.attributes.setdefault("triage_flags", triage_flags)

    return draft


def _fetch_llm_items(chunk: str, cache_dir: Path | None) -> tuple[list[RequirementLLMOut], bool]:
    chunk_hash = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
    if cache_dir:
        cached = _load_cached_items(cache_dir, chunk_hash)
        if cached is not None:
            items = [RequirementLLMOut.model_validate(item) for item in cached]
            return items, True

    items = extract_requirements_from_text(chunk)

    if cache_dir:
        payload = [item.model_dump() for item in items]
        _store_cached_items(cache_dir, chunk_hash, payload)

    return items, False


def extract_requirement_drafts(text: str, trade: str = "electrical") -> List[RequirementDraft]:
    if not text:
        return []

    cache_dir = _resolve_cache_dir()
    chunks = chunk_text(text)
    base_time = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    raw_drafts: List[RequirementDraft] = []
    for idx, chunk in enumerate(chunks):
        try:
            items, cache_hit = _fetch_llm_items(chunk, cache_dir)
        except Exception:  # pragma: no cover - propagate meaningful error
            logger.exception("LLM extraction failed on chunk %s", idx)
            raise
        for item in items:
            draft = _llm_to_draft(item, origin="llm", chunk_idx=idx)
            draft.attributes["cache_hit"] = cache_hit
            raw_drafts.append(draft)

    for regex_req in regex_fallback_requirements(text):
        raw_drafts.append(
            RequirementDraft(
                title_en=regex_req.title_en,
                description_en=regex_req.description_en,
                category=None,
                frequency=regex_req.frequency,
                due_date=None,
                source_ref=regex_req.source_ref,
                confidence=regex_req.confidence,
                origin=regex_req.origin,
                attributes={"source": "regex"},
            )
        )

    prepared: List[RequirementDraft] = []
    for draft in raw_drafts:
        normalized = _prepare_draft(draft, base_time)
        if normalized:
            prepared.append(normalized)

    deduped = dedupe_drafts(prepared)

    enriched: List[RequirementDraft] = []
    for draft in deduped:
        enriched.append(apply_trade_rules(trade, draft))

    return enriched


def attach_translations(drafts: List[RequirementDraft]) -> List[RequirementDraft]:
    if not drafts:
        return drafts

    titles = [draft.title_en for draft in drafts]
    descriptions = [draft.description_en for draft in drafts]
    translated_titles = translate_batch_to_spanish(titles)
    if len(translated_titles) != len(drafts):
        translated_titles = titles

    translated_descriptions = translate_batch_to_spanish(descriptions)
    if len(translated_descriptions) != len(drafts):
        translated_descriptions = descriptions

    for draft, title_es, description_es in zip(drafts, translated_titles, translated_descriptions):
        draft.title_es = title_es
        draft.description_es = description_es

    return drafts
