from __future__ import annotations

from src.settlement.legacy_validation import (
    FinancialFormulaCertification,
    LegacyCalculationPolicy,
)
from src.settlement.phase5_models import RestaurantSettlementEvaluation


class CertifiedFinancialCalculator:
    """Executes only a separately certified, versioned formula policy."""

    def calculate(
        self,
        settlement: RestaurantSettlementEvaluation,
        *,
        certification: FinancialFormulaCertification,
        policy: LegacyCalculationPolicy,
    ) -> RestaurantSettlementEvaluation:
        if not certification.production_ready:
            raise PermissionError("FINANCIAL_FORMULA_CERTIFICATION_REQUIRED")
        if certification.policy_version != policy.policy_version:
            raise ValueError("Certified policy version does not match implementation")
        rate = settlement.commission_resolution.effective_commission
        if rate is None:
            raise ValueError("A valid Invoice Scope commission is required")
        base = policy.commission_base(settlement.model_dump(mode="python"))
        commission = policy.commission_amount(base, rate)
        values = {
            "gross_order_value": settlement.gross_order_value,
            "eligible_partner_amount": settlement.eligible_partner_amount,
            "excluded_amount": settlement.excluded_amount,
            "compensation_amount": settlement.compensation_amount,
            "commission_base": base,
            "commission_amount": commission,
        }
        ht = policy.invoice_ht(values)
        tva = policy.invoice_tva(ht)
        ttc = policy.invoice_ttc(ht, tva)
        debours = policy.note_de_debours(values)
        values.update(
            {
                "invoice_ht": ht,
                "invoice_tva": tva,
                "invoice_ttc": ttc,
                "note_de_debours": debours,
            }
        )
        net = policy.final_net_payable(values)
        return settlement.model_copy(
            update={
                "commission_base": base,
                "commission_amount": commission,
                "invoice_ht": ht,
                "invoice_tva": tva,
                "invoice_ttc": ttc,
                "disbursement_note": debours,
                "net_payable": net,
                "financial_policy_version": policy.policy_version,
            }
        )
