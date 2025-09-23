from __future__ import annotations

from pydantic import BaseModel, Field
from instructor import from_openai
import openai


class RequirementLLMOut(BaseModel):
    title: str
    description: str
    category: str | None = None
    frequency: str | None = None   # or due_date if explicit
    due_date: str | None = None    # ISO if explicit in text
    source_ref: str
    confidence: float = Field(ge=0, le=1)


SYSTEM = (
    "You are a compliance analyst for electrical contractors. "
    "Extract actionable safety and maintenance obligations into structured JSON."
)


USER_TMPL = """Document excerpt:
---
{excerpt}
---
Return a JSON list of objects with keys:
- title (<=120 chars)
- description (1-2 sentences summarising the obligation)
- category (if obvious)
- frequency (daily/weekly/monthly/annual/before each use if present)
- due_date (ISO 8601 if a calendar date exists; otherwise null)
- source_ref (section number or short quote)
- confidence (0.0..1.0)
"""


def extract_requirements_from_text(excerpt: str) -> list[RequirementLLMOut]:
    client = from_openai(openai.OpenAI())
    return client.chat.completions.create(
        model="gpt-4o-mini",
        response_model=list[RequirementLLMOut],
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": USER_TMPL.format(excerpt=excerpt)},
        ],
        temperature=0.2,
    )
