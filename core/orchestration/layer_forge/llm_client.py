"""Which LLM the Layer Forge talks to (PLAN and REVIEW phases).

The other three Forges (skill, tool, plugin) run on the operator's configured engine — for
most installs the Claude subscription (Claude Code CLI), with no API key anywhere. Layer
Forge used to construct ``anthropic.Anthropic()`` unconditionally, so on exactly that install
PLAN and REVIEW failed with the SDK's "Could not resolve authentication method".

Rule: an explicit API credential in the environment wins (unchanged behaviour); otherwise the
configured engine client is used; if neither exists the caller gets a plain error naming both
ways out. Both clients expose ``messages.create(...)``; :func:`reply_text` reads either reply.
"""
from __future__ import annotations

import os
from typing import Any

_API_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


class NoLLMAvailable(RuntimeError):
    pass


def api_credential_present() -> bool:
    return any(os.environ.get(k, "").strip() for k in _API_ENV)


def get_client(*, timeout: float, max_retries: int) -> Any:
    import anthropic

    if api_credential_present():
        return anthropic.Anthropic(timeout=timeout, max_retries=max_retries)
    try:
        from corvin_operator.skill_creator.artifact_review import resolve_engine
        engine = resolve_engine()
    except Exception:  # noqa: BLE001 - an unreachable engine is reported below, not raised raw
        engine = None
    if engine is None:
        raise NoLLMAvailable(
            "no LLM is available: set ANTHROPIC_API_KEY, or sign in to the Claude Code CLI "
            "so the configured engine can be used")
    return engine


def model_for(client: Any, requested: str) -> str:
    """The engine client carries its own model; an API client takes the requested one."""
    model = getattr(client, "model", None)  # only the engine client carries a model name
    return model if isinstance(model, str) and model else requested


def reply_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text
    content = getattr(response, "content", None)
    if not content:
        return ""
    first = content[0]
    return first.text if hasattr(first, "text") else str(first)
