from __future__ import annotations

import pytest

from platform_core.llm_router import LlmRoutingError, ModelRoute, synthesize_with_fallback


def test_primary_failure_uses_fallback() -> None:
    route = ModelRoute(primary="model-a", fallback="model-b")

    def primary(_: str, __: str) -> str:
        raise RuntimeError("primary down")

    def fallback(_: str, __: str) -> str:
        return "fallback-output"

    model, output = synthesize_with_fallback(route, primary, fallback, "prompt")
    assert model == "model-b"
    assert output == "fallback-output"


def test_both_models_fail() -> None:
    route = ModelRoute(primary="model-a", fallback="model-b")

    def fail(_: str, __: str) -> str:
        raise RuntimeError("down")

    with pytest.raises(LlmRoutingError):
        synthesize_with_fallback(route, fail, fail, "prompt")
