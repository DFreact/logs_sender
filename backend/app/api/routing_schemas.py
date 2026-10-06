from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.api.config_schemas import Assignments, RuleInput, SourceInput
from app.api.schemas import InputModel
from app.domain.enums import Severity
from app.domain.rules.conditions import ROUTING_FIELDS, finite_number, safe_text, validate


class RoutingAssignments(Assignments):
    category: str | None = Field(default=None, max_length=120)
    event_type: str | None = Field(default=None, max_length=120)

    @field_validator("category", "event_type")
    @classmethod
    def text_valid(cls, value):
        if value is not None and not safe_text(value, 120):
            raise ValueError("INVALID_TEXT")
        return value

    @model_validator(mode="after")
    def not_empty(self):
        if (
            self.severity is None
            and self.category is None
            and self.event_type is None
            and not self.tags
        ):
            raise ValueError("ASSIGNMENT_REQUIRED")
        return self


class ActionBase(InputModel):
    scope: Literal["EVENT", "OCCURRENCE"] = "EVENT"


class SetFields(ActionBase):
    kind: Literal["SET_FIELDS"]
    fields: RoutingAssignments


class Suppress(ActionBase):
    kind: Literal["SUPPRESS"]


class Notification(ActionBase):
    kind: Literal["NOTIFY"]
    target_id: UUID | None = None
    mode: Literal["IMMEDIATE", "DELAYED", "SCHEDULED"] = "IMMEDIATE"
    delay_seconds: int | None = Field(default=None, strict=True, ge=1, le=2592000)
    scheduled_at: datetime | None = None

    @model_validator(mode="after")
    def schedule(self):
        if (self.mode == "DELAYED") != (self.delay_seconds is not None):
            raise ValueError("INVALID_DELAY")
        if (self.mode == "SCHEDULED") != (self.scheduled_at is not None):
            raise ValueError("INVALID_SCHEDULE")
        if self.scheduled_at is not None and self.scheduled_at.tzinfo is None:
            raise ValueError("TIMEZONE_REQUIRED")
        return self


class Digest(ActionBase):
    kind: Literal["DIGEST"]
    target_id: UUID | None = None


class Escalation(ActionBase):
    kind: Literal["ESCALATE"]
    target_id: UUID | None = None


Action = Annotated[
    SetFields | Suppress | Notification | Digest | Escalation, Field(discriminator="kind")
]


class RoutingInput(InputModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    priority: int = Field(default=100, ge=0, le=100000, strict=True)
    enabled: bool = Field(default=True, strict=True)
    version: int = Field(default=1, ge=1, strict=True)
    conditions: dict
    actions: list[Action] = Field(min_length=1, max_length=10)

    name_valid = field_validator("name")(RuleInput.name_valid.__func__)
    description_valid = field_validator("description")(SourceInput.text_valid.__func__)

    @field_validator("conditions")
    @classmethod
    def condition_valid(cls, value):
        validate(value, fields=ROUTING_FIELDS)
        pending = [value]
        while pending:
            node = pending.pop()
            if "group" in node:
                pending.extend(node["children"])
            elif node["field"] in ("source", "severity"):
                if node["operator"] not in ("equals", "not_equals", "exists"):
                    raise ValueError("INVALID_TYPED_OPERATOR")
                if node["operator"] != "exists":
                    if node["field"] == "source":
                        node["value"] = str(UUID(node["value"]))
                    elif node["value"] not in Severity:
                        raise ValueError("INVALID_SEVERITY")
        return value

    @field_validator("actions")
    @classmethod
    def assignments_once(cls, value):
        if sum(action.kind == "SET_FIELDS" for action in value) > 1:
            raise ValueError("DUPLICATE_ASSIGNMENT")
        return value


class TestPair(InputModel):
    name: str = Field(min_length=1, max_length=120)
    value: str = Field(max_length=1000)
    name_valid = field_validator("name")(RuleInput.name_valid.__func__)


class TestMetadata(InputModel):
    name: str = Field(min_length=1, max_length=120)
    value: str | int | float | bool | None
    name_valid = field_validator("name")(RuleInput.name_valid.__func__)

    @field_validator("value")
    @classmethod
    def valid_value(cls, value):
        if isinstance(value, str) and len(value) > 1000:
            raise ValueError("VALUE_TOO_LONG")
        if type(value) in (int, float) and (not finite_number(value) or abs(value) > 1e100):
            raise ValueError("INVALID_NUMBER")
        return value


class SimulationInput(InputModel):
    source_id: UUID | None = None
    adapter: Literal["SMTP", "REST"] = "SMTP"
    sender: str = Field(default="", max_length=320)
    envelope_sender: str = Field(default="", max_length=320)
    subject: str = Field(default="", max_length=500)
    body: str = Field(default="", max_length=65536)
    recipients: list[str] = Field(default_factory=list, max_length=100)
    headers: list[TestPair] = Field(default_factory=list, max_length=100)
    metadata: list[TestMetadata] = Field(default_factory=list, max_length=100)
    severity: Severity | None = None

    @field_validator("recipients")
    @classmethod
    def recipients_valid(cls, value):
        if any(not safe_text(item, 320) for item in value):
            raise ValueError("INVALID_RECIPIENT")
        return value

    @field_validator("metadata")
    @classmethod
    def unique_keys(cls, value):
        if len({item.name for item in value}) != len(value):
            raise ValueError("DUPLICATE_KEY")
        return value
