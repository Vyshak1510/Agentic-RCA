from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any

import psycopg
from psycopg.rows import dict_row


@dataclass(frozen=True)
class PersistedSettingRecord:
    tenant: str
    environment: str
    category: str
    item_key: str
    payload: dict[str, Any] | list[Any]


class SettingsStatePersistence:
    """Simple JSONB-backed persistence for settings/config state."""

    def __init__(self, dsn: str, *, table_name: str = "rca_settings_state", connect_timeout: int = 3) -> None:
        self._dsn = dsn
        self._table_name = table_name
        self._connect_timeout = max(1, connect_timeout)
        self._schema_ready = False

    @classmethod
    def from_env(cls) -> SettingsStatePersistence | None:
        dsn = (os.getenv("RCA_SETTINGS_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()
        if not dsn:
            return None
        timeout = int(os.getenv("RCA_SETTINGS_DB_CONNECT_TIMEOUT", "3") or "3")
        return cls(dsn, connect_timeout=timeout)

    def _connect(self) -> psycopg.Connection[Any]:
        return psycopg.connect(self._dsn, connect_timeout=self._connect_timeout, row_factory=dict_row)

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._table_name} (
                        tenant TEXT NOT NULL,
                        environment TEXT NOT NULL,
                        category TEXT NOT NULL,
                        item_key TEXT NOT NULL,
                        payload JSONB NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        PRIMARY KEY (tenant, environment, category, item_key)
                    )
                    """
                )
        self._schema_ready = True

    def upsert(
        self,
        *,
        tenant: str,
        environment: str,
        category: str,
        item_key: str,
        payload: dict[str, Any] | list[Any],
    ) -> None:
        self.ensure_schema()
        payload_json = json.dumps(payload, default=str)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._table_name}
                    (tenant, environment, category, item_key, payload, updated_at)
                    VALUES (%s, %s, %s, %s, %s::jsonb, NOW())
                    ON CONFLICT (tenant, environment, category, item_key)
                    DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
                    """,
                    (tenant, environment, category, item_key, payload_json),
                )

    def list_all(self) -> list[PersistedSettingRecord]:
        self.ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT tenant, environment, category, item_key, payload
                    FROM {self._table_name}
                    ORDER BY updated_at ASC
                    """
                )
                rows = cur.fetchall()
        records: list[PersistedSettingRecord] = []
        for row in rows:
            payload = row.get("payload")
            if not isinstance(payload, (dict, list)):
                continue
            records.append(
                PersistedSettingRecord(
                    tenant=str(row.get("tenant") or ""),
                    environment=str(row.get("environment") or ""),
                    category=str(row.get("category") or ""),
                    item_key=str(row.get("item_key") or ""),
                    payload=payload,
                )
            )
        return records
