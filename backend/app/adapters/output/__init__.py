"""Output contract used by the isolated delivery worker."""

import http.client
import json
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Literal, Protocol

from app.adapters.output import network
from app.adapters.output.retry import retry_after
from app.api.errors import ApiFailure, ErrorCode


@dataclass(frozen=True)
class PreparedNotification:
    subject: str
    body: str
    html: str = ""
    payload: str = ""
    idempotency_key: str = ""


@dataclass(frozen=True)
class DeliveryResult:
    outcome: Literal["ACCEPTED", "TRANSIENT", "PERMANENT", "UNKNOWN"]
    reason: str | None = None
    retry_after: int | None = None


class OutputContract(Protocol):
    def validate_configuration(self): ...
    def health_check(self): ...
    def test_connection(self): ...
    def send(self, prepared: PreparedNotification) -> DeliveryResult: ...


def tls_context(settings):
    return (
        ssl.create_default_context(cafile=str(settings.outbound_ca_file))
        if settings.outbound_ca_file
        else ssl.create_default_context()
    )


class BeforeSend(Exception):
    pass


class PinnedSMTP(smtplib.SMTP):
    def __init__(self, settings, config):
        self.settings, self.config = settings, config
        super().__init__(timeout=3)

    def _get_socket(self, host, port, timeout):
        sock = network.connect(self.settings, host, port)
        if self.config["tls"] == "TLS":
            try:
                sock = tls_context(self.settings).wrap_socket(sock, server_hostname=host)
            except BaseException:
                sock.close()
                raise
        return sock


