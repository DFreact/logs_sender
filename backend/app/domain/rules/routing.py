"""Pure evaluation: no database, queues, files or output adapters."""

from copy import deepcopy
from enum import StrEnum

from app.domain.rules.conditions import evaluate


class RuleAction(StrEnum):
    SET_FIELDS = "SET_FIELDS"
    SUPPRESS = "SUPPRESS"
    NOTIFY = "NOTIFY"
    DIGEST = "DIGEST"
    ESCALATE = "ESCALATE"


class ActionOutcome(StrEnum):
    APPLIED = "APPLIED"
    PLANNED = "PLANNED"
    SUPPRESSED = "SUPPRESSED"
    REPEAT = "REPEAT"
    OVERRIDDEN = "OVERRIDDEN"


def plan(rules, data, *, repeated=False, suppressed=False, current=None):
    """Rules must be ordered by priority, then id. Match only the input snapshot."""
    matched = [rule for rule in rules if evaluate(rule["conditions"], data) is True]
    suppressed = suppressed or any(
        action["kind"] == RuleAction.SUPPRESS and (not repeated or action["scope"] == "OCCURRENCE")
        for rule in matched
        for action in rule["actions"]
    )
    result, claimed, decisions = deepcopy(current if current is not None else data), set(), []
    for rule in matched:
        for index, action in enumerate(rule["actions"]):
            applied, overridden = {}, []
            if repeated and action["scope"] == "EVENT":
                outcome = ActionOutcome.REPEAT
            elif action["kind"] == RuleAction.SET_FIELDS:
                for field, value in action["fields"].items():
                    if value is None:
                        continue
                    if field == "tags":
                        if not value:
                            continue
                        result[field] = sorted(set(result.get(field) or []) | set(value))
                        applied[field] = result[field]
                    elif field in claimed:
                        overridden.append(field)
                    else:
                        claimed.add(field)
                        result[field] = value
                        applied[field] = value
                outcome = ActionOutcome.APPLIED if applied else ActionOutcome.OVERRIDDEN
            elif action["kind"] == RuleAction.SUPPRESS:
                outcome = ActionOutcome.APPLIED
            elif suppressed:
                outcome = ActionOutcome.SUPPRESSED
            else:
                outcome = ActionOutcome.PLANNED
            decisions.append(
                {
                    "rule_id": rule["id"],
                    "rule_name": rule["name"],
                    "version_id": rule["version_id"],
                    "version": rule["version"],
                    "action_index": index,
                    "action": deepcopy(action),
                    "outcome": outcome,
                    "applied": applied,
                    "overridden": overridden,
                }
            )
    return {"data": result, "suppressed": suppressed, "decisions": decisions}
