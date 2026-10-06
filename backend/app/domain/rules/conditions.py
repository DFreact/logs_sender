"""Validated data-only conditions. Missing data uses three-valued logic."""

import math
from functools import lru_cache

import re2

FIELDS = ("sender", "recipient", "subject", "body", "header", "adapter", "metadata")
ROUTING_FIELDS = (*FIELDS, "source", "severity", "category", "event_type", "tags")
OPERATORS = (
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "starts_with",
    "ends_with",
    "regex",
    "exists",
    "gt",
    "gte",
    "lt",
    "lte",
)


@lru_cache(maxsize=128)
def expression(pattern, case_sensitive):
    options = re2.Options()
    options.log_errors = False
    options.max_mem = 8 * 1024 * 1024
    options.case_sensitive = case_sensitive
    return re2.compile(pattern, options=options)


def safe_text(value, max_length):
    return (
        isinstance(value, str)
        and len(value) <= max_length
        and all(ord(char) >= 32 and not 0xD800 <= ord(char) <= 0xDFFF for char in value)
    )


def finite_number(value):
    # Avoid converting arbitrary JSON integers to float (which can overflow).
    return type(value) is int or (type(value) is float and math.isfinite(value))


def validate(tree, *, fields=FIELDS):
    pending, count = [(tree, 0)], 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if count > 100 or depth > 3 or not isinstance(node, dict):
            raise ValueError("INVALID_CONDITIONS")
        if "group" in node:
            if set(node) != {"group", "children"} or node["group"] not in ("AND", "OR", "NOT"):
                raise ValueError("INVALID_GROUP")
            children = node["children"]
            if not isinstance(children, list) or not 1 <= len(children) <= 100:
                raise ValueError("INVALID_CHILDREN")
            if node["group"] == "NOT" and len(children) != 1:
                raise ValueError("INVALID_NOT")
            pending.extend((child, depth + 1) for child in children)
            continue
        if set(node) - {"field", "operator", "value", "selector", "case_sensitive"}:
            raise ValueError("INVALID_CONDITION")
        if node.get("field") not in fields or node.get("operator") not in OPERATORS:
            raise ValueError("INVALID_FIELD_OPERATOR")
        if type(node.get("case_sensitive", False)) is not bool:
            raise ValueError("INVALID_CASE")
        if node["field"] in ("header", "metadata"):
            if not safe_text(node.get("selector"), 120) or not node["selector"]:
                raise ValueError("INVALID_SELECTOR")
        elif node.get("selector"):
            raise ValueError("UNEXPECTED_SELECTOR")
        operator, value = node["operator"], node.get("value")
        if operator == "exists":
            if value is not None:
                raise ValueError("UNEXPECTED_VALUE")
        elif operator in ("gt", "gte", "lt", "lte"):
            if node["field"] != "metadata" or not finite_number(value):
                raise ValueError("INVALID_NUMBER")
        elif not safe_text(value, 500):
            raise ValueError("INVALID_VALUE")
        elif operator == "regex":
            try:
                expression(value, node.get("case_sensitive", False))
            except re2.error:
                raise ValueError("INVALID_REGEX") from None
    return tree


def evaluate(node, data):
    if "group" in node:
        values = [evaluate(child, data) for child in node["children"]]
        if node["group"] == "NOT":
            return None if values[0] is None else not values[0]
        if node["group"] == "AND":
            return False if False in values else None if None in values else True
        return True if True in values else None if None in values else False
    field = node["field"]
    value = data.get(field)
    if field == "header":
        values = [v for k, v in (value or []) if k.casefold() == node["selector"].casefold()]
        value = values if values else None
    elif field == "metadata":
        value = (value or {}).get(node["selector"])
    operator = node["operator"]
    if operator == "exists":
        return value is not None
    if value is None:
        return None
    values = value if isinstance(value, list) else [value]
    if not values:
        return None
    expected = node["value"]

    def compare(actual):
        if operator in ("gt", "gte", "lt", "lte"):
            if not finite_number(actual):
                return None
            return {
                "gt": actual > expected,
                "gte": actual >= expected,
                "lt": actual < expected,
                "lte": actual <= expected,
            }[operator]
        if not isinstance(actual, str):
            return None
        if operator == "regex":
            actual = actual.encode("utf-8", "replace").decode("utf-8")
            return bool(expression(expected, node.get("case_sensitive", False)).search(actual))
        left, right = actual, expected
        if not node.get("case_sensitive", False):
            left, right = left.casefold(), right.casefold()
        return {
            "equals": left == right,
            "not_equals": left == right,
            "contains": right in left,
            "not_contains": right in left,
            "starts_with": left.startswith(right),
            "ends_with": left.endswith(right),
        }[operator]

    results = [compare(item) for item in values]
    if operator in ("not_equals", "not_contains"):
        return False if True in results else None if None in results else True
    return True if True in results else None if None in results else False
