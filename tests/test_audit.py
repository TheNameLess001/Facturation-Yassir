from src.audit import AuditService, InMemoryAuditRepository
from src.models.domain import AuditEvent


def test_audit_creation_is_append_only() -> None:
    repository = InMemoryAuditRepository()
    service = AuditService(repository)
    event = AuditEvent(
        event_type="PERIOD_CREATED",
        actor_id="admin-1",
        period_id="2026-08-P1",
        entity_type="SETTLEMENT_PERIOD",
        entity_id="2026-08-P1",
    )
    assert service.record(event) == event
    assert repository.list_events() == (event,)


def test_audit_details_do_not_share_mutable_default() -> None:
    base = {
        "event_type": "TEST",
        "actor_id": "system",
        "entity_type": "TEST",
        "entity_id": "1",
    }
    first = AuditEvent(**base)
    second = AuditEvent(**base)
    assert first.details == second.details == {}
    assert first.details is not second.details
