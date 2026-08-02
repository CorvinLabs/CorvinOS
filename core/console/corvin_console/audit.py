"""Console-UI audit emitters.

Thin wrappers over ``forge.security_events.write_event`` enforcing
the metadata-only rule. Every console event lands in the OWNER's
tenant chain at ``<tenant_home>/global/forge/audit.jsonl`` — the
console is single-tenant by construction.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[2]
_FORGE_PATH = _REPO / "operator" / "forge"
if str(_FORGE_PATH) not in sys.path:
    sys.path.insert(0, str(_FORGE_PATH))

from forge import paths as _forge_paths  # noqa: E402
from forge import security_events as _security_events  # noqa: E402


# L16 compliance: metadata-only allowlist blocks secrets, tokens, PII, exception details, paths
_FORBIDDEN_FIELDS = frozenset({
    "sid", "session_id", "cleartext_sid",
    "csrf_secret", "csrf",
    "token", "bearer_token", "cleartext_token",
    "password", "secret",
})

_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "console.session_started": frozenset({
        "tenant_id", "token_fingerprint", "sid_fingerprint",
        "user_agent_class",
    }),
    "console.session_ended": frozenset({
        "sid_fingerprint", "reason", "tenant_id",
    }),
    "console.session_denied": frozenset({
        "reason", "token_fingerprint", "user_agent_class",
    }),
    "console.action_performed": frozenset({
        "action", "target_kind", "target_id",
        "sid_fingerprint", "tenant_id",
        "run_id",          # optional — workflow run correlation ID (DSGVO Art. 30)
        "step_id",         # optional — checkpoint step that was approved (EU AI Act Art. 14)
        "trigger",         # optional — "manual" | "scheduled" | "api"
    }),
    "console.action_denied": frozenset({
        "action", "target_kind", "target_id",
        "sid_fingerprint", "tenant_id", "reason",
        "run_id",
    }),
    "console.action_failed": frozenset({
        "action", "target_kind", "target_id",
        "sid_fingerprint", "tenant_id", "reason",
    }),
    # ADR-0120 — engine auto-detection + onboarding
    "setup.engine_probe_run": frozenset({
        "found_count", "engine_ids",
    }),
    "setup.onboarding_complete": frozenset({
        "default_engine", "engine_count",
    }),
    # ADR-0131 — Agent Lifecycle Governance
    "agent.charter_created": frozenset({
        "agent_id", "kind", "scope", "tenant_id", "sid_fingerprint",
    }),
    "agent.charter_renewed": frozenset({
        "agent_id", "prior_version", "tenant_id", "sid_fingerprint",
    }),
    "agent.sign_off": frozenset({
        "agent_id", "signer_role", "scope_target", "tenant_id", "sid_fingerprint",
    }),
    "agent.sign_off_revoked": frozenset({
        "agent_id", "signer_role", "prior_scope", "tenant_id", "sid_fingerprint",
    }),
    "agent.compliance_check_failed": frozenset({
        "agent_id", "check_id", "tenant_id",
    }),
    "agent.review_pending": frozenset({
        "agent_id", "days_until_review", "tenant_id",
    }),
    "agent.review_overdue": frozenset({
        "agent_id", "days_overdue", "tenant_id",
    }),
    "agent.orphan_detected": frozenset({
        "agent_id", "missing_role", "tenant_id",
    }),
    "agent.pending_sunset": frozenset({
        "agent_id", "days_until_sunset", "tenant_id",
    }),
    "agent.sunset": frozenset({
        "agent_id", "kind", "final_scope", "tenant_id",
    }),
    "agent.charter_single_owner_exception": frozenset({
        "agent_id", "tenant_id",
    }),
    "agent.session_start_blocked": frozenset({
        "agent_id", "reason", "tenant_id", "sid_fingerprint",
    }),
    # Phase 2b — L16 Audit Chain Integration (ADR-0248)
    "console.execution_context": frozenset({
        "turn_id", "session_id", "tenant_id",
        "engine_id", "model_source", "model_name",
        "delegation_mode", "acs_run_id", "tde_router_decision",
        "duration_ms", "tokens_input", "tokens_output",
        "tool_calls_count", "started_at", "completed_at", "exit_code",
        "turn_number",  # optional: turn index in session
    }),
}


class AuditFieldNotAllowed(Exception):
    """A detail key is on the forbidden or off-allowlist list."""


def classify_user_agent(ua: str | None) -> str:
    if not ua or not isinstance(ua, str):
        return "unknown"
    lo = ua.lower()
    if lo.startswith("curl/"):
        return "cli-curl"
    if any(k in lo for k in ("iphone", "ipad", "android", "mobile")):
        return "browser-mobile"
    if any(k in lo for k in ("mozilla", "chrome", "safari", "firefox", "edge", "webkit")):
        return "browser-desktop"
    if any(k in lo for k in ("python-urllib", "httpx/", "wget/", "go-http-client", "powershell")):
        return "cli-other"
    return "unknown"


def _audit_path(tenant_id: str | None) -> Path:
    """Resolve the chain path. Falls back to _default for anonymous denials."""
    tid = tenant_id if tenant_id else "_default"
    return _forge_paths.tenant_global_dir(tid) / "forge" / "audit.jsonl"


def _emit(
    event_type: str,
    *,
    tenant_id: str | None,
    details: dict[str, Any],
    severity: str | None = None,
) -> None:
    bad = _FORBIDDEN_FIELDS.intersection(details.keys())
    if bad:
        raise AuditFieldNotAllowed(
            f"forbidden fields in {event_type}: {sorted(bad)}"
        )
    allowed = _ALLOWED_FIELDS.get(event_type)
    if allowed is not None:
        extra = set(details.keys()) - allowed
        if extra:
            raise AuditFieldNotAllowed(
                f"{event_type}: unknown fields {sorted(extra)}; "
                f"allowed={sorted(allowed)}"
            )
    chain = _audit_path(tenant_id)
    try:
        _security_events.write_event(
            chain, event_type, details=details, severity=severity,
        )
    except Exception:  # pragma: no cover — chain unreachable
        pass


def session_started(
    *,
    tenant_id: str,
    token_fingerprint: str,
    sid_fingerprint: str,
    user_agent: str | None = None,
) -> None:
    _emit(
        "console.session_started",
        tenant_id=tenant_id,
        details={
            "tenant_id":         tenant_id,
            "token_fingerprint": token_fingerprint,
            "sid_fingerprint":   sid_fingerprint,
            "user_agent_class":  classify_user_agent(user_agent),
        },
        severity="INFO",
    )


def session_ended(
    *,
    tenant_id: str,
    sid_fingerprint: str,
    reason: str,
) -> None:
    _emit(
        "console.session_ended",
        tenant_id=tenant_id,
        details={
            "sid_fingerprint": sid_fingerprint,
            "reason":          reason,
            "tenant_id":       tenant_id,
        },
        severity="INFO",
    )


def action_performed(
    *,
    tenant_id: str,
    sid_fingerprint: str,
    action: str,
    target_kind: str,
    target_id: str,
    run_id: str | None = None,
    step_id: str | None = None,
    trigger: str | None = None,
) -> None:
    """One emit per successful mutation. Audit details are curated;
    NEVER include the cleartext memory body, persona JSON or tool
    impl source."""
    details: dict[str, Any] = {
        "action":          action,
        "target_kind":     target_kind,
        "target_id":       target_id,
        "sid_fingerprint": sid_fingerprint,
        "tenant_id":       tenant_id,
    }
    if run_id is not None:
        details["run_id"] = run_id
    if step_id is not None:
        details["step_id"] = step_id
    if trigger is not None:
        details["trigger"] = trigger
    _emit(
        "console.action_performed",
        tenant_id=tenant_id,
        details=details,
        severity="INFO",
    )


def action_denied(
    *,
    tenant_id: str,
    sid_fingerprint: str,
    action: str,
    target_kind: str,
    target_id: str,
    reason: str,
    run_id: str | None = None,
) -> None:
    """Emit when an action is blocked by policy (consent gate, zone check, etc.)."""
    details: dict[str, Any] = {
        "action":          action,
        "target_kind":     target_kind,
        "target_id":       target_id,
        "sid_fingerprint": sid_fingerprint,
        "tenant_id":       tenant_id,
        "reason":          reason,
    }
    if run_id is not None:
        details["run_id"] = run_id
    _emit(
        "console.action_denied",
        tenant_id=tenant_id,
        details=details,
        severity="WARNING",
    )


def action_failed(
    *,
    tenant_id: str,
    sid_fingerprint: str,
    action: str,
    target_kind: str,
    target_id: str,
    reason: str,
) -> None:
    _emit(
        "console.action_failed",
        tenant_id=tenant_id,
        details={
            "action":          action,
            "target_kind":     target_kind,
            "target_id":       target_id,
            "sid_fingerprint": sid_fingerprint,
            "tenant_id":       tenant_id,
            "reason":          reason,
        },
        severity="WARNING",
    )


def session_denied(
    *,
    reason: str,
    tenant_id: str | None = None,
    token_fingerprint: str | None = None,
    user_agent: str | None = None,
) -> None:
    details: dict[str, Any] = {
        "reason":           reason,
        "user_agent_class": classify_user_agent(user_agent),
    }
    if token_fingerprint is not None:
        details["token_fingerprint"] = token_fingerprint
    _emit(
        "console.session_denied",
        tenant_id=tenant_id,
        details=details,
        severity="WARNING",
    )


def execution_context(
    *,
    turn_id: str,
    session_id: str,
    tenant_id: str,
    engine_id: str,
    model_source: str,
    model_name: str,
    delegation_mode: str,
    duration_ms: int,
    exit_code: int,
    acs_run_id: str | None = None,
    tde_router_decision: str | None = None,
    tokens_input: int | None = None,
    tokens_output: int | None = None,
    tool_calls_count: int = 0,
    started_at: str | None = None,
    completed_at: str | None = None,
    turn_number: int = -1,
) -> None:
    """Emit execution context for every turn — L16 compliance (ADR-0248).

    Captures engine, model, delegation, and performance metadata for every OS
    turn across all engines (Claude Code, ACS, TDE, Hermes). Used for:
      - Performance analytics (avg duration per engine/model)
      - Audit trail (who used which engine/model)
      - Cost estimation (tokens × model pricing)
      - Debugging (failures tagged with engine/exit_code)

    Args:
        turn_id: Unique turn identifier (uuid or turn_key)
        session_id: Chat session identifier (sid or chat_key)
        tenant_id: Tenant ID for audit correlation
        engine_id: Engine used (claude_code | acs | tde | hermes)
        model_source: Model source (claude | ollama | openrouter | hermes)
        model_name: Normalized model name (e.g. "claude-3-5-sonnet")
        delegation_mode: How turn was delegated (native | acs | tde | fallback)
        duration_ms: Wall-clock milliseconds from start to completion
        exit_code: 0 (success) or non-zero (error)
        acs_run_id: ACS run UUID if delegated to ACS
        tde_router_decision: TDE router decision log if TDE used
        tokens_input: Input tokens consumed (if available)
        tokens_output: Output tokens produced (if available)
        tool_calls_count: Number of tool invocations
        started_at: RFC 3339 timestamp when turn started
        completed_at: RFC 3339 timestamp when turn completed
        turn_number: 0-indexed turn number in session
    """
    details: dict[str, Any] = {
        "turn_id":           turn_id,
        "session_id":        session_id,
        "tenant_id":         tenant_id,
        "engine_id":         engine_id,
        "model_source":      model_source,
        "model_name":        model_name,
        "delegation_mode":   delegation_mode,
        "duration_ms":       duration_ms,
        "exit_code":         exit_code,
        "tool_calls_count":  tool_calls_count,
    }
    if acs_run_id is not None:
        details["acs_run_id"] = acs_run_id
    if tde_router_decision is not None:
        details["tde_router_decision"] = tde_router_decision
    if tokens_input is not None:
        details["tokens_input"] = tokens_input
    if tokens_output is not None:
        details["tokens_output"] = tokens_output
    if started_at is not None:
        details["started_at"] = started_at
    if completed_at is not None:
        details["completed_at"] = completed_at
    if turn_number >= 0:
        details["turn_number"] = turn_number

    _emit(
        "console.execution_context",
        tenant_id=tenant_id,
        details=details,
        severity="INFO",
    )


def system_event(
    *,
    tenant_id: str | None,
    event: str,
    details: dict[str, Any],
    severity: str = "INFO",
) -> None:
    """Generic background/system event emitter for callers that don't have
    their own dedicated wrapper (e.g. routes/models.py's periodic model-
    catalog refresh). ``event`` is the free-form event_type string; unlike
    the named wrappers above it has no entry in _ALLOWED_FIELDS, so _emit()
    skips the field-allowlist check for it (still subject to
    _FORBIDDEN_FIELDS)."""
    _emit(
        event,
        tenant_id=tenant_id,
        details=details,
        severity=severity,
    )
