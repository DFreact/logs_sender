"""Bounded previews; originals remain immutable. No received HTML is ever rendered."""

import re
from dataclasses import dataclass, field
from email import policy
from email.errors import MessageError
from email.parser import BytesParser
from html.parser import HTMLParser
from uuid import uuid4

from sqlalchemy import select

from app.adapters.input.rest import validate
from app.domain.enums import Severity
from app.persistence.models import Attachment, BlobRecord, EventOccurrence, RawMessage, Team
from app.services.deduplication import event_for_occurrence
from app.services.identification import identify
from app.services.routing import apply_routing
from app.storage.service import attach, stage
from app.workers.queue import LostLease, complete, owned, renew

PREVIEW_LIMIT = 65536


def clean(value, limit):
    return re.sub(
        r"[\x00-\x08\x0b-\x1f\x7f\u202a-\u202e\u2066-\u2069]",
        "",
        str(value).encode("utf-8", "replace").decode("utf-8"),
    )[:limit]


class TextHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "iframe", "object", "template"}:
            self.hidden += 1
        if not self.hidden and tag in {"p", "br", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "iframe", "object", "template"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


@dataclass
class Preview:
    subject: str = ""
    sender: str = ""
    body: str = ""
    severity: str | None = None
    limited: bool = False
    headers: list = field(default_factory=list)
    attachments: list = field(default_factory=list)


def parse(payload, content_type):
    result = Preview()
    if content_type != "message/rfc822":
        data = validate(payload, content_type)
        fields = data if isinstance(data, dict) else {}
        result.subject = clean(fields.get("subject") or "", 500)
        result.sender = clean(fields.get("sender") or "", 320)
        body = fields.get("body")
        body = body if isinstance(body, str) else payload.decode("utf-8")
        result.body = clean(body, PREVIEW_LIMIT)
        result.severity = fields.get("severity") if fields.get("severity") in Severity else None
        result.limited = len(body) > PREVIEW_LIMIT
        return result
    try:
        # A conservative preflight bounds MIME tree allocation, including embedded messages.
        structural = sum(
            1
            for line in payload.splitlines()
            if line.startswith(b"--") or line.lower().startswith(b"content-type:")
        )
        if structural > 200:
            return Preview(limited=True)
        message = BytesParser(policy=policy.default).parsebytes(payload)
        result.subject = clean(message.get("Subject", ""), 500)
        result.sender = clean(message.get("From", ""), 320)
        result.headers = [[clean(k, 120), clean(v, 1000)] for k, v in list(message.items())[:100]]
        pending, count, decoded_total, plain, html = [(message, 0)], 0, 0, [], []
        while pending:
            part, depth = pending.pop()
            count += 1
            if count > 100 or depth > 20:
                raise ValueError()
            result.limited |= bool(part.defects)
            content = part.get_content_type()
            filename = part.get_filename()
            container_attachment = (
                part.is_multipart()
                and depth > 0
                and (
                    filename
                    or part.get_content_disposition() == "attachment"
                    or content == "message/rfc822"
                )
            )
            if part.is_multipart() and not container_attachment:
                pending.extend((child, depth + 1) for child in reversed(part.get_payload()))
                continue
            if container_attachment:
                embedded = part.get_payload()
                decoded = (embedded[0] if content == "message/rfc822" else part).as_bytes(
                    policy=policy.SMTP
                )
            else:
                decoded = part.get_payload(decode=True) or b""
            result.limited |= bool(part.defects)
            decoded_total += len(decoded)
            if decoded_total > 25 * 1024 * 1024:
                raise ValueError()
            if (
                filename
                or part.get_content_disposition() == "attachment"
                or content not in {"text/plain", "text/html"}
            ):
                filename = clean((filename or "").replace("\\", "/").split("/")[-1], 200)
                result.attachments.append((filename, clean(content, 120), decoded))
            else:
                try:
                    text = decoded.decode(part.get_content_charset() or "utf-8", errors="replace")
                except LookupError:
                    text = decoded.decode("utf-8", errors="replace")
                    result.limited = True
                result.limited |= len(text) > PREVIEW_LIMIT
                (plain if content == "text/plain" else html).append(text[:PREVIEW_LIMIT])
        body = "\n".join(plain)
        if not plain:
            parser = TextHTML()
            parser.feed("\n".join(html))
            body = "".join(parser.parts)
        result.limited |= len(body) > PREVIEW_LIMIT
        result.body = clean(body, PREVIEW_LIMIT)
    except (ValueError, RecursionError, TypeError, LookupError, MessageError, OverflowError):
        result.limited = True
        result.attachments = []
    return result


def normalize(factory, store, settings, lease):
    with factory() as db:
        raw = db.scalar(
            select(RawMessage).where(
                RawMessage.id == lease.entity_id, RawMessage.team_id == lease.team_id
            )
        )
        if raw is None:
            raise ValueError("RAW_NOT_FOUND")
        blob = db.get(BlobRecord, raw.blob_id)
        payload = store.read(blob.id, size=blob.size, checksum=blob.checksum)
    preview = parse(payload, raw.content_type)
    metadata = {}
    if raw.content_type == "application/json":
        parsed = validate(payload, raw.content_type)
        if isinstance(parsed, dict) and isinstance(parsed.get("metadata"), dict):
            metadata = parsed["metadata"]
    data = dict(
        sender=preview.sender,
        subject=preview.subject,
        body=preview.body,
        severity=preview.severity,
        adapter="SMTP" if raw.content_type == "message/rfc822" else "REST",
        recipient=raw.recipients,
        header=preview.headers,
        metadata=metadata,
    )
    staged = []
    for filename, content_type, attachment_data in preview.attachments:
        with factory.begin() as db:
            if not renew(db, lease, settings.lease_seconds):
                raise LostLease()
        blob_id = stage(factory, store, [attachment_data], team_id=lease.team_id)
        staged.append((uuid4(), blob_id, filename, content_type))

    def effect(db):
        if db.scalar(select(EventOccurrence.id).where(EventOccurrence.raw_message_id == raw.id)):
            return
        # Shared configuration lock gives one consistent rules/policy revision while
        # allowing independent receipts to run concurrently. Mutations take FOR UPDATE.
        db.execute(
            select(Team).where(Team.id == raw.team_id).with_for_update(read=True)
        ).scalar_one()
        source_id, version_id, normalized = identify(db, raw.team_id, data)
        event = event_for_occurrence(db, raw, source_id, normalized, preview.limited)
        snapshot = {
            key: normalized.get(key)
            for key in ("sender", "subject", "body", "severity", "category", "event_type", "tags")
        }
        occurrence = EventOccurrence(
            id=uuid4(),
            event_id=event.id,
            raw_message_id=raw.id,
            received_at=raw.received_at,
            normalized_snapshot=snapshot,
            rule_version_id=version_id,
        )
        db.add(occurrence)
        db.flush()
        routing_result = apply_routing(
            db,
            event,
            occurrence,
            {**normalized, "source": str(source_id) if source_id else None},
            settings,
        )
        from app.services.digests import capture

        capture(db, event, occurrence, routing_result)
        for identifier, blob_id, filename, content_type in staged:
            db.add(
                Attachment(
                    id=identifier,
                    raw_message_id=raw.id,
                    blob_id=blob_id,
                    filename=filename,
                    content_type=content_type,
                )
            )
            attach(db, blob_id, owner_id=identifier, owner_kind="ATTACHMENT", team_id=lease.team_id)
        current = db.get(RawMessage, raw.id)
        current.state = "LIMITED" if preview.limited else "NORMALIZED"
        current.headers = preview.headers
        from app.services.retention import assign

        assign(db, event, current)

    with factory.begin() as db:
        if owned(db, lease) is None:
            raise LostLease()
        complete(db, lease, effect)
