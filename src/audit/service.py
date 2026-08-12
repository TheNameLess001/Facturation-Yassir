from typing import Protocol

from src.models.domain import AuditEvent


class AuditRepository(Protocol):
    def append(self, event: AuditEvent) -> None: ...
    def list_events(self) -> tuple[AuditEvent, ...]: ...


class InMemoryAuditRepository:
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        self._events.append(event)

    def list_events(self) -> tuple[AuditEvent, ...]:
        return tuple(self._events)


class AuditService:
    def __init__(self, repository: AuditRepository) -> None:
        self.repository = repository

    def record(self, event: AuditEvent) -> AuditEvent:
        self.repository.append(event)
        return event
