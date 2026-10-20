"""Translate VK's HTTP-200 API errors without retaining untrusted response details."""


def classify(data):
    if not isinstance(data, dict):
        return "UNKNOWN", "UNKNOWN_RESULT"
    if "error" in data:
        error = data["error"]
        code = error.get("error_code") if isinstance(error, dict) else None
        if type(code) is not int:
            return "UNKNOWN", "UNKNOWN_RESULT"
        if code in (6, 9, 29):
            return "TRANSIENT", "RATE_LIMITED"
        if code == 10:
            return "TRANSIENT", "REMOTE_UNAVAILABLE"
        if code in (5, 27):
            return "PERMANENT", "AUTHENTICATION_FAILED"
        if code in (901, 902):
            return "PERMANENT", "RECIPIENT_REJECTED"
        return "PERMANENT", "REMOTE_REJECTED"
    if type(data.get("response")) is int and data["response"] >= 0:
        return "ACCEPTED", None
    return "UNKNOWN", "UNKNOWN_RESULT"
