import json

from app.api.errors import ApiFailure, ErrorCode


def validate(payload: bytes, content_type: str):
    if content_type not in {"application/json", "text/plain"}:
        raise ApiFailure(415, ErrorCode.UNSUPPORTED_CONTENT_TYPE)
    try:
        text = payload.decode("utf-8")
        if content_type == "application/json":
            # Bound nesting before the JSON parser allocates recursive structures.
            depth, quoted, escaped = 0, False, False
            for char in text:
                if quoted:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == '"':
                        quoted = False
                elif char == '"':
                    quoted = True
                elif char in "[{":
                    depth += 1
                    if depth > 20:
                        raise ApiFailure(413, ErrorCode.REQUEST_TOO_LARGE)
                elif char in "]}":
                    depth -= 1

            def invalid_constant(_):
                raise ValueError()

            result = json.loads(text, parse_constant=invalid_constant)
            pending, count = [result], 0
            while pending:
                item = pending.pop()
                count += 1
                if count > 10000:
                    raise ApiFailure(413, ErrorCode.REQUEST_TOO_LARGE)
                if isinstance(item, dict):
                    pending.extend(item.values())
                elif isinstance(item, list):
                    pending.extend(item)
            return result
        return text
    except (ValueError, RecursionError):
        raise ApiFailure(400, ErrorCode.INVALID_INPUT) from None
