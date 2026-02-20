from __future__ import annotations

from platform_core.models import AlertEnvelope, ServiceIdentity


def resolve_service_identity(
    alert: AlertEnvelope,
    nr_candidates: list[str],
    azure_candidates: list[str],
    cmdb_candidates: list[str] | None = None,
    rag_candidates: list[str] | None = None,
) -> ServiceIdentity:
    cmdb_candidates = cmdb_candidates or []
    rag_candidates = rag_candidates or []

    ordered_sources = [nr_candidates, azure_candidates, cmdb_candidates, rag_candidates]
    ranked = [candidate for source in ordered_sources for candidate in source]

    if not ranked:
        return ServiceIdentity(
            canonical_service_id="unknown",
            owner=None,
            env="unknown",
            confidence=0.0,
            ambiguous_candidates=[],
        )

    canonical = ranked[0]
    ambiguity = ranked[1:3]
    base_confidence = 0.4
    if nr_candidates:
        base_confidence += 0.25
    if azure_candidates:
        base_confidence += 0.2
    if cmdb_candidates:
        base_confidence += 0.1
    if rag_candidates:
        base_confidence += 0.05

    return ServiceIdentity(
        canonical_service_id=canonical,
        owner=alert.raw_payload.get("owner"),
        env=alert.raw_payload.get("env", "prod"),
        dependency_graph_refs=alert.raw_payload.get("deps", []),
        mapped_provider_ids={"primary": canonical},
        confidence=min(base_confidence, 0.99),
        ambiguous_candidates=ambiguity,
    )
