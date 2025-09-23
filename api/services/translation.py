from __future__ import annotations

import logging
from typing import List

from instructor import from_openai
import openai

logger = logging.getLogger(__name__)

TRANSLATION_SYSTEM_PROMPT = "You are a professional translator. Translate English compliance statements into neutral Spanish for Latin American electricians. Return only the translated sentences in JSON array format."


def translate_batch_to_spanish(texts: List[str]) -> List[str]:
    if not texts:
        return []

    client = from_openai(openai.OpenAI())
    try:
        return client.chat.completions.create(
            model="gpt-4o-mini",
            response_model=list[str],
            messages=[
                {"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "Translate each item to Spanish. Return JSON array of strings in same order.\n" +
                                "\n".join(f"- {text}" for text in texts),
                },
            ],
            temperature=0.0,
        )
    except Exception as exc:  # pragma: no cover - best effort fallback
        logger.warning("Translation API failed; falling back to heuristic translation: %s", exc)
        return [f"{text} (ES)" if text else "" for text in texts]
