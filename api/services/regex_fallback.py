from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

FREQUENCY_KEYWORDS = {
    "daily": re.compile(r"\b(daily|cada\s+d[ií]a)\b", re.IGNORECASE),
    "weekly": re.compile(r"\b(weekly|cada\s+semana)\b", re.IGNORECASE),
    "monthly": re.compile(r"\b(monthly|cada\s+mes)\b", re.IGNORECASE),
    "annual": re.compile(r"\b(annual|annually|cada\s+a[nñ]o)\b", re.IGNORECASE),
    "before each use": re.compile(r"\b(before\s+each\s+use|antes\s+de\s+cada\s+uso)\b", re.IGNORECASE),
}


@dataclass
class RegexRequirement:
    title_en: str
    description_en: str
    frequency: str
    source_ref: str
    confidence: float = 0.45
    origin: str = "regex"


def regex_fallback_requirements(text: str) -> List[RegexRequirement]:
    if not text:
        return []

    seen_sentences: set[str] = set()
    requirements: List[RegexRequirement] = []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for sentence in sentences:
        normalized = sentence.strip()
        if len(normalized) < 20:
            continue
        frequency = None
        for label, pattern in FREQUENCY_KEYWORDS.items():
            if pattern.search(normalized):
                frequency = label
                break
        if not frequency:
            continue

        sentence_key = re.sub(r"\s+", " ", normalized.lower())
        if sentence_key in seen_sentences:
            continue
        seen_sentences.add(sentence_key)

        title = normalized[:120].rstrip(" .")
        requirements.append(
            RegexRequirement(
                title_en=title,
                description_en=normalized,
                frequency=frequency,
                source_ref=title[:50],
            )
        )

    return requirements
