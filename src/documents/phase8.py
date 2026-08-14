from __future__ import annotations

import hashlib
import html
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from src.documents.legal_readiness import (
    CashCoDocumentType,
    DocumentLegalPolicy,
    DocumentLegalReadiness,
    DocumentLegalStatus,
)
from src.restaurants.registry_models import RegisteredRestaurant
from src.settlement.certified_calculator import CertifiedFinancialCalculator
from src.settlement.legacy_validation import (
    FinancialFormulaCertification,
    LegacyCalculationPolicy,
    LegacyFormulaRegistry,
)
from src.settlement.phase5_models import (
    RestaurantSettlementEvaluation,
    RestaurantSettlementStatus,
)


class DocumentReadinessStatus(StrEnum):
    READY = "READY"
    MISSING_LEGAL = "MISSING_LEGAL"
    FINANCIAL_REVIEW = "FINANCIAL_REVIEW"
    FORMULA_NOT_VALIDATED = "FORMULA_NOT_VALIDATED"
    BLOCKED = "BLOCKED"
    GENERATED = "GENERATED"


class DocumentReadiness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    restaurant_id: str
    period_code: str
    status: DocumentReadinessStatus
    identity_ready: bool
    settlement_final: bool
    legal_ready: bool
    legal_status: DocumentLegalStatus
    financial_formulas_validated: bool
    missing_legal_fields: tuple[str, ...] = ()
    issue_codes: tuple[str, ...] = ()
    potentially_eligible: bool = False
    legal_readiness: tuple[DocumentLegalReadiness, ...] = ()


class DocumentPreview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    restaurant_id: str
    period_code: str
    document_type: CashCoDocumentType
    version: int = Field(ge=1)
    document_key: str
    watermark: str
    generated_at: datetime
    readiness: DocumentReadiness
    content: dict[str, str | None]
    financial_policy_version: str | None = None


class ProductionDocumentStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    PRODUCTION_READY = "PRODUCTION_READY"


class ProductionDocumentCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    restaurant_id: str
    period_code: str
    document_type: CashCoDocumentType
    document_version: int = Field(ge=1)
    document_reference: str
    financial_policy_version: str
    settlement_snapshot_hash: str
    financial_snapshot_hash: str
    legal_snapshot_hash: str
    content_hash: str
    document_hash: str
    legal_status: DocumentLegalStatus
    status: ProductionDocumentStatus
    validation_issues: tuple[str, ...] = ()
    content: dict[str, str | None]


class RenderedDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    restaurant_id: str
    period_code: str
    document_type: CashCoDocumentType
    document_version: int
    filename: str
    mime_type: str = "text/html"
    content: bytes
    document_hash: str


