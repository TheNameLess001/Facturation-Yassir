import pytest

from src.models.enums import WorkflowState
from src.settlement import WorkflowService


def test_valid_workflow_transition() -> None:
    assert (
        WorkflowService().transition(WorkflowState.DRAFT, WorkflowState.DATA_READY)
        == WorkflowState.DATA_READY
    )


def test_draft_cannot_jump_to_sent() -> None:
    with pytest.raises(ValueError, match="Invalid workflow transition"):
        WorkflowService().transition(WorkflowState.DRAFT, WorkflowState.SENT)


def test_locked_is_terminal() -> None:
    workflow = WorkflowService()
    assert not workflow.can_transition(WorkflowState.LOCKED, WorkflowState.DRAFT)


def test_financial_change_can_make_authorization_stale() -> None:
    assert WorkflowService().can_transition(
        WorkflowState.AUTHORIZED, WorkflowState.AUTHORIZATION_STALE
    )
