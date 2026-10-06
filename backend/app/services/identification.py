from sqlalchemy import select

from app.domain.rules.conditions import evaluate
from app.persistence.models import IdentificationRule, RuleVersion, Source


def identify(db, team_id, data):
    rows = db.execute(
        select(IdentificationRule, Source)
        .join(Source, Source.id == IdentificationRule.source_id)
        .where(
            IdentificationRule.team_id == team_id,
            Source.team_id == team_id,
            IdentificationRule.enabled.is_(True),
            Source.enabled.is_(True),
        )
        .order_by(IdentificationRule.priority, IdentificationRule.id)
    ).all()
    for rule, source in rows:
        if evaluate(rule.conditions, data) is True:
            version_id = db.scalar(
                select(RuleVersion.id).where(
                    RuleVersion.rule_id == rule.id, RuleVersion.version == rule.version
                )
            )
            return (
                source.id,
                version_id,
                {
                    **data,
                    **{key: value for key, value in rule.assignments.items() if value is not None},
                },
            )
    return None, None, data
