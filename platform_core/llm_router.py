from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


class LlmRoutingError(RuntimeError):
    pass


@dataclass
class ModelRoute:
    primary: str
    fallback: str


def synthesize_with_fallback(
    route: ModelRoute,
    primary_call: Callable[[str, str], str],
    fallback_call: Callable[[str, str], str],
    prompt: str,
) -> tuple[str, str]:
    try:
        return route.primary, primary_call(route.primary, prompt)
    except Exception:
        try:
            return route.fallback, fallback_call(route.fallback, prompt)
        except Exception as exc:
            raise LlmRoutingError("Both primary and fallback model calls failed") from exc
