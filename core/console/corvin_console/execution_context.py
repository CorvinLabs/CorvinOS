"""Execution Context Badge — tracking engine, model, and performance metadata.

Phase 1 foundation: capture execution context for every turn across all
engines (Claude Code, ACS, TDE, Hermes) and model sources (Anthropic, Ollama,
OpenRouter, Hermes local).

This module provides:
  - ExecutionContext dataclass: unified schema for all turn metadata
  - Model detection: identify source (claude, ollama, openrouter, hermes)
  - Engine detection: identify runtime (claude_code, acs, tde, hermes)
  - Delegation mode detection: native, acs, tde, fallback
  - Token counting and timing utilities

Spans: os_turn.start → os_turn.completed (ADR-0171 engine_span)
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class ModelSource(str, Enum):
    """Canonical model sources."""
    CLAUDE = "claude"           # Anthropic API models (claude-3-5-sonnet, etc.)
    OLLAMA = "ollama"          # Local Ollama HTTP (e.g. ollama:mistral)
    OPENROUTER = "openrouter"  # OpenRouter API routing (e.g. openrouter:mistral)
    HERMES = "hermes"          # Hermes local fallback engine
    UNKNOWN = "unknown"        # Unrecognized model source


class EngineId(str, Enum):
    """Canonical engine IDs."""
    CLAUDE_CODE = "claude_code"  # Direct claude subprocess (ADR-0037)
    ACS = "acs"                   # ACS delegation (ADR-0114, ADR-0201)
    TDE = "tde"                   # Tiered Delegation Engine (ADR-0222)
    HERMES = "hermes"             # Layer-22 WorkerEngine (Ollama local)
    UNKNOWN = "unknown"           # Unrecognized engine


class DelegationMode(str, Enum):
    """How was this turn delegated."""
    NATIVE = "native"     # Direct OS engine (claude_code or hermes)
    ACS = "acs"           # Delegated to ACS fan-out
    TDE = "tde"           # Delegated to Tiered Delegation Engine
    FALLBACK = "fallback" # Delegated but fell back to native


@dataclass
class ExecutionContext:
    """Unified execution context captured for every turn.

    Spans the entire turn lifecycle: start → completion. Persisted in
    message.metadata.execution_context for audit and frontend rendering.
    All fields are optional (graceful fallbacks on parse errors).

    Attributes:
        engine_id: EngineId — claude_code | acs | tde | hermes
        model_source: ModelSource — claude | ollama | openrouter | hermes
        model_name: str (normalized) — e.g. "claude-3-5-sonnet" or "ollama/mistral"

        # Delegation
        delegation_mode: DelegationMode — native | acs | tde | fallback
        acs_run_id: str | None — run UUID if delegated to ACS
        tde_router_decision: str | None — TDE router decision log if TDE used

        # Performance
        duration_ms: int — wall-clock milliseconds from turn.start to completed
        tokens_input: int | None — input tokens consumed
        tokens_output: int | None — output tokens produced

        # Tool usage
        tool_calls_count: int — number of tool invocations

        # Optional context
        tenant_id: str — for audit correlation
        turn_number: int — 0-indexed turn in session

        # Timestamps (ISO 8601 UTC)
        started_at: str | None — RFC 3339 timestamp
        completed_at: str | None — RFC 3339 timestamp

        # Audit
        exit_code: int — 0 (success) or non-zero (error)
    """

    # Required runtime context
    engine_id: EngineId = field(default=EngineId.UNKNOWN)
    model_source: ModelSource = field(default=ModelSource.UNKNOWN)
    model_name: str = field(default="")  # Normalized: "claude-*", "ollama/*", etc.

    # Delegation path
    delegation_mode: DelegationMode = field(default=DelegationMode.NATIVE)
    acs_run_id: Optional[str] = field(default=None)
    tde_router_decision: Optional[str] = field(default=None)

    # Performance metrics
    duration_ms: int = field(default=0)
    tokens_input: Optional[int] = field(default=None)
    tokens_output: Optional[int] = field(default=None)

    # Tool usage
    tool_calls_count: int = field(default=0)

    # Context
    tenant_id: str = field(default="")
    turn_number: int = field(default=-1)

    # Timestamps
    started_at: Optional[str] = field(default=None)
    completed_at: Optional[str] = field(default=None)

    # Result
    exit_code: int = field(default=0)

    # Extra metadata (extensible)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict for audit/storage."""
        return {
            "engine_id": self.engine_id.value,
            "model_source": self.model_source.value,
            "model_name": self.model_name,
            "delegation_mode": self.delegation_mode.value,
            "acs_run_id": self.acs_run_id,
            "tde_router_decision": self.tde_router_decision,
            "duration_ms": self.duration_ms,
            "tokens_input": self.tokens_input,
            "tokens_output": self.tokens_output,
            "tool_calls_count": self.tool_calls_count,
            "tenant_id": self.tenant_id,
            "turn_number": self.turn_number,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "exit_code": self.exit_code,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionContext:
        """Deserialize from JSON dict."""
        try:
            engine_id = EngineId(data.get("engine_id", "unknown"))
        except ValueError:
            engine_id = EngineId.UNKNOWN

        try:
            model_source = ModelSource(data.get("model_source", "unknown"))
        except ValueError:
            model_source = ModelSource.UNKNOWN

        try:
            delegation_mode = DelegationMode(data.get("delegation_mode", "native"))
        except ValueError:
            delegation_mode = DelegationMode.NATIVE

        return cls(
            engine_id=engine_id,
            model_source=model_source,
            model_name=data.get("model_name", ""),
            delegation_mode=delegation_mode,
            acs_run_id=data.get("acs_run_id"),
            tde_router_decision=data.get("tde_router_decision"),
            duration_ms=data.get("duration_ms", 0),
            tokens_input=data.get("tokens_input"),
            tokens_output=data.get("tokens_output"),
            tool_calls_count=data.get("tool_calls_count", 0),
            tenant_id=data.get("tenant_id", ""),
            turn_number=data.get("turn_number", -1),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            exit_code=data.get("exit_code", 0),
            extra=data.get("extra", {}),
        )


# ── Model Detection ────────────────────────────────────────────────────────

_CLAUDE_PATTERN = re.compile(r'^claude-', re.IGNORECASE)
_OLLAMA_PATTERN = re.compile(r'^ollama[:/]', re.IGNORECASE)
_OPENROUTER_PATTERN = re.compile(r'^openrouter[:/]', re.IGNORECASE)
_HERMES_PATTERN = re.compile(r'^hermes', re.IGNORECASE)


def detect_model_source(model_name: str) -> ModelSource:
    """Detect model source from model name.

    Normalizes naming conventions:
      - "claude-3-5-sonnet" → CLAUDE
      - "claude-3-5-sonnet-20241022" → CLAUDE
      - "ollama:mistral" → OLLAMA
      - "ollama/mistral:latest" → OLLAMA
      - "openrouter:meta-llama/llama-2" → OPENROUTER
      - "openrouter/meta-llama/llama-2" → OPENROUTER
      - "hermes-local-3" → HERMES

    Returns:
        ModelSource enum (CLAUDE | OLLAMA | OPENROUTER | HERMES | UNKNOWN)
    """
    if not model_name or not isinstance(model_name, str):
        return ModelSource.UNKNOWN

    name = model_name.strip()
    if not name:
        return ModelSource.UNKNOWN

    if _CLAUDE_PATTERN.match(name):
        return ModelSource.CLAUDE
    if _OLLAMA_PATTERN.match(name):
        return ModelSource.OLLAMA
    if _OPENROUTER_PATTERN.match(name):
        return ModelSource.OPENROUTER
    if _HERMES_PATTERN.match(name):
        return ModelSource.HERMES

    return ModelSource.UNKNOWN


def normalize_model_name(model_name: str, model_source: Optional[ModelSource] = None) -> str:
    """Normalize model name to canonical form.

    Ensures consistent representation:
      - "claude-3-5-sonnet-20241022" → "claude-3-5-sonnet"
      - "ollama:mistral" → "ollama/mistral"
      - "ollama:mistral:latest" → "ollama/mistral:latest"
      - "openrouter:meta-llama/llama-2" → "openrouter/meta-llama/llama-2"

    Args:
        model_name: Raw model name from runtime
        model_source: Detected source (speeds up normalization)

    Returns:
        Normalized model name
    """
    if not model_name:
        return ""

    if model_source is None:
        model_source = detect_model_source(model_name)

    name = model_name.strip()

    # Claude: strip timestamp suffix (e.g. "-20241022")
    if model_source == ModelSource.CLAUDE:
        # Remove date/version suffix: "claude-3-5-sonnet-20241022" → "claude-3-5-sonnet"
        match = re.match(r'(claude-[\w-]+?)(?:-\d{8})?$', name, re.IGNORECASE)
        if match:
            return match.group(1).lower()
        return name.lower()

    # Ollama: normalize separator to "/" and preserve tags
    if model_source == ModelSource.OLLAMA:
        # "ollama:mistral" → "ollama/mistral"
        # "ollama:mistral:latest" → "ollama/mistral:latest"
        # "ollama/mistral:latest" → "ollama/mistral:latest" (already normalized)
        # Only replace ':' if it comes BEFORE any '/' (prefix separator, not tag)
        if "/" in name:
            # Already has "/" separator — don't change colons (they're tags)
            normalized = name
        elif ":" in name:
            # Replace the first ':' with '/' (model name separator)
            prefix, rest = name.split(":", 1)
            normalized = f"{prefix}/{rest}"
        else:
            normalized = name
        return normalized.lower()

    # OpenRouter: normalize separator to "/"
    if model_source == ModelSource.OPENROUTER:
        # "openrouter:meta-llama/llama-2" → "openrouter/meta-llama/llama-2"
        parts = name.split(":", 1)
        if len(parts) == 2:
            normalized = f"{parts[0]}/{parts[1]}"
        else:
            normalized = name
        return normalized.lower()

    # Hermes: lowercase
    if model_source == ModelSource.HERMES:
        return name.lower()

    return name


# ── Engine Detection ───────────────────────────────────────────────────────

def detect_engine(runtime_state: dict[str, Any]) -> EngineId:
    """Detect which engine is running from runtime state.

    Args:
        runtime_state: Dict with engine/delegation context:
          - engine_id: "claude_code" | "hermes" | "acs" | "tde"
          - delegation_mode: "native" | "acs" | "tde"
          - spawn_via: "subprocess" | "worker" | "http"

    Returns:
        EngineId enum
    """
    if not runtime_state:
        return EngineId.UNKNOWN

    # Explicit engine_id in state
    engine_id = runtime_state.get("engine_id", "").lower()
    if engine_id in ("claude_code", "acs", "tde", "hermes"):
        return EngineId(engine_id)

    # Infer from spawn method
    spawn_via = runtime_state.get("spawn_via", "").lower()
    if spawn_via == "http":
        return EngineId.HERMES
    if spawn_via == "worker":
        return EngineId.ACS

    # Infer from delegation_mode
    delegation_mode = runtime_state.get("delegation_mode", "").lower()
    if delegation_mode == "acs":
        return EngineId.ACS
    if delegation_mode == "tde":
        return EngineId.TDE

    return EngineId.UNKNOWN


def detect_delegation_mode(runtime_state: dict[str, Any]) -> DelegationMode:
    """Detect delegation mode from runtime state.

    Args:
        runtime_state: Dict with delegation context:
          - delegation_enabled: bool
          - delegation_mode: "native" | "acs" | "tde"
          - acs_run_id: str (if ACS)
          - fallback_reason: str (if fell back)

    Returns:
        DelegationMode enum
    """
    if not runtime_state:
        return DelegationMode.NATIVE

    # Explicit delegation_mode
    mode = runtime_state.get("delegation_mode", "").lower()
    if mode in ("acs", "tde", "fallback"):
        return DelegationMode(mode)
    if mode == "native":
        return DelegationMode.NATIVE

    # Infer from presence of run IDs
    if runtime_state.get("acs_run_id"):
        return DelegationMode.ACS
    if runtime_state.get("tde_run_id"):
        return DelegationMode.TDE

    # Check for fallback flag
    if runtime_state.get("fallback_reason"):
        return DelegationMode.FALLBACK

    return DelegationMode.NATIVE


# ── Token Counting ────────────────────────────────────────────────────────

def extract_token_usage(usage_data: dict[str, Any] | None) -> tuple[int | None, int | None]:
    """Extract input/output tokens from API response.

    Supports multiple API formats:
      - Anthropic: {input_tokens, output_tokens}
      - OpenRouter: {prompt_tokens, completion_tokens}
      - Generic: {tokens_in, tokens_out} or {in, out}

    Args:
        usage_data: Usage dict from LLM response

    Returns:
        (input_tokens, output_tokens) — both None if parsing fails
    """
    if not usage_data:
        return None, None

    # Anthropic format
    input_tokens = usage_data.get("input_tokens")
    output_tokens = usage_data.get("output_tokens")
    if input_tokens is not None and output_tokens is not None:
        return int(input_tokens), int(output_tokens)

    # OpenRouter format
    input_tokens = usage_data.get("prompt_tokens")
    output_tokens = usage_data.get("completion_tokens")
    if input_tokens is not None and output_tokens is not None:
        return int(input_tokens), int(output_tokens)

    # Generic format
    input_tokens = usage_data.get("tokens_in") or usage_data.get("in")
    output_tokens = usage_data.get("tokens_out") or usage_data.get("out")
    if input_tokens is not None and output_tokens is not None:
        return int(input_tokens), int(output_tokens)

    return None, None


# ── Builder Utilities ──────────────────────────────────────────────────────

class ExecutionContextBuilder:
    """Builder for ExecutionContext — tracks turn lifecycle."""

    def __init__(self, tenant_id: str = "", turn_number: int = -1):
        """Initialize builder with session context.

        Args:
            tenant_id: Tenant ID for audit correlation
            turn_number: 0-indexed turn number in session
        """
        self._ctx = ExecutionContext(tenant_id=tenant_id, turn_number=turn_number)
        self._start_time: float | None = None

    def start(self, engine_id: str | EngineId = "", model_name: str = "") -> ExecutionContextBuilder:
        """Mark turn start and set engine/model."""
        self._start_time = time.time()
        self._ctx.started_at = _iso8601_now()

        # Set engine
        if isinstance(engine_id, EngineId):
            self._ctx.engine_id = engine_id
        elif engine_id:
            try:
                self._ctx.engine_id = EngineId(engine_id)
            except ValueError:
                self._ctx.engine_id = EngineId.UNKNOWN

        # Set model
        if model_name:
            self._ctx.model_name = normalize_model_name(model_name)
            self._ctx.model_source = detect_model_source(model_name)

        return self

    def set_delegation(
        self,
        mode: str | DelegationMode = "",
        acs_run_id: str | None = None,
        tde_router_decision: str | None = None,
    ) -> ExecutionContextBuilder:
        """Set delegation context."""
        if isinstance(mode, DelegationMode):
            self._ctx.delegation_mode = mode
        elif mode:
            try:
                self._ctx.delegation_mode = DelegationMode(mode)
            except ValueError:
                pass

        if acs_run_id:
            self._ctx.acs_run_id = acs_run_id
        if tde_router_decision:
            self._ctx.tde_router_decision = tde_router_decision

        return self

    def set_usage(self, usage_data: dict[str, Any] | None = None) -> ExecutionContextBuilder:
        """Set token usage from API response."""
        if usage_data:
            tokens_in, tokens_out = extract_token_usage(usage_data)
            self._ctx.tokens_input = tokens_in
            self._ctx.tokens_output = tokens_out
        return self

    def add_tool_call(self) -> ExecutionContextBuilder:
        """Increment tool call counter."""
        self._ctx.tool_calls_count += 1
        return self

    def set_exit_code(self, code: int) -> ExecutionContextBuilder:
        """Set final exit code (0 = success)."""
        self._ctx.exit_code = code
        return self

    def complete(self) -> ExecutionContext:
        """Mark turn complete and return context."""
        if self._start_time:
            elapsed = time.time() - self._start_time
            self._ctx.duration_ms = int(elapsed * 1000)
        self._ctx.completed_at = _iso8601_now()
        return self._ctx


# ── Timestamp Utilities ────────────────────────────────────────────────────

def _iso8601_now() -> str:
    """Return current time in ISO 8601 format (UTC)."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
