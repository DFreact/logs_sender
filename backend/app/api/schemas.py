from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import UserRole
from app.security.permissions import Permission


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginInput(InputModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128, repr=False)

    @field_validator("username", mode="before")
    @classmethod
    def normalize(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class UserCreate(InputModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=12, max_length=128, repr=False)
    role: UserRole = UserRole.VIEWER

    @field_validator("username", "display_name", mode="before")
    @classmethod
    def trim(cls, value, info):
        if not isinstance(value, str):
            return value
        return value.strip().lower() if info.field_name == "username" else value.strip()


class UserUpdate(InputModel):
    display_name: str = Field(min_length=1, max_length=120)
    role: UserRole
    active: bool = Field(strict=True)
    version: int = Field(ge=1, strict=True)
    password: str | None = Field(default=None, min_length=12, max_length=128, repr=False)

    @field_validator("display_name", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


class PasswordChange(InputModel):
    current_password: str = Field(min_length=1, max_length=128, repr=False)
    new_password: str = Field(min_length=12, max_length=128, repr=False)


class UserView(BaseModel):
    id: UUID
    username: str
    display_name: str
    role: UserRole
    active: bool
    version: int
    created_at: datetime


class MeView(BaseModel):
    user: UserView
    permissions: list[Permission]
    csrf_token: str
    expires_at: datetime
