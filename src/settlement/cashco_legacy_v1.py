from __future__ import annotations

import math
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

POLICY_VERSION = "cashco_legacy_v1"
AUTHORITATIVE_SOURCE = "4_Generateur bulk.py"
TVA_RATE = Decimal("0.20")
SALES_HT_DIVISOR = Decimal("1.2")
DISPLAY_QUANTUM = Decimal("0.01")


class LegacyInputValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: Decimal
    defaulted: bool = False
    warning_code: str | None = None


class LegacyFinancialResult(BaseModel):
    """Exact unrounded monetary chain from the approved legacy generator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sales_ttc: Decimal
    sales_ht: Decimal
    commission_rate: Decimal
    commission_ht: Decimal
    tva_rate: Decimal
    tva: Decimal
    invoice_ttc: Decimal
    net_payable: Decimal
    policy_version: str = POLICY_VERSION
    data_quality_warnings: tuple[str, ...] = ()

    @property
    def commission_base(self) -> Decimal:
        return self.sales_ht

    @property
    def note_de_debours_deduction(self) -> Decimal:
        return self.invoice_ttc

    @property
    def note_de_debours_payable(self) -> Decimal:
        return self.net_payable

    @property
    def reconciliation_difference(self) -> Decimal:
        return self.sales_ttc - (self.net_payable + self.invoice_ttc)

    def display_value(self, field: str) -> Decimal:
        value = getattr(self, field)
        if not isinstance(value, Decimal):
            raise TypeError(f"{field} is not a monetary Decimal field")
        return value.quantize(DISPLAY_QUANTUM, rounding=ROUND_HALF_EVEN)

    def amount_to_words_value(self, field: str = "net_payable") -> Decimal:
        return self.display_value(field)


class CashCoLegacyV1Policy:
    """Certified reproduction of the owner-approved production calculation block."""

    policy_version = POLICY_VERSION
    authoritative_source = AUTHORITATIVE_SOURCE
    tva_rate = TVA_RATE
    presentation_quantum = DISPLAY_QUANTUM
    business_owner_approved = True
    authoritative_cases: ClassVar[tuple[tuple[str, str, tuple[str, ...]], ...]] = (
        ("120 MAD", "20%", ("120", "100", "20", "4", "24", "96")),
        ("240 MAD", "24%", ("240", "200", "48", "9.6", "57.6", "182.4")),
        ("100 MAD", "0%", ("100", "83.33333333333333333333333333", "0", "0", "0", "100")),
    )

    def calculate(
        self,
        item_total: object,
        commission_rate: object,
    ) -> LegacyFinancialResult:
        sales_input = self.clean_currency(item_total)
        rate_input = self.normalize_commission_rate(commission_rate)
        sales_ttc = sales_input.value
        sales_ht = sales_ttc / SALES_HT_DIVISOR
        commission_ht = sales_ht * rate_input.value
        tva = commission_ht * TVA_RATE
        invoice_ttc = commission_ht + tva
        net_payable = sales_ttc - invoice_ttc
        warnings = tuple(
            code
            for code in (sales_input.warning_code, rate_input.warning_code)
            if code is not None
        )
        return LegacyFinancialResult(
            sales_ttc=sales_ttc,
            sales_ht=sales_ht,
            commission_rate=rate_input.value,
            commission_ht=commission_ht,
            tva_rate=TVA_RATE,
            tva=tva,
            invoice_ttc=invoice_ttc,
            net_payable=net_payable,
            data_quality_warnings=warnings,
        )

    @staticmethod
    def clean_currency(value: object) -> LegacyInputValue:
        if CashCoLegacyV1Policy._is_missing(value):
            return LegacyInputValue(
                value=Decimal(0),
                defaulted=True,
                warning_code="LEGACY_MONETARY_INPUT_DEFAULTED_MISSING",
            )
        text = str(value).strip().upper()
        normalized = (
            text.replace("MAD", "")
            .replace("DH", "")
            .replace(" ", "")
            .replace("\u00a0", "")
            .replace(",", ".")
            .strip()
        )
        try:
            parsed = Decimal(normalized)
            if not parsed.is_finite():
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            return LegacyInputValue(
                value=Decimal(0),
                defaulted=True,
                warning_code="LEGACY_MONETARY_INPUT_DEFAULTED_INVALID",
            )
        return LegacyInputValue(value=parsed)

    @staticmethod
    def normalize_commission_rate(value: object) -> LegacyInputValue:
        if CashCoLegacyV1Policy._is_missing(value):
            return LegacyInputValue(
                value=Decimal(0),
                defaulted=True,
                warning_code="LEGACY_COMMISSION_RATE_DEFAULTED_MISSING",
            )
        text = str(value).strip().replace("%", "").replace(" ", "").replace(",", ".")
        try:
            parsed = Decimal(text)
            if not parsed.is_finite():
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            return LegacyInputValue(
                value=Decimal(0),
                defaulted=True,
                warning_code="LEGACY_COMMISSION_RATE_DEFAULTED_INVALID",
            )
        normalized = parsed / Decimal(100) if parsed > 1 else parsed
        return LegacyInputValue(value=normalized)

    @staticmethod
    def _is_missing(value: object) -> bool:
        if value is None:
            return True
        if isinstance(value, Decimal):
            return value.is_nan()
        if isinstance(value, float):
            return math.isnan(value)
        return not str(value).strip()

    def eligible_order_amount(self, order: dict[str, object]) -> Decimal | None:
        if order.get("eligible") is False:
            return None
        decision = str(order.get("final_financial_decision") or "").upper()
        if decision and decision != "PAY_PARTNER":
            return None
        raw = order.get("item_total", order.get("amount"))
        return self.clean_currency(raw).value

    def commission_base(self, values: dict[str, object]) -> Decimal:
        raw_sales_ttc = values.get("sales_ttc")
        if raw_sales_ttc is None:
            raw_sales_ttc = values.get("eligible_partner_amount")
        sales_ttc = self._decimal_value(
            raw_sales_ttc,
            "sales_ttc",
        )
        return sales_ttc / SALES_HT_DIVISOR

    def commission_amount(self, base: Decimal, rate: Decimal) -> Decimal:
        return base * self.normalize_commission_rate(rate).value

    @staticmethod
    def invoice_ht(values: dict[str, Decimal]) -> Decimal:
        return values["commission_amount"]

    @staticmethod
    def invoice_tva(invoice_ht: Decimal) -> Decimal:
        return invoice_ht * TVA_RATE

    @staticmethod
    def invoice_ttc(invoice_ht: Decimal, invoice_tva: Decimal) -> Decimal:
        return invoice_ht + invoice_tva

    @staticmethod
    def note_de_debours(values: dict[str, Decimal]) -> Decimal:
        return values["eligible_partner_amount"] - values["invoice_ttc"]

    @staticmethod
    def final_net_payable(values: dict[str, Decimal]) -> Decimal:
        return values["eligible_partner_amount"] - values["invoice_ttc"]

    @staticmethod
    def rounding_policy(value: Decimal, field: str) -> Decimal:
        """Presentation-only rounding; calculations never call this method."""

        if not field:
            raise ValueError("A presentation field is required")
        return value.quantize(DISPLAY_QUANTUM, rounding=ROUND_HALF_EVEN)

    def implementation_matches_authoritative_cases(self) -> bool:
        for item_total, rate, expected in self.authoritative_cases:
            result = self.calculate(item_total, rate)
            actual = (
                result.sales_ttc,
                result.sales_ht,
                result.commission_ht,
                result.tva,
                result.invoice_ttc,
                result.net_payable,
            )
            if actual != tuple(Decimal(item) for item in expected):
                return False
            if result.reconciliation_difference != 0:
                return False
            if result.invoice_ttc != result.commission_ht + result.tva:
                return False
            if result.tva != result.commission_ht * TVA_RATE:
                return False
        return True

    @staticmethod
    def _decimal_value(value: object, field: str) -> Decimal:
        if not isinstance(value, Decimal):
            raise TypeError(f"{field} must be Decimal")
        return value
