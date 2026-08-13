from __future__ import annotations

from dataclasses import dataclass

from src.emails.phase10_models import (
    ProductionReadinessResult,
    ReadinessBlocker,
    ReadinessState,
    RecipientStatus,
)


@dataclass(frozen=True)
class ProductionReadinessInput:
    identity_ready: bool
    settlement_ready: bool
    manual_review_clear: bool
    commission_valid: bool
    financial_data_valid: bool
    legacy_formula_validated: bool
    legal_data_ready: bool
    document_ready: bool
    email_status: RecipientStatus
    admin_authorized: bool = False
    authorization_current: bool = False
    already_sent: bool = False
    period_locked: bool = False
    production_send_enabled: bool = False


class ProductionReadinessPolicy:
    def evaluate(self, value: ProductionReadinessInput) -> ProductionReadinessResult:
        blockers: list[ReadinessBlocker] = []
        checks = (
            (value.identity_ready, ReadinessBlocker.IDENTITY_BLOCKED),
            (value.settlement_ready, ReadinessBlocker.SETTLEMENT_NOT_READY),
            (value.manual_review_clear, ReadinessBlocker.MANUAL_REVIEW_PENDING),
            (value.commission_valid, ReadinessBlocker.COMMISSION_BLOCKED),
            (value.financial_data_valid, ReadinessBlocker.INVALID_FINANCIAL_DATA),
            (
                value.legacy_formula_validated,
                ReadinessBlocker.LEGACY_FORMULA_NOT_VALIDATED,
            ),
            (value.legal_data_ready, ReadinessBlocker.MISSING_LEGAL_DATA),
            (value.document_ready, ReadinessBlocker.DOCUMENT_NOT_READY),
        )
        blockers.extend(blocker for valid, blocker in checks if not valid)
        if value.email_status == RecipientStatus.EMAIL_MISSING:
            blockers.append(ReadinessBlocker.EMAIL_MISSING)
        elif value.email_status == RecipientStatus.EMAIL_INVALID:
            blockers.append(ReadinessBlocker.EMAIL_INVALID)
        if not value.admin_authorized:
            blockers.append(ReadinessBlocker.ADMIN_NOT_AUTHORIZED)
        elif not value.authorization_current:
            blockers.append(ReadinessBlocker.AUTHORIZATION_STALE)
        if value.already_sent:
            blockers.append(ReadinessBlocker.ALREADY_SENT)
        if value.period_locked:
            blockers.append(ReadinessBlocker.PERIOD_LOCKED)
        if not value.production_send_enabled:
            blockers.append(ReadinessBlocker.PRODUCTION_SEND_DISABLED)

        preview_blockers = {
            ReadinessBlocker.IDENTITY_BLOCKED,
            ReadinessBlocker.EMAIL_MISSING,
            ReadinessBlocker.EMAIL_INVALID,
        }
        draft_blockers = {
            ReadinessBlocker.IDENTITY_BLOCKED,
            ReadinessBlocker.DOCUMENT_NOT_READY,
            ReadinessBlocker.EMAIL_MISSING,
            ReadinessBlocker.EMAIL_INVALID,
            ReadinessBlocker.PERIOD_LOCKED,
        }
        ready_preview = not preview_blockers.intersection(blockers)
        ready_draft = not draft_blockers.intersection(blockers)
        ready_send = not blockers
        if ready_send:
            state = ReadinessState.READY_FOR_SEND
        elif ready_draft:
            state = ReadinessState.READY_FOR_DRAFT
        elif ready_preview:
            state = ReadinessState.READY_FOR_PREVIEW
        else:
            state = ReadinessState.BLOCKED
        return ProductionReadinessResult(
            state=state,
            blockers=tuple(dict.fromkeys(blockers)),
            ready_for_preview=ready_preview,
            ready_for_draft=ready_draft,
            ready_for_send=ready_send,
        )
