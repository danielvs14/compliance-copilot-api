from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Any


@dataclass
class RequirementDraft:
    title_en: str
    description_en: str
    category: str | None
    frequency: str | None
    due_date: str | None
    source_ref: str
    confidence: float
    origin: str
    title_es: str | None = None
    description_es: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


class TradeRuleSet(Protocol):
    trade: str

    def apply(self, draft: RequirementDraft) -> RequirementDraft:
        ...


class ElectricalRuleSet:
    trade = "electrical"
    default_category = "Electrical Safety"

    def apply(self, draft: RequirementDraft) -> RequirementDraft:
        if not draft.category:
            draft.category = self.default_category
        return draft


TRADE_RULESETS: dict[str, TradeRuleSet] = {
    "electrical": ElectricalRuleSet(),
}


def apply_trade_rules(trade: str, draft: RequirementDraft) -> RequirementDraft:
    ruleset = TRADE_RULESETS.get(trade.lower())
    if not ruleset:
        return draft
    return ruleset.apply(draft)
