from src.models.enums import WorkflowState

TRANSITIONS: dict[WorkflowState, frozenset[WorkflowState]] = {
    WorkflowState.DRAFT: frozenset({WorkflowState.DATA_READY, WorkflowState.BLOCKED}),
    WorkflowState.DATA_READY: frozenset(
        {WorkflowState.TO_REVIEW, WorkflowState.VALIDATED, WorkflowState.BLOCKED}
    ),
    WorkflowState.TO_REVIEW: frozenset(
        {WorkflowState.VALIDATED, WorkflowState.BLOCKED}
    ),
    WorkflowState.VALIDATED: frozenset(
        {WorkflowState.DOCUMENTS_GENERATED, WorkflowState.TO_REVIEW}
    ),
    WorkflowState.DOCUMENTS_GENERATED: frozenset(
        {WorkflowState.EMAIL_READY, WorkflowState.AUTHORIZATION_STALE}
    ),
    WorkflowState.EMAIL_READY: frozenset(
        {WorkflowState.AUTHORIZED, WorkflowState.AUTHORIZATION_STALE}
    ),
    WorkflowState.AUTHORIZED: frozenset(
        {WorkflowState.SENDING, WorkflowState.AUTHORIZATION_STALE}
    ),
    WorkflowState.SENDING: frozenset({WorkflowState.SENT, WorkflowState.EMAIL_READY}),
    WorkflowState.SENT: frozenset({WorkflowState.PAID}),
    WorkflowState.PAID: frozenset({WorkflowState.LOCKED}),
    WorkflowState.LOCKED: frozenset(),
    WorkflowState.BLOCKED: frozenset(
        {WorkflowState.DATA_READY, WorkflowState.TO_REVIEW}
    ),
    WorkflowState.AUTHORIZATION_STALE: frozenset(
        {WorkflowState.TO_REVIEW, WorkflowState.VALIDATED}
    ),
}


class WorkflowService:
    def can_transition(self, current: WorkflowState, target: WorkflowState) -> bool:
        return target in TRANSITIONS[current]

    def transition(
        self, current: WorkflowState, target: WorkflowState
    ) -> WorkflowState:
        if not self.can_transition(current, target):
            raise ValueError(f"Invalid workflow transition: {current} -> {target}")
        return target
