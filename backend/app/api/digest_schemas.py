from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator

from app.api.config_schemas import RuleInput, SourceInput
from app.api.schemas import InputModel
from app.domain.enums import DigestPeriod, DigestSelection, Severity


class DigestInput(InputModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    enabled: bool = Field(default=True, strict=True)
    version: int = Field(default=1, ge=1, strict=True)
    channel_id: UUID
    template_id: UUID
    time_zone: str = Field(default="Europe/Moscow", max_length=80)
    minute_of_day: int = Field(default=480, ge=0, le=1439, strict=True)
    period: DigestPeriod = DigestPeriod.CALENDAR
    selection: DigestSelection = DigestSelection.FILTER
    source_ids: list[UUID] = Field(default_factory=list, max_length=100)
    severities: list[Severity | Literal[""]] = Field(default_factory=list, max_length=6)
    wait_seconds: int = Field(default=3600, ge=60, le=604800, strict=True)
    send_empty: bool = Field(default=False, strict=True)
    name_valid = field_validator("name")(RuleInput.name_valid.__func__)
    description_valid = field_validator("description")(SourceInput.text_valid.__func__)

    @field_validator("time_zone")
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("INVALID_TIME_ZONE") from None
        return value
