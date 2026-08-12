from dataclasses import dataclass
from typing import Protocol

from src.config import Settings
from src.models.enums import Role


@dataclass(frozen=True)
class User:
    user_id: str
    name: str
    email: str
    role: Role


class IdentityProvider(Protocol):
    def current_user(self) -> User: ...


class AuthService:
    """Replaceable authentication boundary; Phase 1 uses configured demo identity."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def current_user(self) -> User:
        return User(
            user_id=self.settings.default_user_id,
            name=self.settings.default_user_name,
            email=self.settings.default_user_email,
            role=self.settings.default_user_role,
        )
