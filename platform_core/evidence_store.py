from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from platform_core.models import EvidenceItem
from platform_core.redaction import redact_payload


class EvidenceStore:
    def __init__(self) -> None:
        self._data: dict[str, list[EvidenceItem]] = {}

    def add(self, investigation_id: str, provider: str, evidence_type: str, payload: dict) -> EvidenceItem:
        citation_id = f"cit-{uuid4()}"
        evidence = EvidenceItem(
            provider=provider,
            timestamp=datetime.now(timezone.utc),
            evidence_type=evidence_type,
            normalized_fields=redact_payload(payload),
            citation_id=citation_id,
            redaction_state="redacted",
        )
        self._data.setdefault(investigation_id, []).append(evidence)
        return evidence

    def list(self, investigation_id: str) -> list[EvidenceItem]:
        return self._data.get(investigation_id, [])
