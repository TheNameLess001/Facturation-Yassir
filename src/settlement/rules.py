from __future__ import annotations

import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field

from src.models.enums import FinancialDecision


def normalize_rule_value(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^A-Z0-9]+", "_", text.upper()).strip("_")


class RuleOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision: FinancialDecision
    reason: str


class SettlementRuleConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    status_rules: dict[str, RuleOutcome] = Field(default_factory=dict)
    cancellation_rules: dict[str, RuleOutcome] = Field(default_factory=dict)
    cancelled_statuses: frozenset[str] = frozenset({"CANCELLED", "CANCELED"})

    @classmethod
    def safe_defaults(cls) -> SettlementRuleConfig:
        return cls(
            status_rules={
                "DELIVERED": RuleOutcome(
                    decision=FinancialDecision.PAY_PARTNER,
                    reason="DELIVERED_ORDER",
                ),
            },
            cancellation_rules={
                "RESTAURANT": RuleOutcome(
                    decision=FinancialDecision.EXCLUDE,
                    reason="PARTNER_RESPONSIBILITY",
                ),
                "PARTNER": RuleOutcome(
                    decision=FinancialDecision.EXCLUDE,
                    reason="PARTNER_RESPONSIBILITY",
                ),
                "YASSIR": RuleOutcome(
                    decision=FinancialDecision.YASSIR_COMPENSATION,
                    reason="YASSIR_RESPONSIBILITY",
                ),
                "DRIVER": RuleOutcome(
                    decision=FinancialDecision.PAY_PARTNER,
                    reason="YASSIR_RESPONSIBILITY",
                ),
            },
        )

    @classmethod
    def from_overrides(cls, overrides: dict[str, object]) -> SettlementRuleConfig:
        base = cls.safe_defaults().model_dump(mode="json")
        for key in ("status_rules", "cancellation_rules", "cancelled_statuses"):
            if key in overrides:
                base[key] = overrides[key]
        return cls.model_validate(base)


class SettlementRuleEngine:
    def __init__(self, config: SettlementRuleConfig | None = None) -> None:
        self.config = config or SettlementRuleConfig.safe_defaults()

    def decide(
        self, operational_status: str, cancellation_reason: str | None
    ) -> RuleOutcome:
        status = normalize_rule_value(operational_status)
        if status in self.config.cancelled_statuses:
            reason = normalize_rule_value(cancellation_reason)
            return self.config.cancellation_rules.get(
                reason,
                RuleOutcome(
                    decision=FinancialDecision.MANUAL_REVIEW,
                    reason="UNKNOWN_CANCELLATION_RESPONSIBILITY",
                ),
            )
        return self.config.status_rules.get(
            status,
            RuleOutcome(
                decision=FinancialDecision.MANUAL_REVIEW,
                reason="UNCONFIGURED_OPERATIONAL_STATUS",
            ),
        )
