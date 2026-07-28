"""Layer 29.5 Phase 3 — Adaptive OS-Turn Model Selector (ADR-0024).

Pure-stdlib module — NO anthropic / openai / SDK imports.
Resolves the OS-turn model from environment config and a payload-size
estimate. Used by adapter._resolve_os_model (Phase 29.5.3b).

Resolution order (managed in adapter._resolve_os_model):
  1. CORVIN_OS_MODEL_OVERRIDE   → operator-wide kill-switch
  2. profile.model               → explicit per-persona pin
  3. autoselect_os_model(chars) + apply_floor  → adaptive default
  4. None                        → CLI subscription default
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Final

# Logging is best-effort; the selector must stay pure-stdlib + cheap.
try:
    import sys as _sys
    _shared = os.path.dirname(os.path.abspath(__file__))
    if _shared not in _sys.path:
        _sys.path.insert(0, _shared)
    from debug_logging import get_logger as _corvin_get_logger  # type: ignore
    _mlog = _corvin_get_logger("model_selector")
except Exception:  # pragma: no cover
    import logging as _logging
    _mlog = _logging.getLogger("model_selector")
    _mlog.addHandler(_logging.NullHandler())

DEFAULT_LOW: Final[str] = "claude-haiku-4-5-20251001"
DEFAULT_HIGH: Final[str] = "claude-sonnet-5"
DEFAULT_THRESHOLD_CHARS: Final[int] = 60_000
_MIN_THRESHOLD: Final[int] = 20_000
_MAX_THRESHOLD: Final[int] = 200_000
_INTERNAL_OVERHEAD_CHARS: Final[int] = 10_000
SESSION_BYTES_CAP: Final[int] = 5 * 1024 * 1024  # 5 MB

# Curated context-overflow error patterns.
# Extend ONLY with a real error-string sample + E2E test case.
_CONTEXT_ERROR_PATTERNS: Final[tuple[str, ...]] = (
    "Autocompact is thrashing",
    "prompt is too long",
    "context_length_exceeded",
    "input length",
)

_MODEL_RANK: Final[dict[str, int]] = {
    "claude-haiku-4-5-20251001": 1,
    "claude-haiku-4-5": 1,
    "claude-sonnet-5": 2,
    "claude-sonnet-4-6": 2,
    "claude-opus-4-7": 3,
}

_FLOOR_TO_MODEL: Final[dict[str, str]] = {
    "haiku": DEFAULT_LOW,
    "sonnet": DEFAULT_HIGH,
    "opus": "claude-opus-4-7",
}

# Curated values for audit fields — changing these requires an ADR amendment.
_VALID_SELECTED_REASONS: Final[frozenset[str]] = frozenset({
    "override", "explicit", "autoselect_low", "autoselect_high",
    "floor", "estimate_failed",
})

_VALID_ESCALATED_REASONS: Final[frozenset[str]] = frozenset({
    "autocompact-thrash", "context-overflow", "http-400",
})

_ALLOWED_FIELDS_SELECTED: Final[frozenset[str]] = frozenset({
    "persona", "channel", "estimate_chars", "chosen", "reason",
})

_ALLOWED_FIELDS_ESCALATED: Final[frozenset[str]] = frozenset({
    "persona", "channel", "from", "to", "reason",
})

_FORBIDDEN_FIELDS: Final[frozenset[str]] = frozenset({
    "prompt", "prompt_text", "system_prompt", "system_prompt_text",
    "body", "payload", "final_text",
})


class OsModelAuditFieldNotAllowed(ValueError):
    """Raised when a forbidden or off-allowlist field is smuggled into an audit event."""


# ---------------------------------------------------------------------------
# Estimator
# ---------------------------------------------------------------------------

def _session_history_bytes(session_dir: Path | None) -> int:
    """Sum file sizes under session_dir recursively, capped at SESSION_BYTES_CAP.

    Best-effort: permission errors / missing files → 0, never raises.
    Early-exit at cap — no point counting more once Haiku is ruled out.
    """
    if session_dir is None or not session_dir.exists():
        return 0
    total = 0
    try:
        for path in session_dir.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
                if total >= SESSION_BYTES_CAP:
                    return SESSION_BYTES_CAP
    except OSError:
        return 0
    return min(total, SESSION_BYTES_CAP)


def estimate_os_turn_chars(
    prompt: str,
    system_prompt: str,
    mcp_config_text: str = "",
    session_dir: Path | None = None,
) -> int:
    """Estimate total initial-context chars for the upcoming OS turn.

    RAM-pass-through for mcp_config_text (caller builds the string, no
    extra disk-IO here). Adds a fixed _INTERNAL_OVERHEAD_CHARS for
    claude-internal preamble + tool boilerplate.
    """
    return (
        len(prompt)
        + len(system_prompt)
        + len(mcp_config_text)
        + _session_history_bytes(session_dir)
        + _INTERNAL_OVERHEAD_CHARS
    )


# ---------------------------------------------------------------------------
# Core selectors
# ---------------------------------------------------------------------------

def autoselect_os_model(
    payload_chars: int,
    *,
    threshold: int | None = None,
    low: str | None = None,
    high: str | None = None,
) -> str:
    """Return HIGH (Sonnet) by default. Only return LOW (Haiku) if CORVIN_OS_MODEL_ALLOW_HAIKU=1."""
    hi = high if high is not None else high_model()
    lo = low if low is not None else low_model()

    # Default to HIGH (Sonnet) unless user explicitly enables Haiku downgrade
    if not haiku_downgrade_allowed():
        _mlog.debug(
            "autoselect: returning HIGH (Sonnet) by default. "
            "Set CORVIN_OS_MODEL_ALLOW_HAIKU=1 to allow Haiku downgrade. chars=%d",
            payload_chars,
        )
        return hi

    # User explicitly enabled Haiku; use adaptive selection
    t = threshold if threshold is not None else threshold_chars()
    pick = lo if payload_chars <= t else hi
    _mlog.debug(
        "autoselect chars=%d threshold=%d → %s (low=%s high=%s)",
        payload_chars, t, pick, lo, hi,
    )
    return pick


def apply_floor(chosen: str, floor: str | None) -> str:
    """Upgrade chosen to the floor model when chosen's rank is below floor's.

    floor: shorthand ("haiku", "sonnet", "opus") or None/empty.
    Never downgrades — if chosen is already above floor, chosen wins.
    """
    if not floor:
        return chosen
    floor_model_id = _FLOOR_TO_MODEL.get((floor or "").lower())
    if floor_model_id is None:
        return chosen
    floor_rank = _MODEL_RANK.get(floor_model_id, 0)
    chosen_rank = _MODEL_RANK.get(chosen, 0)
    if chosen_rank < floor_rank:
        return floor_model_id
    return chosen


def escalate_for_error(error_text: str, *, current: str | None) -> str | None:
    """Return the escalation target model, or None if no escalation.

    Returns None when:
    - current is already HIGH (max 1 retry per turn)
    - error_text is not a context-overflow pattern
    - retry_on_thrash_enabled() is False
    """
    if not retry_on_thrash_enabled():
        return None
    if not is_context_error(error_text):
        return None
    hi = high_model()
    if current == hi:
        return None
    return hi


# ---------------------------------------------------------------------------
# Env-driven config helpers
# ---------------------------------------------------------------------------

def autoselect_enabled() -> bool:
    """True unless CORVIN_OS_MODEL_AUTOSELECT is "off" / "0" / "false"."""
    val = os.environ.get("CORVIN_OS_MODEL_AUTOSELECT", "on").strip().lower()
    return val not in {"off", "0", "false", "no"}


def threshold_chars() -> int:
    """Return threshold from env, clamped to [_MIN_THRESHOLD, _MAX_THRESHOLD]."""
    raw = os.environ.get("CORVIN_OS_MODEL_THRESHOLD_CHARS", "")
    try:
        val = int(raw)
    except (ValueError, TypeError):
        return DEFAULT_THRESHOLD_CHARS
    return max(_MIN_THRESHOLD, min(_MAX_THRESHOLD, val))


def low_model() -> str:
    """Low-tier model id (default Haiku 4.5)."""
    val = os.environ.get("CORVIN_OS_MODEL_LOW", "").strip()
    return val if val else DEFAULT_LOW


def high_model() -> str:
    """High-tier model id (default Sonnet 4.6)."""
    val = os.environ.get("CORVIN_OS_MODEL_HIGH", "").strip()
    return val if val else DEFAULT_HIGH


def os_model_override() -> str | None:
    """Tier-1 kill-switch: CORVIN_OS_MODEL_OVERRIDE → model id or None."""
    val = os.environ.get("CORVIN_OS_MODEL_OVERRIDE", "").strip()
    return val if val else None


def retry_on_thrash_enabled() -> bool:
    """True unless CORVIN_OS_MODEL_RETRY_ON_THRASH is "off" / "0"."""
    val = os.environ.get("CORVIN_OS_MODEL_RETRY_ON_THRASH", "on").strip().lower()
    return val not in {"off", "0", "false", "no"}


def haiku_downgrade_allowed() -> bool:
    """True only if CORVIN_OS_MODEL_ALLOW_HAIKU is explicitly "1", "true", "yes", or "on".

    Default: False. Sonnet is always the default unless explicitly opted out.
    """
    val = os.environ.get("CORVIN_OS_MODEL_ALLOW_HAIKU", "").strip().lower()
    return val in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

def is_context_error(error_text: str) -> bool:
    """True if error_text matches a curated context-overflow pattern."""
    if not isinstance(error_text, str) or not error_text:
        return False
    lower = error_text.lower()
    return any(pat.lower() in lower for pat in _CONTEXT_ERROR_PATTERNS)


def classify_error_reason(error_text: str) -> str:
    """Map error_text to a curated escalation reason for the audit event."""
    lower = (error_text or "").lower()
    if "autocompact" in lower:
        return "autocompact-thrash"
    if "400" in lower:
        return "http-400"
    return "context-overflow"


# ---------------------------------------------------------------------------
# Transient HTTP-error classification (ADR-0024 follow-up)
# ---------------------------------------------------------------------------

# HTTP status codes that warrant a session-reset + single retry when the
# claude CLI surfaces them on stream-json. The 400 entry is the load-
# bearing one: Anthropic returns 400 on tool_use/tool_result mismatches,
# empty text blocks, and oversized prompts — all of which clear on a
# fresh `--continue`-less session. Server-side 5xx and 408/429 are
# classic retry-worthy transients.
_TRANSIENT_HTTP_CODES: Final[frozenset[str]] = frozenset({
    "400", "404", "408", "409", "429",
    "500", "502", "503", "504", "529",
})

# Symbolic tokens the Anthropic SDK / claude CLI sometimes surfaces
# instead of a numeric status — match these case-insensitively as well.
_TRANSIENT_HTTP_TOKENS: Final[tuple[str, ...]] = (
    "rate_limit", "rate_limited", "rate-limited",
    "overloaded_error", "overloaded",
    "internal_server_error", "service_unavailable",
    "api_error_status", "request_too_large",
    # Connection-level failures (local network / DNS outage — the request
    # never reached the API). A short network blip mid-turn otherwise kills
    # the whole turn with zero retries (incident 2026-07-10: hotspot drop →
    # "Unable to connect to API (ConnectionRefused)" ended the session turn).
    # These are NOT in _SESSION_CORRUPTING_TOKENS: the on-disk session state
    # is intact, so the retry keeps context (no wipe).
    "unable to connect", "connection refused", "connectionrefused",
    "connection error", "connection reset", "connection timed out",
    "getaddrinfo", "enotfound", "eai_again", "econnrefused",
    "econnreset", "etimedout", "enetunreach",
    "network is unreachable", "name or service not known",
)

# Codes / tokens that indicate the --continue / --resume session state may be
# corrupted (wipe local .claude state + retry fresh).
# 400 / api_error_status: API rejected the session continuation.
# 404: `claude --resume <id>` for a session the server no longer has (expired /
#      too large / evicted) returns a naked 404 — the on-disk session id is
#      stale, so wiping it and starting fresh is the correct recovery. Without
#      this the bare "404" was propagated straight to the user (e.g. Discord).
# Pure transients (429, 5xx, rate-limit tokens) should NOT wipe the session.
_SESSION_CORRUPTING_CODES: Final[frozenset[str]] = frozenset({"400", "404"})
_SESSION_CORRUPTING_TOKENS: Final[tuple[str, ...]] = ("api_error_status",)

import re as _re  # local import; module already pure-stdlib

_HTTP_CODE_RE: Final = _re.compile(r"\b(\d{3})\b")
_RETRY_AFTER_RE: Final = _re.compile(
    r"(?:retry[- ]?after|retry in|wait)[: ]?\s*(\d+)\s*(?:s|sec|second)?",
    _re.IGNORECASE,
)


def is_transient_http_error(error_text: str) -> bool:
    """True when error_text looks like a transient/reset-worthy HTTP error.

    Curated allowlist of numeric codes (`_TRANSIENT_HTTP_CODES`) plus
    symbolic Anthropic tokens (`_TRANSIENT_HTTP_TOKENS`). Used by the
    adapter's `should_reset` gate to recover from upstream API failures
    that strand a `--continue` session in a half-broken state.

    Specifically covers the case `error_text == "400"` (HTTP status
    only, no body — surfaced when the CLI sees `api_error_status: 400`
    without further detail). See `docs/claude-ref/layer-engines.md`.
    """
    if not isinstance(error_text, str) or not error_text:
        return False
    lower = error_text.lower()
    for tok in _TRANSIENT_HTTP_TOKENS:
        if tok in lower:
            _mlog.debug("transient: token=%r matched in %r", tok, error_text[:120])
            return True
    for match in _HTTP_CODE_RE.finditer(error_text):
        if match.group(1) in _TRANSIENT_HTTP_CODES:
            _mlog.debug(
                "transient: status=%s matched in %r",
                match.group(1), error_text[:120],
            )
            return True
    return False


def is_session_corrupting_http_error(error_text: str) -> bool:
    """True for HTTP errors that indicate a broken --continue session.

    400 / api_error_status: the API rejected the session continuation —
    wiping .claude.json and retrying fresh is the right recovery.
    429 / 5xx / symbolic rate-limit tokens: transient API pressure — the
    local conversation state is still valid, preserve it on retry.
    """
    if not isinstance(error_text, str) or not error_text:
        return False
    lower = error_text.lower()
    for tok in _SESSION_CORRUPTING_TOKENS:
        if tok in lower:
            return True
    for match in _HTTP_CODE_RE.finditer(error_text):
        if match.group(1) in _SESSION_CORRUPTING_CODES:
            return True
    return False


def parse_retry_after_seconds(
    error_text: str, *, default: int | None = None,
    min_seconds: int = 5, max_seconds: int = 120,
) -> int | None:
    """Extract a Retry-After hint from error_text, clamped to [min, max].

    Anthropic occasionally surfaces a `retry-after: N` token in the
    stream-json error body. When present, callers should sleep that
    long before retrying. Returns `default` when no hint is found.
    """
    if isinstance(error_text, str) and error_text:
        m = _RETRY_AFTER_RE.search(error_text)
        if m:
            try:
                val = int(m.group(1))
            except (TypeError, ValueError):
                val = -1
            if val > 0:
                return max(min_seconds, min(max_seconds, val))
    if default is None:
        return None
    return max(min_seconds, min(max_seconds, int(default)))


def _model_to_curated(model_id: str) -> str:
    """Map full model id to curated label (haiku / sonnet / opus / other)."""
    lo = (model_id or "").lower()
    if "haiku" in lo:
        return "haiku"
    if "sonnet" in lo:
        return "sonnet"
    if "opus" in lo:
        return "opus"
    return "other"


# ---------------------------------------------------------------------------
# Audit emitters
# ---------------------------------------------------------------------------

def _validate_details(
    details: dict,
    allowed: frozenset[str],
    event_type: str,
) -> None:
    for key in details:
        if key in _FORBIDDEN_FIELDS:
            raise OsModelAuditFieldNotAllowed(
                f"Forbidden field {key!r} in {event_type} audit event"
            )
        if key not in allowed:
            raise OsModelAuditFieldNotAllowed(
                f"Off-allowlist field {key!r} in {event_type} audit event "
                f"(allowed: {sorted(allowed)})"
            )


def emit_selected(
    *,
    persona: str,
    channel: str,
    estimate_chars: int,
    chosen: str,
    reason: str,
) -> None:
    """Emit os_model.selected into the unified audit chain (metadata only).

    Raises OsModelAuditFieldNotAllowed on invalid reason or forbidden field.
    Actual chain write is best-effort (never crashes the OS turn).
    """
    if reason not in _VALID_SELECTED_REASONS:
        raise OsModelAuditFieldNotAllowed(
            f"Unknown reason {reason!r} for os_model.selected "
            f"(valid: {sorted(_VALID_SELECTED_REASONS)})"
        )
    details: dict = {
        "persona": persona,
        "channel": channel,
        "estimate_chars": estimate_chars,
        "chosen": _model_to_curated(chosen),
        "reason": reason,
    }
    _validate_details(details, _ALLOWED_FIELDS_SELECTED, "os_model.selected")
    _write_event("os_model.selected", details)


def emit_escalated(
    *,
    persona: str,
    channel: str,
    from_model: str,
    to_model: str,
    reason: str,
) -> None:
    """Emit os_model.escalated into the unified audit chain (metadata only).

    Raises OsModelAuditFieldNotAllowed on invalid reason or forbidden field.
    """
    if reason not in _VALID_ESCALATED_REASONS:
        raise OsModelAuditFieldNotAllowed(
            f"Unknown reason {reason!r} for os_model.escalated "
            f"(valid: {sorted(_VALID_ESCALATED_REASONS)})"
        )
    details: dict = {
        "persona": persona,
        "channel": channel,
        "from": _model_to_curated(from_model),
        "to": _model_to_curated(to_model),
        "reason": reason,
    }
    _validate_details(details, _ALLOWED_FIELDS_ESCALATED, "os_model.escalated")
    _write_event("os_model.escalated", details)


def _write_event(event_type: str, details: dict) -> None:
    """Best-effort write to the unified forge audit chain."""
    try:
        try:
            from forge.security_events import write_event  # type: ignore
        except ImportError:
            try:
                import sys as _sys
                import os as _os
                _forge_path = _os.path.join(
                    _os.path.dirname(__file__),
                    "..", "..", "..", "forge", "forge",
                )
                if _forge_path not in _sys.path:
                    _sys.path.insert(0, _forge_path)
                from security_events import write_event  # type: ignore  # noqa: F811
            except ImportError:
                return
        # write_event's first positional arg is the chain PATH, not the event
        # type. Resolve the canonical chain path via the bridge audit resolver
        # (the prior `write_event(event_type, details)` call had path/event_type
        # transposed → it never reached the chain and was swallowed below).
        try:
            from audit import audit_path  # type: ignore
        except ImportError:
            from .audit import audit_path  # type: ignore
        write_event(audit_path(), event_type, details=details)
    except Exception:  # noqa: BLE001
        pass  # audit is observability; never crash the OS turn


# ── ADR-0119/0123/0251 — the single OS-model resolver for BOTH surfaces ───────


def resolve_os_model(
    profile: dict | None,
    *,
    payload_chars: int = 0,
    engine_id: str = "claude_code",
    tenant_id: str = "_default",
    workload_hint: dict | None = None,
    chat_key: str | None = None,
    audit_fn=None,
) -> str | None:
    """Layer 29.5 Phase 3 (ADR-0024) / ADR-0119 / ADR-0123 — 6-Tier adaptive OS model selection.

    THE single source of truth for OS-turn model resolution. Both the console
    web-chat (``chat_runtime.py``) and the bridge adapter (``adapter.py``) call
    this SAME function so the two surfaces cannot silently diverge again:
    before this, ``chat_runtime.py`` hand-rolled only Tier 1 + Tier 3 and
    never consulted Tier 2.5 at all — the console's own "OS Model" setting
    under Settings → AI Engines had no effect on the console's own chat.

    Resolution order (top wins):
      1.   CORVIN_OS_MODEL_OVERRIDE env                              → operator-wide kill-switch
      2.   profile.model                                             → explicit per-persona/profile pin
      1.5. profile._persona_os_model                                → per-persona pin (ADR-0123)
      2.5. spec.engine_models.<engine_id>.os_model in tenant YAML   → per-engine tenant default (ADR-0119)
      2.7. ADR-0043 workload classification (CHAT fast-path only)    → opt-in tenant feature flag
      3.   autoselect(payload_chars) + floor                         → adaptive (default path)
      4.   None                                                      → CLI subscription default

    ``profile`` is optional — a caller with no persona/profile concept (the
    console) passes ``None``; Tiers 2 and 1.5 then simply no-op and fall
    through to Tier 2.5, which is the tier that matters for parity.

    ``workload_hint`` / ``chat_key`` / ``audit_fn`` are ADR-0043 bridge
    concerns: a caller that never passes ``workload_hint`` never reaches
    Tier 2.7 and never needs to supply ``audit_fn``.

    When ``CORVIN_OS_MODEL_AUTOSELECT=off``, Tier 3 is skipped → Tier 4
    (``None``). On estimate failure, Tier 3 returns HIGH (Sonnet) as a safe
    default — never silently picks LOW on an unknown payload size.
    """
    # Tier 1 — operator-wide kill-switch (wins even over explicit model:)
    override = os_model_override()
    if override:
        return override

    # Tier 2 — explicit per-persona / per-chat-profile pin
    profile = profile or {}
    explicit = profile.get("model")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    # Tier 1.5 — per-persona JSON pin (ADR-0123); injected by call_claude_streaming
    persona_os_model = profile.get("_persona_os_model")
    if isinstance(persona_os_model, str) and persona_os_model.strip():
        return persona_os_model.strip()

    # Tier 2.5 — per-engine tenant default (ADR-0119)
    try:
        from engine_models import get_tenant_engine_model  # type: ignore  # noqa: PLC0415,I001
    except ImportError:
        try:
            from .engine_models import get_tenant_engine_model  # type: ignore  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            get_tenant_engine_model = None  # type: ignore
    if get_tenant_engine_model is not None:
        try:
            tenant_os_model = get_tenant_engine_model(tenant_id, engine_id, "os_model")
            if tenant_os_model:
                return tenant_os_model
        except Exception:  # noqa: BLE001
            pass

    # Tier 2.7 — ADR-0043 workload classification (hybrid CHAT/CODE routing).
    # The hint arrives as a PARAMETER threaded from the caller's request
    # handler, NOT via env vars: os.environ is process-global in a daemon
    # serving parallel chats/tenants, so an env channel is both racy and a
    # cross-tenant leak by construction (adversarial review 2026-07-18).
    # Only the CHAT fast-path acts here. CODE/UNCERTAIN fall through to the
    # existing tiers (adaptive Tier 3 / subscription Tier 4) — ADR-0043 says
    # "respect the user's model choice", and hard-pinning the full tier for
    # CODE would bypass ADR-0112 adaptive selection.
    if workload_hint:
        try:
            from engine_models import _load_tenant_spec, resolve_model_for_workload  # type: ignore  # noqa: PLC0415,I001,I001
        except ImportError:
            try:
                from .engine_models import _load_tenant_spec, resolve_model_for_workload  # type: ignore  # noqa: PLC0415,I001
            except Exception:  # noqa: BLE001
                _load_tenant_spec = None  # type: ignore
                resolve_model_for_workload = None  # type: ignore
        if resolve_model_for_workload is not None:
            try:
                workload_class = str(workload_hint.get("workload") or "")
                if workload_class == "chat":
                    # Feature flag: (1) profile, (2) tenant YAML
                    # spec.features.fast_chat_mode. Deliberately NO env-var
                    # switch: a process-wide env flag would enable the
                    # feature across all tenants (CLAUDE.md multi-tenant
                    # rule) and bypass the operator's opt-in. Default false
                    # → fail-closed.
                    fast_chat_enabled = bool(profile.get("fast_chat_mode", False))
                    if not fast_chat_enabled and _load_tenant_spec is not None:
                        try:
                            tenant_spec = _load_tenant_spec(tenant_id)
                            fast_chat_enabled = bool(
                                (tenant_spec.get("features") or {}).get("fast_chat_mode", False))
                        except Exception:  # noqa: BLE001
                            pass
                    if fast_chat_enabled:
                        try:
                            conf = float(workload_hint.get("confidence", 0.0))
                        except (ValueError, TypeError):
                            conf = 0.0
                        model = resolve_model_for_workload(
                            engine_id,
                            workload_type=workload_class,
                            user_chosen_model=None,  # explicit pin already returned at Tier 2
                            confidence=conf,
                            fast_chat_enabled=True,
                        )
                        if model:
                            # ADR-0043 §6: audit every routing decision (BUG#15).
                            # No user-message content — workload/confidence/model only.
                            if audit_fn is not None:
                                try:
                                    audit_fn(
                                        "bridge.workload_model_selection",
                                        chat_key=chat_key or "unknown",
                                        details={
                                            "workload_type": workload_class,
                                            "confidence": conf,
                                            "selected_model": model,
                                            "engine": engine_id,
                                            "tier": "2.7_workload",
                                        },
                                    )
                                except Exception:  # noqa: BLE001
                                    pass  # audit failure is non-fatal
                            return model
            except Exception:  # noqa: BLE001
                pass  # Tier 2.7 routing failure is non-fatal; fall through to Tier 3

    # Tier 3 — adaptive autoselect (the new default path)
    if autoselect_enabled():
        try:
            chosen = autoselect_os_model(payload_chars)
            floor = profile.get("os_model_floor")
            chosen = apply_floor(chosen, floor)
            return chosen
        except Exception:  # noqa: BLE001
            # estimate_failed → safe-default HIGH (never silently pick LOW)
            return high_model()

    # Tier 4 — fallthrough to CLI subscription default
    return None


# ── ADR-0251 — the `engine.model_selection` call site ─────────────────────────


def resolve_step_model(
    bundled: str | None,
    *,
    engine_id: str,
    tenant_id: str = "_default",
    request: dict | None = None,
) -> str | None:
    """Offer the resolved model to an ``engine.model_selection`` hook.

    Lives here rather than at each surface's own resolution block for the same
    reason ``delegation_policy`` owns the engine rule: the console and the bridge
    adapter each compute the bundled answer their own way, but the decision about
    what a PLUGIN may do to that answer must be identical on both, or the first
    divergence is silent.

    **The bound a hook must meet (ADR-0251 D2).** The operator's setting bounds
    the answer; for models that setting is the engine registry. A hook may name
    any model the operator has actually installed for ``engine_id`` and nothing
    else — checked with :func:`engine_models.model_is_registered`, the same
    fail-closed rule the bundled tier resolution already applies to itself.

    Note what this deliberately does NOT mirror from the engine point. There is
    no ``native``-style floor to de-escalate to and no ordering among models, so
    "may not escalate" has no meaning here; registry membership is the whole
    bound. In particular a hook MAY name a model when ``bundled`` is ``None``
    (the CLI-subscription default): the core expressed no preference, and a
    registered model is inside the operator's own configuration either way.

    Returns ``bundled`` unchanged when the flag is off, when no hook is
    registered, when the hook abstains, or when its answer is refused.
    """
    try:
        from corvin_plugins import extension_points as _ep  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        # No plugin package in this install. The bundled answer IS the
        # pre-feature behaviour, so this is a quiet path, not a degradation.
        return bundled

    chosen = _ep.invoke(
        "engine.model_selection",
        dict(request or {}, bundled=bundled, engine_id=engine_id),
        default=bundled,
        tenant_id=tenant_id,
    )
    if chosen == bundled:
        return bundled

    # The admissibility check is fail-closed HERE, not only inside
    # `model_is_registered`. That function already swallows its own errors, but
    # relying on that would put the guarantee one import away from this decision:
    # if it is ever refactored to raise, or the import itself fails, the choice
    # must still be "keep the bundled answer", never "let a plugin's model
    # through because the check broke". Both call sites also wrap this — and one
    # of them (the console) turns an escaping exception into `model = None`,
    # which would silently DOWNGRADE the turn rather than preserve it.
    try:
        try:
            from engine_models import model_is_registered  # noqa: PLC0415
        except ImportError:  # pragma: no cover - layout fallback
            from .engine_models import model_is_registered  # type: ignore
        admissible = model_is_registered(chosen, engine_id)
    except Exception:  # noqa: BLE001
        admissible = False

    if admissible:
        return chosen

    _write_event("plugin.extension_model_refused", {
        "point": "engine.model_selection",
        "tenant_id": tenant_id,
        "engine_id": engine_id,
        # Model ids are operator-configured identifiers, not user content, and
        # the refused one is the entire content of this event. But it came from
        # a plugin and could be anything, so it is length-capped before it
        # reaches an append-only chain.
        "refused": str(chosen)[:64],
        "reason": "not_in_engine_registry",
    })
    return bundled
