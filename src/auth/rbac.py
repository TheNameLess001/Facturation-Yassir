from enum import StrEnum

from src.auth.service import User
from src.models.enums import Role


class Permission(StrEnum):
    VIEW = "VIEW"
    REVIEW_SETTLEMENT = "REVIEW_SETTLEMENT"
    ADJUST_ORDER = "ADJUST_ORDER"
    GENERATE_DOCUMENTS = "GENERATE_DOCUMENTS"
    PREVIEW_EMAIL = "PREVIEW_EMAIL"
    AUTHORIZE_AUTOMATION = "AUTHORIZE_AUTOMATION"
    SEND_EMAIL = "SEND_EMAIL"
    LOCK_PERIOD = "LOCK_PERIOD"
    CONFIGURE = "CONFIGURE"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset({Permission.VIEW}),
    Role.FINANCE: frozenset(
        {
            Permission.VIEW,
            Permission.REVIEW_SETTLEMENT,
            Permission.ADJUST_ORDER,
            Permission.GENERATE_DOCUMENTS,
            Permission.PREVIEW_EMAIL,
        }
    ),
    Role.ADMIN: frozenset(Permission),
}


class RBACService:
    def can(self, user: User, permission: Permission) -> bool:
        return permission in ROLE_PERMISSIONS[user.role]

    def require(self, user: User, permission: Permission) -> None:
        if not self.can(user, permission):
            raise PermissionError(f"{user.role} cannot perform {permission}")
