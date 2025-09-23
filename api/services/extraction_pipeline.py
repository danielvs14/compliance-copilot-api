from __future__ import annotations

import logging
import math
import re
from typing import Iterable, List

from .llm_extract import RequirementLLMOut, extract_requirements_from_text
from .regex_fallback import regex_fallback_requirements
from .trade_rules import RequirementDraft, apply_trade_rules
from .translation import translate_batch_to_spanish

logger = logging.getLogger(__name__)

MAX_CHARS_PER_CHUNK = 4000
MAX_CHUNKS = 3


def chunk_text(text: str) -> List[str]:
    if len(text) <= MAX_CHARS_PER_CHUNK:
        return [text]
    chunk_count = min(MAX_CHUNKS, math.ceil(len(text) / MAX_CHARS_PER_CHUNK))
    chunk_length = math.ceil(len(text) / chunk_count)
    return [text[i : i + chunk_length] for i in range(0, len(text), chunk_length)][:chunk_count]


def normalize_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


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
    seen: dict[tuple[str, str], RequirementDraft] = {}
    for draft in drafts:
        title_key = normalize_key(draft.title_en)
        source_key = normalize_key(draft.source_ref)
        key = (title_key, source_key)
        existing = seen.get(key)
        if existing is None or draft.confidence > existing.confidence:
            seen[key] = draft
    return list(seen.values())


def extract_requirement_drafts(text: str, trade: str = "electrical") -> List[RequirementDraft]:
    if not text:
        return []

    chunks = chunk_text(text)
    drafts: List[RequirementDraft] = []
    for idx, chunk in enumerate(chunks):
        try:
            items = extract_requirements_from_text(chunk)
        except Exception as exc:  # pragma: no cover - propagate meaningful error
            logger.exception("LLM extraction failed on chunk %s", idx)
            raise
        for item in items:
            drafts.append(_llm_to_draft(item, origin="llm", chunk_idx=idx))

    for regex_req in regex_fallback_requirements(text):
        drafts.append(
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

    deduped = dedupe_drafts(drafts)

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
