"""LLM client resolution for Skill-Creator — Max-subscription first (ADR-0363).

Why this module exists
----------------------
Every Skill-Creator phase used to call ``anthropic.Anthropic()`` directly.
On an install without ``ANTHROPIC_API_KEY`` that constructor raises

    Could not resolve authentication method. Expected one of api_key,
    auth_token, or credentials to be set. …

which surfaced in the console as a FAILED run in the Planning phase.
CorvinOS installs authenticate through the **Claude Code CLI** (Max
subscription OAuth in ``~/.claude``), not through a raw API key, so the
default path must be the CLI — the same engine the console web-chat, ACS
runtime, TDE workers and the house-rules gate already drive.

Resolution order (``resolve_llm_client``):
  1. ``ClaudeCodeClient``  — ``claude -p --output-format json`` subprocess
     (Max subscription / whatever the CLI is logged in as). Default.
  2. ``anthropic.Anthropic`` — only when ``ANTHROPIC_API_KEY`` is set AND
     ``CORVIN_SKILL_CREATOR_ENGINE=api`` explicitly selects it.
  3. ``None``              — callers degrade to local template generation.

``ClaudeCodeClient`` is duck-typed against the slice of the Anthropic SDK
the Skill-Creator uses — ``client.messages.create(model=…, max_tokens=…,
messages=[…])`` returning an object with ``.content[0].text`` — so the
phase code and its unit-test mocks stay unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Engine selector: "claude_code" (default) | "api" | "local"
ENGINE_ENV = "CORVIN_SKILL_CREATOR_ENGINE"
MODEL_ENV = "CORVIN_SKILL_CREATOR_MODEL"
TIMEOUT_ENV = "CORVIN_SKILL_CREATOR_TIMEOUT_S"

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_TIMEOUT_S = 180.0

_BIN_FALLBACKS = (
    "/home/linuxbrew/.linuxbrew/bin/claude",
    "/usr/local/bin/claude",
    "/opt/homebrew/bin/claude",
)


class ClaudeCodeUnavailable(RuntimeError):
    """Raised when the `claude` CLI cannot be located or invoked."""


def resolve_claude_bin(binary: str | None = None) -> str:
    """Locate the `claude` binary. Honours CORVIN_CLAUDE_BIN (canonical env)."""
    candidate = binary or os.environ.get("CORVIN_CLAUDE_BIN") or "claude"
    if os.path.isabs(candidate) and os.access(candidate, os.X_OK):
        return candidate
    found = shutil.which(candidate)
    if found:
        return found
    # A service unit's PATH often lacks ~/.local/bin where the npm global
    # install lands; probe the usual locations before giving up.
    home_local = Path.home() / ".local" / "bin" / "claude"
    for path in (str(home_local), *_BIN_FALLBACKS):
        if os.access(path, os.X_OK):
            return path
    raise ClaudeCodeUnavailable(
        f"claude binary not found (tried {candidate!r}, PATH, ~/.local/bin, "
        f"{list(_BIN_FALLBACKS)}). Install Claude Code or set CORVIN_CLAUDE_BIN."
    )


# --------------------------------------------------------------------------
# Anthropic-SDK-shaped response objects (duck-typed)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class _TextBlock:
    text: str
    type: str = "text"


@dataclass(frozen=True)
class LLMResponse:
    """Minimal stand-in for ``anthropic.types.Message``."""
    content: list[_TextBlock]
    model: str = DEFAULT_MODEL
    usage: dict[str, Any] = field(default_factory=dict)
    stop_reason: str | None = None

    @property
    def text(self) -> str:
        return self.content[0].text if self.content else ""


class _Messages:
    """The ``client.messages`` namespace."""

    def __init__(self, client: "ClaudeCodeClient") -> None:
        self._client = client

    def create(self, *, model: str | None = None, max_tokens: int | None = None,
               messages: list[dict[str, Any]] | None = None,
               system: str | None = None, **_ignored: Any) -> LLMResponse:
        return self._client._create(
            model=model, max_tokens=max_tokens, messages=messages or [], system=system
        )


class ClaudeCodeClient:
    """Anthropic-SDK-shaped client backed by the `claude -p` CLI.

    Uses the operator's Claude Code login (Max subscription) — no API key.
    Each call is a single-turn, tool-free completion in a neutral scratch
    cwd, so a skill-generation prompt can never touch the repo.
    """

    engine_id = "claude_code"

    def __init__(self, *, binary: str | None = None, model: str | None = None,
                 timeout_s: float | None = None) -> None:
        self.binary = resolve_claude_bin(binary)
        self.model = model or os.environ.get(MODEL_ENV) or DEFAULT_MODEL
        if timeout_s is None:
            try:
                timeout_s = float(os.environ.get(TIMEOUT_ENV) or DEFAULT_TIMEOUT_S)
            except (TypeError, ValueError):
                timeout_s = DEFAULT_TIMEOUT_S
        self.timeout_s = timeout_s
        self.messages = _Messages(self)

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _flatten(messages: list[dict[str, Any]]) -> str:
        """Collapse an Anthropic ``messages`` list into one prompt string."""
        parts: list[str] = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                content = "\n".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            role = msg.get("role", "user")
            parts.append(str(content) if role == "user" else f"[{role}]\n{content}")
        return "\n\n".join(p for p in parts if p)

    def _create(self, *, model: str | None, max_tokens: int | None,
                messages: list[dict[str, Any]], system: str | None) -> LLMResponse:
        prompt = self._flatten(messages)
        if not prompt.strip():
            raise ValueError("empty prompt")

        argv = [
            self.binary, "-p", prompt,
            "--model", model or self.model,
            "--output-format", "json",
            "--max-turns", "1",
            # Skill generation is pure text synthesis: no file access, no
            # shell, no MCP. Keeps a generated prompt from reaching the FS.
            "--disallowedTools", "*",
        ]
        if system:
            argv.extend(["--append-system-prompt", system])

        with tempfile.TemporaryDirectory(prefix="skill-creator-") as neutral_cwd:
            try:
                proc = subprocess.run(
                    argv,
                    # Without stdin=DEVNULL the child inherits the service's
                    # stdin and `claude -p` blocks ~3s polling it on EVERY
                    # call (see llm_synthesis.py for the same finding).
                    stdin=subprocess.DEVNULL,
                    cwd=neutral_cwd,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                )
            except subprocess.TimeoutExpired as exc:
                raise ClaudeCodeUnavailable(
                    f"claude CLI timed out after {self.timeout_s}s"
                ) from exc
            except OSError as exc:
                raise ClaudeCodeUnavailable(f"claude CLI could not be spawned: {exc}") from exc

        if proc.returncode != 0:
            tail = (proc.stderr or "").strip()[-500:]
            raise ClaudeCodeUnavailable(
                f"claude CLI exited {proc.returncode}: {tail or '(no stderr)'}"
            )

        return self._parse_envelope(proc.stdout, fallback_model=model or self.model)

    @staticmethod
    def _parse_envelope(stdout: str, *, fallback_model: str) -> LLMResponse:
        """Unwrap a `claude -p --output-format json` envelope.

        The CLI returns rc=0 with ``is_error: true`` for soft failures
        (max-turns hit, upstream API error). Those MUST surface as an
        exception — recording the error text as the phase's output would
        feed a bogus skill spec into the next phase.
        """
        raw = (stdout or "").strip()
        if not raw:
            raise ClaudeCodeUnavailable("claude CLI returned empty output")
        try:
            env = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            # Older CLI / plain-text reply: treat stdout as the answer.
            return LLMResponse(content=[_TextBlock(text=raw)], model=fallback_model)
        if not isinstance(env, dict):
            return LLMResponse(content=[_TextBlock(text=raw)], model=fallback_model)
        if env.get("is_error"):
            raise ClaudeCodeUnavailable(
                f"claude CLI error ({env.get('subtype') or 'unknown'}): "
                f"{str(env.get('result') or '')[:300]}"
            )
        text = env.get("result", "")
        if not isinstance(text, str):
            text = json.dumps(text)
        model = fallback_model
        model_usage = env.get("modelUsage")
        if isinstance(model_usage, dict) and model_usage:
            model = next(iter(model_usage))
        usage = env.get("usage") if isinstance(env.get("usage"), dict) else {}
        return LLMResponse(
            content=[_TextBlock(text=text)],
            model=model,
            usage=usage,
            stop_reason=env.get("stop_reason"),
        )


# --------------------------------------------------------------------------
# Factory
# --------------------------------------------------------------------------

def resolve_llm_client(explicit=None):
    """Return the LLM client the Skill-Creator should use.

    Args:
        explicit: caller-injected client (tests, DI) — returned unchanged.

    Returns:
        A client exposing ``.messages.create(...)``, or ``None`` when no
        engine is reachable (callers degrade to local template generation).
    """
    if explicit is not None:
        return explicit

    engine = (os.environ.get(ENGINE_ENV) or "claude_code").strip().lower()

    if engine == "local":
        logger.info("Skill-Creator engine=local (explicit) — template generation")
        return None

    if engine == "api":
        return _api_client() or _claude_code_client()

    # Default: Max subscription via the Claude Code CLI.
    return _claude_code_client() or _api_client()


def _claude_code_client():
    try:
        client = ClaudeCodeClient()
        logger.info(
            "Skill-Creator engine=claude_code (Max subscription) bin=%s model=%s",
            client.binary, client.model,
        )
        return client
    except ClaudeCodeUnavailable as exc:
        logger.warning("Claude Code CLI unavailable: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001 — never break generation on probe
        logger.warning("Claude Code client construction failed: %s", exc)
        return None


def _api_client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic  # noqa: PLC0415
        logger.info("Skill-Creator engine=api (ANTHROPIC_API_KEY)")
        return anthropic.Anthropic(api_key=api_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Anthropic API client construction failed: %s", exc)
        return None


def engine_id_of(client) -> str:
    """Human-readable engine id for status/audit surfaces.

    Always a str: the value is serialised into the run status, and a client
    object that exposes a non-string `engine_id` (a bare Mock, a proxy) made
    the whole status endpoint 500 on a run that had actually succeeded.
    """
    if client is None:
        return "local"
    engine = getattr(client, "engine_id", None)
    return engine if isinstance(engine, str) and engine else "api"
