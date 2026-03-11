from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Callable

from litellm import completion


class LlmRoutingError(RuntimeError):
    pass


@dataclass
class ModelRoute:
    primary: str
    fallback: str
    key_ref: str | None = None


def resolve_model_alias(model: str) -> str:
    requested = model.strip()
    if not requested:
        raise LlmRoutingError("Model identifier is empty")

    alias_env = {
        "codex": "RCA_MODEL_ALIAS_CODEX",
        "claude": "RCA_MODEL_ALIAS_CLAUDE",
    }
    alias_key = alias_env.get(requested.lower())
    if not alias_key:
        return requested

    resolved = (os.getenv(alias_key) or "").strip()
    if not resolved:
        raise LlmRoutingError(f"Model alias '{requested}' unresolved. Set {alias_key}.")
    return resolved


def resolve_model_route(route: ModelRoute) -> tuple[str, str]:
    return resolve_model_alias(route.primary), resolve_model_alias(route.fallback)


def synthesize_with_fallback(
    route: ModelRoute,
    primary_call: Callable[[str, str], str],
    fallback_call: Callable[[str, str], str],
    prompt: str,
) -> tuple[str, str]:
    try:
        return route.primary, primary_call(route.primary, prompt)
    except Exception as primary_exc:
        try:
            return route.fallback, fallback_call(route.fallback, prompt)
        except Exception as fallback_exc:
            raise LlmRoutingError(
                f"Both primary and fallback model calls failed "
                f"(primary={route.primary}: {primary_exc}; fallback={route.fallback}: {fallback_exc})"
            ) from fallback_exc


def _extract_text(response: Any) -> str:
    choices = None
    if isinstance(response, dict):
        choices = response.get("choices")
    else:
        choices = getattr(response, "choices", None)

    if not choices:
        raise RuntimeError("LLM response has no choices")

    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else getattr(first, "message", None)
    if isinstance(message, dict):
        content = message.get("content")
    else:
        content = getattr(message, "content", None)

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        text_chunks: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    text_chunks.append(text)
        if text_chunks:
            return "\n".join(text_chunks)

    if isinstance(first, dict):
        text = first.get("text")
        if isinstance(text, str):
            return text

    raise RuntimeError("Unable to extract text from LLM response")


def _resolve_api_key(route: ModelRoute) -> str | None:
    if route.key_ref:
        value = os.getenv(route.key_ref)
        if value:
            return value
    return os.getenv("LITELLM_API_KEY")


def _api_base() -> str | None:
    return os.getenv("LITELLM_BASE_URL") or os.getenv("LLM_API_BASE")


def _call_model(route: ModelRoute, model: str, prompt: str, system_prompt: str, max_tokens: int) -> str:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": float(os.getenv("LLM_TEMPERATURE", "0.1")),
        "max_tokens": max_tokens,
    }
    api_key = _resolve_api_key(route)
    if api_key:
        kwargs["api_key"] = api_key
    api_base = _api_base()
    if api_base:
        kwargs["api_base"] = api_base

    result = completion(**kwargs)
    text = _extract_text(result).strip()
    if not text:
        raise RuntimeError("LLM returned empty content")
    return text


def summarize_with_model_route(
    route: ModelRoute,
    prompt: str,
    *,
    system_prompt: str = "You are an RCA assistant.",
    max_tokens: int = 320,
) -> tuple[str, str]:
    resolved_primary, resolved_fallback = resolve_model_route(route)
    resolved_route = ModelRoute(primary=resolved_primary, fallback=resolved_fallback, key_ref=route.key_ref)

    def primary_call(model: str, prompt_text: str) -> str:
        return _call_model(resolved_route, model, prompt_text, system_prompt, max_tokens)

    def fallback_call(model: str, prompt_text: str) -> str:
        return _call_model(resolved_route, model, prompt_text, system_prompt, max_tokens)

    return synthesize_with_fallback(resolved_route, primary_call, fallback_call, prompt)
