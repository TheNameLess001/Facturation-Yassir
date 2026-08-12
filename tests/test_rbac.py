import pytest

from src.auth import Permission, RBACService, User
from src.models.enums import Role


def user(role: Role) -> User:
    return User("user-1", "Test User", "test@example.com", role)


def test_admin_has_automation_permissions() -> None:
    rbac = RBACService()
    assert rbac.can(user(Role.ADMIN), Permission.AUTHORIZE_AUTOMATION)
    assert rbac.can(user(Role.ADMIN), Permission.SEND_EMAIL)
    assert rbac.can(user(Role.ADMIN), Permission.LOCK_PERIOD)


@pytest.mark.parametrize("role", [Role.FINANCE, Role.VIEWER])
def test_non_admin_cannot_authorize_or_send(role: Role) -> None:
    rbac = RBACService()
    assert not rbac.can(user(role), Permission.AUTHORIZE_AUTOMATION)
    assert not rbac.can(user(role), Permission.SEND_EMAIL)


def test_finance_can_adjust_but_viewer_cannot() -> None:
    rbac = RBACService()
    assert rbac.can(user(Role.FINANCE), Permission.ADJUST_ORDER)
    assert not rbac.can(user(Role.VIEWER), Permission.ADJUST_ORDER)


def test_require_raises_on_forbidden_action() -> None:
    with pytest.raises(PermissionError):
        RBACService().require(user(Role.FINANCE), Permission.SEND_EMAIL)