class Phase8DocumentEngine:
    DRIVE_PUBLISHING_STATUS = "DRIVE_PUBLISHING_NOT_CONFIGURED"
    FINANCIAL_FIELDS: ClassVar[tuple[str, ...]] = (
        "sales_ttc",
        "sales_ht",
        "commission_amount",
        "invoice_tva",
        "invoice_ttc",
        "note_de_debours",
        "final_net_payable",
    )

    def __init__(
        self,
        formulas: LegacyFormulaRegistry | None = None,
        *,
        certification: FinancialFormulaCertification | None = None,
        policy: LegacyCalculationPolicy | None = None,
    ) -> None:
        self.formulas = formulas or LegacyFormulaRegistry()
        self.certification = certification or self.formulas.certification()
        self.policy = policy or self.formulas.active_policy()
        self.legal_policy = DocumentLegalPolicy()

    def financial_formulas_ready(self) -> bool:
        return bool(
            self.certification
            and self.certification.production_ready
            and self.policy is not None
        )

    def readiness(
        self,
        restaurant: RegisteredRestaurant,
        settlement: RestaurantSettlementEvaluation,
        document_type: CashCoDocumentType | None = None,
    ) -> DocumentReadiness:
        legal_results = (
            (self.legal_policy.evaluate(restaurant, document_type),)
            if document_type is not None
            else self.legal_policy.evaluate_package(restaurant)
        )
        missing_legal = tuple(
            dict.fromkeys(
                field
                for result in legal_results
                for field in result.missing_required_fields
            )
        )
        optional_missing = tuple(
            dict.fromkeys(
                field
                for result in legal_results
                for field in result.optional_missing_fields
            )
        )
        invalid_legal = tuple(
            dict.fromkeys(
                field for result in legal_results for field in result.invalid_fields
            )
        )
        if any(item.status == DocumentLegalStatus.BLOCKED for item in legal_results):
            legal_status = DocumentLegalStatus.BLOCKED
        elif any(
            item.status == DocumentLegalStatus.READY_WITH_WARNINGS
            for item in legal_results
        ):
            legal_status = DocumentLegalStatus.READY_WITH_WARNINGS
        else:
            legal_status = DocumentLegalStatus.READY
        legal_ready = legal_status != DocumentLegalStatus.BLOCKED
        formula_ready = self.financial_formulas_ready()
        financial_review = (
            settlement.manual_review_orders > 0
            or settlement.settlement_status
            in {
                RestaurantSettlementStatus.REVIEW_REQUIRED,
                RestaurantSettlementStatus.BLOCKED_COMMISSION,
                RestaurantSettlementStatus.BLOCKED_DATA,
            }
        )
        valid_commission = (
            settlement.commission_resolution.effective_commission is not None
        )
        potentially_eligible = bool(
            settlement.total_orders
            and restaurant.readiness.identity_ready
            and not financial_review
            and valid_commission
            and legal_ready
        )
        issues: list[str] = []
        if not restaurant.readiness.identity_ready:
            issues.append("IDENTITY_BLOCKED")
        if financial_review:
            issues.append("FINANCIAL_REVIEW_REQUIRED")
        if not valid_commission:
            issues.append("COMMISSION_NOT_RESOLVED")
        issues.extend(f"MISSING_REQUIRED_{item.upper()}" for item in missing_legal)
        issues.extend(f"OPTIONAL_MISSING_{item.upper()}" for item in optional_missing)
        issues.extend(f"INVALID_{item.upper()}" for item in invalid_legal)
        if not formula_ready:
            issues.append("LEGACY_FORMULA_VALIDATION_REQUIRED")
        if not restaurant.readiness.identity_ready:
            status = DocumentReadinessStatus.BLOCKED
        elif financial_review or not valid_commission:
            status = DocumentReadinessStatus.FINANCIAL_REVIEW
        elif not legal_ready:
            status = DocumentReadinessStatus.MISSING_LEGAL
        elif not formula_ready:
            status = DocumentReadinessStatus.FORMULA_NOT_VALIDATED
        elif settlement.total_orders == 0:
            status = DocumentReadinessStatus.BLOCKED
        else:
            status = DocumentReadinessStatus.READY
        return DocumentReadiness(
            restaurant_id=settlement.restaurant_id,
            period_code=settlement.period_code,
            status=status,
            identity_ready=restaurant.readiness.identity_ready,
            settlement_final=not financial_review,
            legal_ready=legal_ready,
            legal_status=legal_status,
            financial_formulas_validated=formula_ready,
            missing_legal_fields=missing_legal,
            issue_codes=tuple(issues),
            potentially_eligible=potentially_eligible,
            legal_readiness=legal_results,
        )

    def preview(
        self,
        document_type: CashCoDocumentType,
        restaurant: RegisteredRestaurant,
        settlement: RestaurantSettlementEvaluation,
        *,
        version: int = 1,
        generated_at: datetime | None = None,
    ) -> DocumentPreview:
        readiness = self.readiness(restaurant, settlement, document_type)
        legal = readiness.legal_readiness[0]
        financial_content = self._financial_content(settlement)
        watermark = (
            "DRAFT · NOT VALIDATED"
            if readiness.status != DocumentReadinessStatus.READY
            else "DRAFT"
        )
        return DocumentPreview(
            restaurant_id=settlement.restaurant_id,
            period_code=settlement.period_code,
            document_type=document_type,
            version=version,
            document_key=(
                f"{settlement.restaurant_id}:{settlement.period_code}:"
                f"{document_type.value}:v{version}"
            ),
            watermark=watermark,
            generated_at=generated_at or datetime.now(UTC),
            readiness=readiness,
            financial_policy_version=(
                self.policy.policy_version
                if self.financial_formulas_ready() and self.policy
                else None
            ),
            content={
                "partner": legal.document_partner_name,
                "partner_name_source": (
                    legal.document_partner_name_source.value
                    if legal.document_partner_name_source
                    else None
                ),
                "restaurant_id": settlement.restaurant_id,
                "period": settlement.period_code,
                "legal_entity": restaurant.legal_entity,
                "ice": restaurant.ice,
                "if": restaurant.if_number,
                "rc": restaurant.rc,
                "address": restaurant.address,
                "commission": (
                    str(settlement.commission_resolution.effective_commission)
                    if settlement.commission_resolution.effective_commission is not None
                    else None
                ),
                "gross_order_value": str(settlement.gross_order_value),
                **financial_content,
            },
        )

    def production_candidate(
        self,
        document_type: CashCoDocumentType,
        restaurant: RegisteredRestaurant,
        settlement: RestaurantSettlementEvaluation,
        *,
        version: int = 1,
        generated_at: datetime | None = None,
    ) -> ProductionDocumentCandidate:
        preview = self.preview(
            document_type,
            restaurant,
            settlement,
            version=version,
            generated_at=generated_at,
        )
        issues: list[str] = []
        if preview.readiness.status != DocumentReadinessStatus.READY:
            issues.extend(preview.readiness.issue_codes)
        for field in self.FINANCIAL_FIELDS:
            if preview.content.get(field) is None:
                issues.append(f"MISSING_FINANCIAL_FIELD:{field}")
        if not preview.content.get("partner"):
            issues.append("MISSING_DOCUMENT_PARTNER_NAME")
        if restaurant.restaurant_id != settlement.restaurant_id:
            issues.append("RESTAURANT_ID_MISMATCH")
        if not settlement.period_code:
            issues.append("MISSING_SETTLEMENT_PERIOD")
        if settlement.commission_resolution.effective_commission is None:
            issues.append("MISSING_COMMISSION_RATE")
        if preview.financial_policy_version != "cashco_legacy_v1":
            issues.append("INVALID_FINANCIAL_POLICY_VERSION")
        if (
            settlement.invoice_ttc is not None
            and settlement.commission_amount is not None
            and settlement.invoice_tva is not None
            and settlement.invoice_ttc
            != settlement.commission_amount + settlement.invoice_tva
        ):
            issues.append("INVOICE_TTC_RECONCILIATION_FAILED")
        if (
            settlement.sales_ttc is not None
            and settlement.net_payable is not None
            and settlement.invoice_ttc is not None
            and settlement.sales_ttc
            != settlement.net_payable + settlement.invoice_ttc
        ):
            issues.append("NET_PAYABLE_RECONCILIATION_FAILED")
        snapshot_hash = self._stable_hash(settlement.model_dump(mode="json"))
        financial_snapshot_hash = self._stable_hash(
            {
                "financial_policy_version": preview.financial_policy_version,
                "commission": preview.content.get("commission"),
                **{
                    field: preview.content.get(field)
                    for field in self.FINANCIAL_FIELDS
                },
            }
        )
        legal_snapshot_hash = self._stable_hash(
            {
                field: preview.content.get(field)
                for field in (
                    "partner",
                    "partner_name_source",
                    "restaurant_id",
                    "legal_entity",
                    "ice",
                    "if",
                    "rc",
                    "address",
                )
            }
        )
        content_hash = self._stable_hash(
            {
                "document_reference": preview.document_key,
                "financial_policy_version": preview.financial_policy_version,
                "settlement_snapshot_hash": snapshot_hash,
                "content": preview.content,
            }
        )
        candidate = ProductionDocumentCandidate(
            restaurant_id=settlement.restaurant_id,
            period_code=settlement.period_code,
            document_type=document_type,
            document_version=version,
            document_reference=preview.document_key,
            financial_policy_version=preview.financial_policy_version or "NOT_VALIDATED",
            settlement_snapshot_hash=snapshot_hash,
            financial_snapshot_hash=financial_snapshot_hash,
            legal_snapshot_hash=legal_snapshot_hash,
            content_hash=content_hash,
            document_hash="PENDING_RENDER",
            legal_status=preview.readiness.legal_status,
            status=(
                ProductionDocumentStatus.PRODUCTION_READY
                if not issues
                else ProductionDocumentStatus.DRAFT
            ),
            validation_issues=tuple(dict.fromkeys(issues)),
            content=preview.content,
        )
        rendered = self.render_production_document(candidate)
        return candidate.model_copy(update={"document_hash": rendered.document_hash})

    def _financial_content(
        self, settlement: RestaurantSettlementEvaluation
    ) -> dict[str, str | None]:
        empty = {
            "sales_ttc": None,
            "sales_ht": None,
            "commission_amount": None,
            "invoice_ht": None,
            "invoice_tva": None,
            "invoice_ttc": None,
            "note_de_debours": None,
            "final_net_payable": None,
        }
        if (
            not self.financial_formulas_ready()
            or self.policy is None
            or self.certification is None
            or settlement.commission_resolution.effective_commission is None
        ):
            return empty
        calculated = CertifiedFinancialCalculator().calculate(
            settlement,
            certification=self.certification,
            policy=self.policy,
        )
        def present(value: object, field: str) -> str | None:
            if value is None:
                return None
            rounded = self.policy.rounding_policy(value, field)
            return f"{rounded:.2f}"

        return {
            "sales_ttc": present(calculated.sales_ttc, "sales_ttc"),
            "sales_ht": present(calculated.sales_ht, "sales_ht"),
            "commission_amount": present(
                calculated.commission_amount, "commission_amount"
            ),
            "invoice_ht": present(calculated.invoice_ht, "invoice_ht"),
            "invoice_tva": present(calculated.invoice_tva, "invoice_tva"),
            "invoice_ttc": present(calculated.invoice_ttc, "invoice_ttc"),
            "note_de_debours": present(
                calculated.disbursement_note, "note_de_debours"
            ),
            "final_net_payable": present(
                calculated.net_payable, "final_net_payable"
            ),
        }

    @staticmethod
    def render_local_preview(preview: DocumentPreview) -> bytes:
        return json.dumps(
            preview.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode()

    @classmethod
    def render_production_document(
        cls, candidate: ProductionDocumentCandidate
    ) -> RenderedDocument:
        """Render deterministic, self-contained HTML; calculations stay upstream."""
        content = candidate.content
        labels = {
            CashCoDocumentType.INVOICE: "FACTURE COMMISSION",
            CashCoDocumentType.NOTE_DE_DEBOURS: "NOTE DE DÉBOURS",
            CashCoDocumentType.PARTNER_STATEMENT: "PARTNER STATEMENT",
        }
        rows_by_type = {
            CashCoDocumentType.INVOICE: (
                ("Base de calcul TTC", "sales_ttc"),
                ("Commission HT", "invoice_ht"),
                ("TVA 20%", "invoice_tva"),
                ("Total Facture TTC", "invoice_ttc"),
            ),
            CashCoDocumentType.NOTE_DE_DEBOURS: (
                ("Total du panier TTC", "sales_ttc"),
                ("Déduction Yassir TTC", "invoice_ttc"),
                ("Total à payer TTC", "final_net_payable"),
            ),
            CashCoDocumentType.PARTNER_STATEMENT: (
                ("Sales TTC", "sales_ttc"),
                ("Sales HT", "sales_ht"),
                ("Commission HT", "commission_amount"),
                ("TVA 20%", "invoice_tva"),
                ("Invoice TTC", "invoice_ttc"),
                ("Net payable", "final_net_payable"),
            ),
        }

        def safe(value: object) -> str:
            return html.escape(str(value or "—"), quote=True)

        financial_rows = "".join(
            f"<tr><th>{safe(label)}</th><td>{safe(content.get(field))} MAD</td></tr>"
            for label, field in rows_by_type[candidate.document_type]
        )
        optional_legal = "".join(
            f"<span><strong>{safe(label)}:</strong> {safe(content.get(field))}</span>"
            for label, field in (("ICE", "ice"), ("IF", "if"), ("RC", "rc"))
            if content.get(field)
        )
        watermark = (
            ""
            if candidate.status == ProductionDocumentStatus.PRODUCTION_READY
            else '<div class="watermark">DRAFT · NOT VALIDATED</div>'
        )
        markup = f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>{safe(labels[candidate.document_type])}</title>
<style>body{{font:14px Arial,sans-serif;color:#17213b;margin:48px}}header{{border-bottom:3px solid #6941c6;padding-bottom:18px}}h1{{font-size:24px}}.meta{{color:#667085}}.watermark{{padding:10px;margin-bottom:20px;background:#fff0ef;color:#b42318;font-weight:700;text-align:center}}.legal{{display:grid;gap:6px;margin:28px 0}}table{{width:100%;border-collapse:collapse;margin-top:24px}}th,td{{padding:12px;border-bottom:1px solid #e4e7ec;text-align:left}}td{{text-align:right;font-variant-numeric:tabular-nums}}footer{{margin-top:42px;color:#667085;font-size:11px}}</style></head>
<body>{watermark}<header><div class="meta">Yassir CashCo · {safe(candidate.period_code)}</div><h1>{safe(labels[candidate.document_type])}</h1><div>Référence: {safe(candidate.document_reference)}</div></header>
<section class="legal"><strong>{safe(content.get('partner'))}</strong><span>Restaurant ID: {safe(candidate.restaurant_id)}</span><span>Adresse: {safe(content.get('address'))}</span>{optional_legal}</section>
<table>{financial_rows}</table>
<footer>Version {candidate.document_version} · Politique financière {safe(candidate.financial_policy_version)} · Document validé par les contrôles CashCo</footer></body></html>"""
        payload = markup.encode("utf-8")
        suffix = {
            CashCoDocumentType.INVOICE: "facture_commission",
            CashCoDocumentType.NOTE_DE_DEBOURS: "note_de_debours",
            CashCoDocumentType.PARTNER_STATEMENT: "partner_statement",
        }[candidate.document_type]
        return RenderedDocument(
            restaurant_id=candidate.restaurant_id,
            period_code=candidate.period_code,
            document_type=candidate.document_type,
            document_version=candidate.document_version,
            filename=(
                f"{candidate.period_code}_{candidate.restaurant_id}_{suffix}_"
                f"v{candidate.document_version}.html"
            ),
            content=payload,
            document_hash=hashlib.sha256(payload).hexdigest(),
        )

    @staticmethod
    def _stable_hash(value: object) -> str:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(payload).hexdigest()
