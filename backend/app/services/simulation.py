from types import SimpleNamespace

from app.api.errors import ApiFailure, ErrorCode
from app.domain.rules.routing import plan
from app.persistence.models import RuleVersion, Source
from app.services.deduplication import effective_policy, preview_occurrence
from app.services.identification import identify
from app.services.normalization import clean
from app.services.retention import effective
from app.services.routing import active_rules
from app.workers.queue import clock


def simulate(db, team_id, body):
    timestamp = clock(db)
    data = dict(
        sender=clean(body.sender, 320),
        subject=clean(body.subject, 500),
        body=clean(body.body, 65536),
        adapter=body.adapter,
        severity=body.severity,
        recipient=body.recipients,
        header=[[h.name, clean(h.value, 1000)] for h in body.headers],
        metadata={entry.name: entry.value for entry in body.metadata},
    )
    source_id, version_id, data = identify(db, team_id, data)
    if body.source_id is not None:
        source = db.get(Source, body.source_id)
        if source is None or source.team_id != team_id or not source.enabled:
            raise ApiFailure(422, ErrorCode.RESOURCE_NOT_FOUND)
        # A manual source is an explicit test override; identification assignments
        # must not leak from a different automatically detected source.
        source_id, version_id = source.id, None
        data = {**data, "severity": body.severity, "category": "", "event_type": "", "tags": []}
    source = db.get(Source, source_id) if source_id else None
    version = db.get(RuleVersion, version_id) if version_id else None
    raw = SimpleNamespace(
        team_id=team_id,
        received_at=timestamp,
        credential_id=None,
        envelope_sender=clean(body.envelope_sender, 320),
        payload_hash="",
    )
    dedup_status, existing = preview_occurrence(db, raw, source_id, data)
    policy = effective_policy(db, team_id, source_id)
    data = {**data, "source": str(source_id) if source_id else None}
    current = (
        {
            **data,
            **{
                field: getattr(existing, field)
                for field in ("severity", "category", "event_type", "tags")
            },
        }
        if existing
        else data
    )
    result = plan(
        active_rules(db, team_id),
        data,
        current=current,
        repeated=existing is not None,
        suppressed=existing is not None and existing.status == "SUPPRESSED",
    )
    retention, enabled = effective(db, team_id, source_id)
    return {
        "checked_at": timestamp,
        "manual_source": body.source_id is not None,
        "source_name": source.name if source else None,
        "identification": {"name": version.snapshot["name"], "version": version.version}
        if version
        else None,
        "deduplication": {
            "status": dedup_status,
            "event_id": existing.id if existing else None,
            "window_seconds": policy.window_seconds if policy and policy.enabled else None,
        },
        "retention": {
            "status": "ENABLED" if enabled else "DISABLED",
            "event_days": retention["event_days"],
            "raw_days": retention["raw_days"],
        },
        "result": {
            field: result["data"].get(field)
            for field in ("severity", "category", "event_type", "tags")
        },
        "status": "SUPPRESSED" if result["suppressed"] else existing.status if existing else "NEW",
        "decisions": result["decisions"],
    }
