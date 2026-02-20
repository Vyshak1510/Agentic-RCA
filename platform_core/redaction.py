from __future__ import annotations

import re
from typing import Any

PII_PATTERNS = [
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
]


def redact_value(value: str) -> str:
    redacted = value
    for pattern in PII_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, str):
            out[key] = redact_value(value)
        elif isinstance(value, dict):
            out[key] = redact_payload(value)
        elif isinstance(value, list):
            out[key] = [redact_value(v) if isinstance(v, str) else v for v in value]
        else:
            out[key] = value
    return out
