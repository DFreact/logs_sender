import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator

from app.adapters.output.network import endpoint, host_name
from app.api.config_schemas import RuleInput
from app.api.schemas import InputModel

Kind = Literal["SMTP", "TELEGRAM", "MAX", "WEBHOOK"]


class SMTPConfiguration(InputModel):
    kind: Literal["SMTP"]
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=587, ge=1, le=65535, strict=True)
    tls: Literal["STARTTLS", "TLS"] = "STARTTLS"
    username: str = Field(default="", max_length=320)
    sender: str = Field(min_length=3, max_length=320)
    recipients: list[str] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def valid(self):
        self.host = host_name(self.host)
        for value in [self.sender, *self.recipients]:
            if (
                not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+", value)
                or len(value) > 320
            ):
                raise ValueError("INVALID_ADDRESS")
        if any(ord(c) < 32 or ord(c) == 127 for c in self.username):
            raise ValueError("INVALID_USERNAME")
        return self


class TelegramConfiguration(InputModel):
    kind: Literal["TELEGRAM"]
    chat_id: str = Field(pattern=r"^-?[0-9]{1,20}$")


class MaxConfiguration(InputModel):
    kind: Literal["MAX"]
    chat_id: str = Field(pattern=r"^-?[0-9]{1,19}$")

    @field_validator("chat_id")
    @classmethod
    def valid(cls, value):
        if not -(2**63) <= int(value) < 2**63 or int(value) == 0:
            raise ValueError("INVALID_CHAT_ID")
        return str(int(value))


class WebhookConfiguration(InputModel):
    kind: Literal["WEBHOOK"]
    url: str = Field(min_length=8, max_length=2000)

    @field_validator("url")
    @classmethod
    def valid(cls, value):
        endpoint(value)
        return value


Configuration = Annotated[
    SMTPConfiguration | TelegramConfiguration | MaxConfiguration | WebhookConfiguration,
    Field(discriminator="kind"),
]


class ChannelInput(InputModel):
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = Field(default=True, strict=True)
    version: int = Field(default=1, ge=1, strict=True)
    secret_version: int = Field(default=0, ge=0, strict=True)
    template_id: UUID | None = None
    configuration: Configuration
    secret: SecretStr | None = Field(default=None, max_length=1024)
    clear_secret: bool = Field(default=False, strict=True)
    name_valid = field_validator("name")(RuleInput.name_valid.__func__)

    @model_validator(mode="after")
    def valid(self):
        if self.secret is not None:
            value = self.secret.get_secret_value()
            if self.clear_secret or not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError("INVALID_SECRET")
            if self.configuration.kind == "TELEGRAM" and not re.fullmatch(
                r"[0-9]{1,20}:[A-Za-z0-9_-]{20,100}", value
            ):
                raise ValueError("INVALID_TOKEN")
        return self


class AdapterInput(InputModel):
    enabled: bool = Field(strict=True)
    visible: bool = Field(strict=True)
    version: int = Field(ge=1, strict=True)


class TemplateInput(InputModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Kind
    version: int = Field(default=1, ge=1, strict=True)
    subject: str = Field(default="", max_length=2000)
    body: str = Field(min_length=1, max_length=16384)
    html: str = Field(default="", max_length=16384)
    name_valid = field_validator("name")(RuleInput.name_valid.__func__)

    @model_validator(mode="after")
    def valid(self):
        if self.kind != "SMTP" and self.html:
            raise ValueError("HTML_NOT_SUPPORTED")
        return self


class PreviewEvent(InputModel):
    subject: str = Field(default="", max_length=1000)
    body: str = Field(default="", max_length=4096)
    sender: str = Field(default="", max_length=320)
    source: str = Field(default="", max_length=120)
    severity: str = Field(default="", max_length=120)
    category: str = Field(default="", max_length=120)
    event_type: str = Field(default="", max_length=120)


class PreviewInput(InputModel):
    template: TemplateInput
    event: PreviewEvent = Field(default_factory=PreviewEvent)
