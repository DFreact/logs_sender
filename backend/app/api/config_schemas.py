from uuid import UUID

from pydantic import Field, field_validator

from app.api.schemas import InputModel
from app.domain.enums import Severity
from app.domain.rules.conditions import safe_text, validate

DEDUP_FIELDS = ("sender", "subject", "body", "event_type", "category", "severity", "tags")
DEFAULT_FIELDS = ["sender", "subject", "body", "event_type"]


class PolicyInput(InputModel):
    enabled: bool = Field(default=False, strict=True)
    inherit: bool = Field(default=False, strict=True)
    window_seconds: int = Field(default=600, ge=1, le=86400, strict=True)
    fields: list[str] = Field(
        default_factory=lambda: list(DEFAULT_FIELDS), min_length=1, max_length=7
    )
    version: int = Field(default=1, ge=1, strict=True)

    @field_validator("fields")
    @classmethod
    def fields_valid(cls, value):
        if len(set(value)) != len(value) or any(item not in DEDUP_FIELDS for item in value):
            raise ValueError("INVALID_FIELDS")
        return value


class SourceInput(InputModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    enabled: bool = Field(default=True, strict=True)
    version: int = Field(default=1, ge=1, strict=True)
    dedup: PolicyInput = Field(default_factory=lambda: PolicyInput(inherit=True))

    @field_validator("name", "description")
    @classmethod
    def text_valid(cls, value):
        if not safe_text(value, 2000):
            raise ValueError("INVALID_TEXT")
        return value.strip()

    @field_validator("name")
    @classmethod
    def nonempty(cls, value):
        if not value:
            raise ValueError("NAME_REQUIRED")
        return value


class Assignments(InputModel):
    severity: Severity | None = None
    category: str = Field(default="", max_length=120)
    event_type: str = Field(default="", max_length=120)
    tags: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("category", "event_type")
    @classmethod
    def text_valid(cls, value):
        if not safe_text(value, 120):
            raise ValueError("INVALID_TEXT")
        return value

    @field_validator("tags")
    @classmethod
    def tags_valid(cls, value):
        if any(not safe_text(item, 64) or not item.strip() for item in value):
            raise ValueError("INVALID_TAGS")
        return sorted(set(item.strip() for item in value))


class RuleInput(InputModel):
    name: str = Field(min_length=1, max_length=120)
    source_id: UUID
    priority: int = Field(default=100, ge=0, le=100000, strict=True)
    enabled: bool = Field(default=True, strict=True)
    version: int = Field(default=1, ge=1, strict=True)
    conditions: dict
    assignments: Assignments = Field(default_factory=Assignments)

    @field_validator("name")
    @classmethod
    def name_valid(cls, value):
        if not safe_text(value, 120) or not value.strip():
            raise ValueError("INVALID_NAME")
        return value.strip()

    @field_validator("conditions")
    @classmethod
    def condition_valid(cls, value):
        return validate(value)
