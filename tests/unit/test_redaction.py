from __future__ import annotations

from platform_core.redaction import redact_payload


def test_redaction_masks_email_and_ssn() -> None:
    payload = {
        "message": "user jane@example.com failed with ssn 123-45-6789",
        "nested": {"contact": "john@example.org"},
    }

    redacted = redact_payload(payload)

    assert "example.com" not in redacted["message"]
    assert "123-45-6789" not in redacted["message"]
    assert redacted["nested"]["contact"] == "[REDACTED]"