class Adapter:
    def __init__(self, settings, configuration, secret_provider=lambda: ""):
        self.settings, self.config, self.secret_provider = settings, configuration, secret_provider

    def destination(self):
        if self.config["kind"] == "SMTP":
            return self.config["host"], self.config["port"]
        if self.config["kind"] == "TELEGRAM":
            if not self.settings.telegram_allowed:
                raise ApiFailure(422, ErrorCode.OUTBOUND_BLOCKED)
            return "api.telegram.org", 443
        if self.config["kind"] == "MAX":
            if not self.settings.max_allowed:
                raise ApiFailure(422, ErrorCode.OUTBOUND_BLOCKED)
            return "platform-api2.max.ru", 443
        host, port, _ = network.endpoint(self.config["url"])
        return host, port

    def validate_configuration(self):
        network.validate_destination(self.settings, *self.destination())

    def tls_socket(self):
        host, port = self.destination()
        sock = network.connect(self.settings, host, port)
        try:
            return tls_context(self.settings).wrap_socket(sock, server_hostname=host)
        except BaseException:
            sock.close()
            raise

    def http(self, path, payload, secret="", idempotency_key="", *, method="POST"):
        host, port = self.destination()
        connection = http.client.HTTPSConnection(host, port, timeout=3)
        try:
            try:
                connection.sock = self.tls_socket()
            except ApiFailure:
                raise
            except Exception:
                raise BeforeSend() from None
            headers = {"Content-Type": "application/json", "Connection": "close"}
            if idempotency_key:
                headers["Idempotency-Key"] = idempotency_key
            if secret:
                headers["Authorization"] = (
                    secret if self.config["kind"] == "MAX" else "Bearer " + secret
                )
            connection.request(
                method, path, body=payload.encode() if payload else None, headers=headers
            )
            response = connection.getresponse()
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError()
            return response.status, raw, retry_after(response.getheader("Retry-After"))
        finally:
            connection.close()

    def smtp(self):
        client = PinnedSMTP(self.settings, self.config)
        try:
            # smtplib needs the hostname for STARTTLS certificate verification.
            client._host = self.config["host"]
            code, _ = client.connect(self.config["host"], self.config["port"])
            if code != 220:
                raise OSError()
            client.ehlo()
            if self.config["tls"] == "STARTTLS":
                client.starttls(context=tls_context(self.settings))
                client.ehlo()
            if self.config["username"]:
                client.login(self.config["username"], self.secret_provider())
            return client
        except BaseException:
            client.close()
            raise

    def health_check(self):
        return self.test_connection()

    def test_connection(self):
        try:
            if self.config["kind"] == "SMTP":
                client = self.smtp()
                try:
                    if client.noop()[0] != 250:
                        raise OSError()
                finally:
                    client.close()
                return "CONNECTED"
            if self.config["kind"] == "TELEGRAM":
                status, raw, _ = self.http("/bot" + self.secret_provider() + "/getMe", "{}")
                if status != 200 or json.loads(raw).get("ok") is not True:
                    raise OSError()
                return "BOT_VERIFIED"
            if self.config["kind"] == "MAX":
                status, raw, _ = self.http("/me", "", self.secret_provider(), method="GET")
                if status != 200 or json.loads(raw).get("is_bot") is not True:
                    raise OSError()
                return "BOT_VERIFIED"
            with self.tls_socket():
                return "TLS_ONLY"
        except ApiFailure:
            raise
        except Exception:
            code = (
                ErrorCode.MAIL_CONNECTION_FAILED
                if self.config["kind"] == "SMTP"
                else ErrorCode.CHANNEL_CONNECTION_FAILED
            )
            raise ApiFailure(422, code) from None

    def send(self, prepared):
        # Immutable, already rendered input; conservative ambiguity on transport errors.
        if sum(
            len(v.encode())
            for v in (prepared.subject, prepared.body, prepared.html, prepared.payload)
        ) > 65536 or any(ord(c) < 32 or ord(c) == 127 for c in prepared.subject):
            return DeliveryResult("PERMANENT", "TEMPLATE_INVALID")
        started = False
        try:
            self.validate_configuration()
            if self.config["kind"] == "SMTP":
                message = EmailMessage()
                message["Subject"], message["From"] = prepared.subject, self.config["sender"]
                message["To"] = ", ".join(self.config["recipients"])
                message.set_content(prepared.body)
                if prepared.html:
                    message.add_alternative(prepared.html, subtype="html")
                client = self.smtp()
                try:
                    started = True
                    refused = client.send_message(message)
                    return (
                        DeliveryResult("UNKNOWN", "UNKNOWN_RESULT")
                        if refused
                        else DeliveryResult("ACCEPTED")
                    )
                finally:
                    client.close()
            if self.config["kind"] == "TELEGRAM":
                if not prepared.body or len(prepared.body.encode("utf-16-le")) // 2 > 4096:
                    return DeliveryResult("PERMANENT", "TEMPLATE_INVALID")
                path = "/bot" + self.secret_provider() + "/sendMessage"
                payload = json.dumps({"chat_id": self.config["chat_id"], "text": prepared.body})
                secret = ""
            elif self.config["kind"] == "MAX":
                if not prepared.body or len(prepared.body.encode("utf-16-le")) // 2 > 4000:
                    return DeliveryResult("PERMANENT", "TEMPLATE_INVALID")
                chat_id = int(self.config["chat_id"])
                if not -(2**63) <= chat_id < 2**63 or chat_id == 0:
                    return DeliveryResult("PERMANENT", "TEMPLATE_INVALID")
                path = f"/messages?chat_id={chat_id}&disable_link_preview=true"
                payload = json.dumps({"text": prepared.body, "notify": True})
                secret = self.secret_provider()
            else:
                _, _, path = network.endpoint(self.config["url"])
                payload = prepared.payload
                data = json.loads(payload)
                if (
                    not isinstance(data, dict)
                    or set(data) != {"subject", "message"}
                    or any(not isinstance(v, str) for v in data.values())
                ):
                    return DeliveryResult("PERMANENT", "TEMPLATE_INVALID")
                secret = self.secret_provider()
            started = True
            status, raw, retry = self.http(path, payload, secret, prepared.idempotency_key)
            if 200 <= status < 300:
                if self.config["kind"] == "TELEGRAM" and json.loads(raw).get("ok") is not True:
                    return DeliveryResult("UNKNOWN", "UNKNOWN_RESULT")
                if self.config["kind"] == "MAX":
                    mid = json.loads(raw).get("message", {}).get("body", {}).get("mid")
                    if not isinstance(mid, str) or not mid:
                        return DeliveryResult("UNKNOWN", "UNKNOWN_RESULT")
                return DeliveryResult("ACCEPTED")
            if self.config["kind"] == "TELEGRAM":
                try:
                    hint = json.loads(raw).get("parameters", {}).get("retry_after")
                    if type(hint) is int and hint >= 0:
                        retry = max(retry or 0, min(3600, hint))
                except (ValueError, AttributeError):
                    pass
            reason = (
                "RATE_LIMITED"
                if status == 429
                else "REMOTE_UNAVAILABLE"
                if status >= 500
                else "AUTHENTICATION_FAILED"
                if status in (401, 403)
                else "REMOTE_REJECTED"
            )
            return DeliveryResult(
                "TRANSIENT" if status == 429 or status >= 500 else "PERMANENT", reason, retry
            )
        except ApiFailure as exc:
            return DeliveryResult(
                "PERMANENT",
                "OUTBOUND_BLOCKED"
                if exc.code == ErrorCode.OUTBOUND_BLOCKED
                else "CONNECTION_FAILED",
            )
        except BeforeSend:
            return DeliveryResult("TRANSIENT", "CONNECTION_FAILED")
        except smtplib.SMTPRecipientsRefused as exc:
            temporary = any(400 <= code < 500 for code, _ in exc.recipients.values())
            return DeliveryResult("TRANSIENT" if temporary else "PERMANENT", "RECIPIENT_REJECTED")
        except smtplib.SMTPResponseException as exc:
            reason = (
                "AUTHENTICATION_FAILED"
                if isinstance(exc, smtplib.SMTPAuthenticationError)
                else "REMOTE_REJECTED"
            )
            return DeliveryResult(
                "TRANSIENT" if 400 <= exc.smtp_code < 500 else "PERMANENT", reason
            )
        except Exception:
            return (
                DeliveryResult("UNKNOWN", "UNKNOWN_RESULT")
                if started
                else DeliveryResult("TRANSIENT", "CONNECTION_FAILED")
            )


ADAPTER_KINDS = ("SMTP", "TELEGRAM", "MAX", "WEBHOOK")
