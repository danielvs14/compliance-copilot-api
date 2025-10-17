from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from api.models.requirements import RequirementFrequencyEnum
from api.services import extraction_pipeline
from api.services.llm_extract import RequirementLLMOut


@pytest.fixture(autouse=True)
def reset_cache_dir(monkeypatch, tmp_path) -> None:
    """Point extraction cache to a temp directory for deterministic tests."""
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(extraction_pipeline.settings, "extraction_cache_dir", cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    # ensure we start with a clean cache between tests
    if cache_dir.exists():
        for path in cache_dir.iterdir():
            path.unlink()


def _make_llm_item(**overrides: Any) -> RequirementLLMOut:
    payload = {
        "title": "Inspect Protective Devices",
        "description": "Inspect protective devices monthly and document findings.",
        "category": "Electrical Safety",
        "frequency": "Each Month",
        "due_date": None,
        "source_ref": "OSHA 4472 §130.3",
        "confidence": 0.82,
    }
    payload.update(overrides)
    return RequirementLLMOut(**payload)


def test_extract_requirement_drafts_filters_non_actionable(monkeypatch) -> None:
    items = [
        _make_llm_item(
            title="Maintain Equipment to Prevent Arc Flashes",
            description="Proper maintenance of equipment is essential to reduce the likelihood of arc flash incidents.",
            frequency=None,
        ),
        _make_llm_item(),
    ]

    call_count = {"count": 0}

    def fake_extract(_: str) -> list[RequirementLLMOut]:
        call_count["count"] += 1
        return items

    monkeypatch.setattr(extraction_pipeline, "extract_requirements_from_text", fake_extract)

    drafts = extraction_pipeline.extract_requirement_drafts("Example text")

    assert call_count["count"] == 1
    assert len(drafts) == 2
    missing_frequency = [d for d in drafts if d.frequency is None][0]
    assert "missing_frequency" in missing_frequency.attributes.get("triage_flags", [])
    monthly = [d for d in drafts if d.frequency == RequirementFrequencyEnum.MONTHLY][0]
    assert monthly.due_date is None


def test_extract_requirement_drafts_uses_cache(monkeypatch, tmp_path) -> None:
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(extraction_pipeline, "_resolve_cache_dir", lambda: cache_dir)

    returned: list[list[RequirementLLMOut]] = [
        [_make_llm_item()],
        [_make_llm_item(title="Duplicate", description="Duplicate entry", source_ref="Sec 2")],
    ]
    call_count = {"count": 0}

    def fake_extract(_: str) -> list[RequirementLLMOut]:
        payload = returned[min(call_count["count"], len(returned) - 1)]
        call_count["count"] += 1
        return payload

    monkeypatch.setattr(extraction_pipeline, "extract_requirements_from_text", fake_extract)

    first = extraction_pipeline.extract_requirement_drafts("Some long enough text")
    second = extraction_pipeline.extract_requirement_drafts("Some long enough text")

    assert call_count["count"] == 1
    assert first[0].attributes.get("cache_hit") is False
    assert second[0].attributes.get("cache_hit") is True


def test_extract_requirement_drafts_drops_duplicates(monkeypatch) -> None:
    item = _make_llm_item()
    items = [item, _make_llm_item(description=item.description, source_ref=item.source_ref, confidence=0.5)]

    monkeypatch.setattr(extraction_pipeline, "extract_requirements_from_text", lambda _: items)

    drafts = extraction_pipeline.extract_requirement_drafts("Duplicate text")
    assert len(drafts) == 1
    assert drafts[0].confidence == pytest.approx(0.82)


def test_extract_requirement_drafts_strips_due_for_before_each_use(monkeypatch) -> None:
    monkeypatch.setattr(
        extraction_pipeline,
        "extract_requirements_from_text",
        lambda _: [
            _make_llm_item(
                title="Barricade Arc Flash Boundary",
                description="Employers must barricade the arc flash boundary before each shift.",
                frequency="Before each use",
                due_date="2024-07-01T00:00:00Z",
            ),
        ],
    )

    drafts = extraction_pipeline.extract_requirement_drafts("Arc flash text")
    assert len(drafts) == 1
    assert drafts[0].frequency == RequirementFrequencyEnum.BEFORE_EACH_USE
    assert drafts[0].due_date is None
