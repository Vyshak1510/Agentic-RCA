from __future__ import annotations

from pydantic import BaseModel, Field


class ConnectorManifest(BaseModel):
    name: str
    provider: str
    read_only: bool = True
    capabilities: list[str] = Field(default_factory=list)
    sdk_version: str = "0.1"
