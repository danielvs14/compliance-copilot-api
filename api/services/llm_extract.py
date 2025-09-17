from pydantic import BaseModel, Field
from instructor import from_openai
import openai

class RequirementOut(BaseModel):
    title: str
    category: str | None = None
    frequency: str | None = None   # or due_date if explicit
    due_date: str | None = None    # ISO if explicit in text
    source_ref: str
    confidence: float = Field(ge=0, le=1)

SYSTEM = "You are a compliance analyst. Extract OSHA-like obligations into structured JSON."

USER_TMPL = """Document excerpt:
---
{excerpt}
---
Return a JSON list of RequirementOut objects.
- Use 'frequency' when cadence is stated (e.g., 'weekly', 'before each use').
- Use 'due_date' only if a specific calendar date is present.
- source_ref: include section number or short quote.
- confidence: 0.0..1.0
"""

def extract_requirements_from_text(excerpt: str) -> list[RequirementOut]:
    client = from_openai(openai.OpenAI())
    return client.chat.completions.create(
        model="gpt-4o-mini",
        response_model=list[RequirementOut],
        messages=[
            {"role":"system","content":SYSTEM},
            {"role":"user","content":USER_TMPL.format(excerpt=excerpt)}
        ],
        temperature=0.2,
    )
