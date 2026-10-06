"""Remove obsolete configuration snapshots; current/referenced versions remain."""

from datetime import timedelta

from sqlalchemy import and_, exists, or_, select

from app.persistence.models import (
    DigestDefinition,
    DigestVersion,
    EscalationPolicy,
    EscalationPolicyVersion,
    EventOccurrence,
    IdentificationRule,
    Notification,
    RoutingRule,
    RuleExecution,
    RuleVersion,
    Template,
    TemplateVersion,
)


def prune_versions(db, team, now, days, limit):
    from app.services.retention.cleanup import prune

    cutoff = now - timedelta(days=days)
    count = prune(
        db,
        RuleVersion,
        [
            RuleVersion.created_at <= cutoff,
            or_(
                exists(
                    select(IdentificationRule.id).where(
                        IdentificationRule.id == RuleVersion.rule_id,
                        IdentificationRule.team_id == team,
                        IdentificationRule.version > RuleVersion.version,
                    )
                ),
                exists(
                    select(RoutingRule.id).where(
                        RoutingRule.id == RuleVersion.routing_rule_id,
                        RoutingRule.team_id == team,
                        RoutingRule.version > RuleVersion.version,
                    )
                ),
            ),
            ~exists(
                select(EventOccurrence.id).where(EventOccurrence.rule_version_id == RuleVersion.id)
            ),
            ~exists(select(RuleExecution.id).where(RuleExecution.version_id == RuleVersion.id)),
        ],
        limit,
    )
    for model, parent, foreign in [
        (TemplateVersion, Template, TemplateVersion.template_id),
        (EscalationPolicyVersion, EscalationPolicy, EscalationPolicyVersion.policy_id),
        (DigestVersion, DigestDefinition, DigestVersion.definition_id),
    ]:
        conditions = [
            model.created_at <= cutoff,
            exists(
                select(parent.id).where(
                    and_(
                        parent.id == foreign, parent.team_id == team, parent.version > model.version
                    )
                )
            ),
        ]
        if model is TemplateVersion:
            conditions.append(
                ~exists(
                    select(Notification.id).where(
                        Notification.template_version_id == TemplateVersion.id
                    )
                )
            )
        # Escalations and digests embed their execution snapshot, without version FKs.
        count += prune(db, model, conditions, limit)
    return count
