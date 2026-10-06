from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.api.config_schemas import RuleInput, SourceInput
from app.api.schemas import InputModel


class StepInput(InputModel):
    channel_id: UUID
    delay_seconds: int = Field(ge=0, le=2592000, strict=True)


class PolicyInput(InputModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    enabled: bool = Field(default=True, strict=True)
    version: int = Field(default=1, ge=1, strict=True)
    steps: list[StepInput] = Field(min_length=1, max_length=10)
    name_valid = field_validator("name")(RuleInput.name_valid.__func__)
    description_valid = field_validator("description")(SourceInput.text_valid.__func__)

    @model_validator(mode="after")
    def ordered(self):
        delays = [step.delay_seconds for step in self.steps]
        if delays[0] != 0 or any(a >= b for a, b in zip(delays, delays[1:], strict=False)):
            raise ValueError("INVALID_ESCALATION_SCHEDULE")
        return self
