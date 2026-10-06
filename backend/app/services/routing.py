from uuid import uuid4

from sqlalchemy import select

from app.domain.rules.routing import ActionOutcome, RuleAction, plan
from app.persistence.models import RoutingRule, RuleExecution, RuleVersion
from app.workers.queue import insert_for


def active_rules(db, team_id):
    rows = db.execute(
        select(RoutingRule, RuleVersion.id)
        .join(
            RuleVersion,
            (RuleVersion.routing_rule_id == RoutingRule.id)
            & (RuleVersion.version == RoutingRule.version),
        )
        .where(RoutingRule.team_id == team_id, RoutingRule.enabled.is_(True))
        .order_by(RoutingRule.priority, RoutingRule.id)
    ).all()
    return [
        dict(
            id=str(rule.id),
            name=rule.name,
            version=rule.version,
            version_id=str(version_id),
            conditions=rule.conditions,
            actions=rule.actions,
        )
        for rule, version_id in rows
    ]


def apply_routing(db, event, occurrence, data, settings=None):
    from app.services.events import stop_for_event
    from app.services.events.escalation import from_decision as escalate
    from app.services.notifications import from_decision
    from app.settings import Settings
    from app.workers.queue import clock

    settings = settings or Settings()
    from uuid import UUID

    if db.scalar(
        select(RuleExecution.id).where(RuleExecution.occurrence_id == occurrence.id).limit(1)
    ):
        return None

    current = {
        **data,
        **{
            field: getattr(event, field) for field in ("severity", "category", "event_type", "tags")
        },
    }
    result = plan(
        active_rules(db, event.team_id),
        data,
        current=current,
        repeated=event.occurrence_count > 1,
        suppressed=event.status == "SUPPRESSED",
    )
    for decision in result["decisions"]:
        action = decision["action"]
        scope = (
            occurrence.id
            if action["scope"] == "OCCURRENCE" or decision["outcome"] == "REPEAT"
            else event.id
        )
        inserted = db.scalar(
            insert_for(db, RuleExecution)
            .values(
                id=uuid4(),
                event_id=event.id,
                occurrence_id=occurrence.id,
                version_id=UUID(decision["version_id"]),
                action_index=decision["action_index"],
                scope_key=scope,
                result=decision,
            )
            .on_conflict_do_nothing(
                index_elements=["event_id", "version_id", "action_index", "scope_key"]
            )
            .returning(RuleExecution.id)
        )
        if inserted:
            from_decision(db, settings, event, occurrence, decision, inserted, result["data"])
            escalate(db, settings, event, decision, inserted, result["data"])
        if inserted and decision["outcome"] == ActionOutcome.APPLIED:
            if action["kind"] == RuleAction.SUPPRESS:
                if event.status != "SUPPRESSED":
                    event.status = "SUPPRESSED"
                    event.version += 1
                    event.state_changed_at, event.state_changed_by = clock(db), None
                stop_for_event(db, event, clock(db))
            for key, value in decision["applied"].items():
                setattr(event, key, value)
    return result
