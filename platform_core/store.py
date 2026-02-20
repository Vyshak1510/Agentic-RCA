from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from threading import Lock

from platform_core.models import (
    AdjudicationRecord,
    AlertEnvelope,
    EvalRunResult,
    InvestigationRecord,
    InvestigationStatus,
    LlmProviderRoute,
    MappingUpsertRequest,
)


class InMemoryStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self.investigations: dict[str, InvestigationRecord] = {}
        self.dedupe_index: dict[str, tuple[str, datetime]] = {}
        self.mappings: dict[tuple[str, str], MappingUpsertRequest] = {}
        self.llm_routes: dict[tuple[str, str], LlmProviderRoute] = {}
        self.eval_runs: dict[str, EvalRunResult] = {}
        self.adjudications: list[AdjudicationRecord] = []
        self.counters: defaultdict[str, int] = defaultdict(int)

    @staticmethod
    def _fingerprint(alert: AlertEnvelope) -> str:
        parts = [alert.incident_key, alert.severity, *sorted(alert.entity_ids)]
        return sha256("|".join(parts).encode("utf-8")).hexdigest()

    def record_alert(self, investigation: InvestigationRecord) -> tuple[str, bool]:
        with self._lock:
            fingerprint = self._fingerprint(investigation.alert)
            now = datetime.now(timezone.utc)
            if fingerprint in self.dedupe_index:
                existing_id, seen_at = self.dedupe_index[fingerprint]
                if now - seen_at <= timedelta(minutes=15):
                    return existing_id, True

            self.investigations[investigation.id] = investigation
            self.dedupe_index[fingerprint] = (investigation.id, now)
            self.counters["alerts_ingested_total"] += 1
            return investigation.id, False

    def get_investigation(self, investigation_id: str) -> InvestigationRecord | None:
        return self.investigations.get(investigation_id)

    def update_status(self, investigation_id: str, status: InvestigationStatus) -> InvestigationRecord | None:
        with self._lock:
            investigation = self.investigations.get(investigation_id)
            if not investigation:
                return None
            investigation.status = status
            investigation.updated_at = datetime.now(timezone.utc)
            investigation.timeline.append(f"Status updated to {status.value}")
            return investigation


store = InMemoryStore()
