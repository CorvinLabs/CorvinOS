"""Audit emitter — metadata only, never the prompt or output text.

Three event types — ``delegate.invoked`` / ``delegate.completed`` /
``delegate.failed`` — land in the unified hash chain at
``<corvin_home>/global/forge/audit.jsonl`` via
``forge.security_events.write_event``. The chain is the same one
forge, skill-forge, dialectic, voice-transcribe, memory etc. write
to — one ``voice-audit verify`` covers all of them.

Per-event allow-list + global ``_FORBIDDEN_FIELDS`` set enforce the
metadata-only rule at the boundary. Mirror of L23 (voice-transcribe),
L24 (data snapshots), L25 (compute), L28 (memory).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# Forbidden everywhere — these names smell like raw content. Any
# attempt to smuggle one into a details payload raises before the
# write_event call.
_FORBIDDEN_FIELDS: frozenset[str] = frozenset({
    "prompt", "prompt_text", "input", "input_text",
    "output", "output_text", "final_text", "text",
    "response", "completion", "result_text",
    "api_key", "key", "token", "secret",
})

_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "delegate.invoked": frozenset({
        "engine", "persona", "prompt_chars", "budget_s", "model",
    }),
    "delegate.completed": frozenset({
        "engine", "persona", "duration_ms", "output_chars",
    }),
    "delegate.failed": frozenset({
        "engine", "persona", "reason", "duration_ms",
    }),
    # Layer 29.3a — output-judge metadata. NEVER carries the judge's
    # `notes` line or the revised text; only the structural verdict.
    # Revised text would leak both the worker's output AND the judge's
    # interpretation into the chain — exactly the metadata-only rule
    # we're protecting.
    "delegate.output_judged": frozenset({
        "engine", "persona", "mode", "verdict", "latency_ms", "replaced",
    }),
    # Layer 29.4a — tenant-policy gate. Two distinct events because
    # they fire on different conditions (engine-list vs zone-routing)
    # and operators want to filter / alert on them separately.
    "delegate.engine_policy_denied": frozenset({
        "engine", "persona", "tenant_id", "reason",
    }),
    "delegate.zone_policy_denied": frozenset({
        "engine", "persona", "tenant_id", "tenant_zone",
        "engine_zone", "reason",
    }),
    # Layer 29.5 — bwrap sandbox metadata. Two distinct events:
    # the happy path (bwrap wrap activated) is INFO with mode +
    # decision; the bwrap-missing path is WARNING with reason.
    "delegate.sandboxed": frozenset({
        "engine", "persona", "mode", "decision",
    }),
    "delegate.sandbox_unavailable": frozenset({
        "engine", "persona", "mode", "reason",
    }),
    # Layer 29.6 — pre-flight prompt-safety classifier metadata.
    # NEVER carries the classifier's `notes` line OR the prompt
    # text itself — only the structural verdict + mode + whether
    # the delegation was ultimately blocked.
    "delegate.prompt_classified": frozenset({
        "engine", "persona", "mode", "verdict", "latency_ms", "blocked",
    }),
    # Layer 30 (ADR-0022) — engine-agnostic Forge + SkillForge via
    # delegation. Skill bodies NEVER in the chain (mirror of L23 voice-
    # transcribe, L25 compute, L28 memory). The two events surface only
    # structural metadata so an operator can see "this delegate spawn
    # received N skills (M chars total)" and "this delegate spawn
    # had forge + skill_forge MCP servers wired" without leaking what
    # the skills contained or what the MCP config looked like.
    "delegate.skill_injected": frozenset({
        "engine", "persona", "skill_count", "skill_chars",
    }),
    "delegate.mcp_wired": frozenset({
        "engine", "persona", "mcp_servers",
    }),
}


class DelegateAuditFieldNotAllowed(Exception):
    """Raised when a details payload carries a key outside the allow-list."""


def _resolve_audit_path() -> Path | None:
    """Best-effort resolution of the unified chain path.

    Order:
      1. $CORVIN_HOME env override — checked first so tests can inject a
         temp dir without patching forge.paths (which is Phase-7 locked).
      2. forge.paths.corvin_home() → <home>/global/forge/audit.jsonl
      3. None — caller treats as "audit disabled"
    """
    # Check env override first — lets integration tests use a temp dir.
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(env) / "global" / "forge" / "audit.jsonl"

    # Fall back to the canonical path resolver.
    try:
        _ensure_forge_on_path()
        from forge.paths import corvin_home  # type: ignore
        return Path(corvin_home()) / "global" / "forge" / "audit.jsonl"
    except Exception:  # noqa: BLE001
        pass

    return None


def _ensure_forge_on_path() -> None:
    plugin_root = Path(__file__).resolve().parents[2]
    forge_pkg = plugin_root / "forge"
    if forge_pkg.is_dir():
        p = str(forge_pkg)
        if p not in sys.path:
            sys.path.insert(0, p)


def _validate_details(event_type: str, details: dict[str, Any]) -> dict[str, Any]:
    allowed = _ALLOWED_FIELDS.get(event_type)
    if allowed is None:
        raise DelegateAuditFieldNotAllowed(
            f"unknown audit event_type: {event_type!r}"
        )
    out: dict[str, Any] = {}
    for k, v in details.items():
        if not isinstance(k, str):
            raise DelegateAuditFieldNotAllowed(
                f"non-string audit key for {event_type}: {k!r}"
            )
        if k in _FORBIDDEN_FIELDS:
            raise DelegateAuditFieldNotAllowed(
                f"forbidden audit field {k!r} for {event_type}"
            )
        if k not in allowed:
            raise DelegateAuditFieldNotAllowed(
                f"field {k!r} not in allow-list for {event_type}"
            )
        # Strip None values so the chain stays compact.
        if v is None:
            continue
        out[k] = v
    return out


def _write(event_type: str, **fields: Any) -> None:
    """Common emit path. Best-effort — failures swallow silently."""
    audit_path = _resolve_audit_path()
    if audit_path is None:
        return
    try:
        _ensure_forge_on_path()
        from forge.security_events import write_event  # type: ignore
    except Exception:  # noqa: BLE001
        return
    safe_details = _validate_details(event_type, fields)
    try:
        write_event(
            audit_path,
            event_type,
            tool="corvin-delegate",
            details=safe_details,
        )
    except Exception:  # noqa: BLE001 — never let audit failure break delegation
        pass


# ---------------------------------------------------------------------------
# Public emitters
# ---------------------------------------------------------------------------


def emit_invoked(
    *,
    engine: str,
    persona: str = "",
    prompt_chars: int = 0,
    budget_s: int = 0,
    model: str | None = None,
) -> None:
    """Emit ``delegate.invoked``. Metadata only."""
    _write(
        "delegate.invoked",
        engine=engine,
        persona=persona,
        prompt_chars=int(prompt_chars),
        budget_s=int(budget_s),
        model=model,
    )


def emit_completed(
    *,
    engine: str,
    persona: str = "",
    duration_ms: int = 0,
    output_chars: int = 0,
) -> None:
    """Emit ``delegate.completed``. Metadata only."""
    _write(
        "delegate.completed",
        engine=engine,
        persona=persona,
        duration_ms=int(duration_ms),
        output_chars=int(output_chars),
    )


def emit_failed(
    *,
    engine: str,
    persona: str = "",
    reason: str = "",
    duration_ms: int = 0,
) -> None:
    """Emit ``delegate.failed``. Metadata only.

    ``reason`` is curated by the caller — keep it short and stable
    (``engine-spawn-failed``, ``engine-error``, ``engine-construct-failed``,
    ``agents-import-failed``).
    """
    _write(
        "delegate.failed",
        engine=engine,
        persona=persona,
        reason=reason[:120],  # hard cap so a runaway reason can't bloat the chain
        duration_ms=int(duration_ms),
    )


def emit_output_judged(
    *,
    engine: str,
    persona: str = "",
    mode: str = "off",
    verdict: str = "skipped",
    latency_ms: int = 0,
    replaced: bool = False,
) -> None:
    """Emit ``delegate.output_judged``. Metadata only.

    The judge's free-form ``notes`` field and the ``revised_text``
    are NEVER logged here — only the structural verdict + latency
    + whether the text was replaced. Mirror of the metadata-only
    rule (L23 voice-transcribe / L25 compute / L28 memory).
    """
    _write(
        "delegate.output_judged",
        engine=engine,
        persona=persona,
        mode=mode,
        verdict=verdict,
        latency_ms=int(latency_ms),
        replaced=bool(replaced),
    )


def emit_engine_policy_denied(
    *,
    engine: str,
    persona: str = "",
    tenant_id: str = "",
    reason: str = "",
) -> None:
    """Emit ``delegate.engine_policy_denied``. Metadata only.

    ``reason`` is curated: ``engine-not-allowed`` (engine missing
    from allowed_engines OR present in forbid_engines) or
    ``policy-malformed`` (config file exists but is broken).
    """
    _write(
        "delegate.engine_policy_denied",
        engine=engine,
        persona=persona,
        tenant_id=tenant_id,
        reason=reason[:120],
    )


def emit_zone_policy_denied(
    *,
    engine: str,
    persona: str = "",
    tenant_id: str = "",
    tenant_zone: str = "",
    engine_zone: str = "",
    reason: str = "",
) -> None:
    """Emit ``delegate.zone_policy_denied``. Metadata only.

    Carries both zones explicitly so an operator's metrics dashboard
    can break down "EU tenant tried to route through US-only engine"
    without parsing the reason string. Reason field carries the
    curated tag from ``is_zone_compatible``.
    """
    _write(
        "delegate.zone_policy_denied",
        engine=engine,
        persona=persona,
        tenant_id=tenant_id,
        tenant_zone=tenant_zone,
        engine_zone=engine_zone,
        reason=reason[:120],
    )


def emit_sandboxed(
    *,
    engine: str,
    persona: str = "",
    mode: str = "off",
    decision: str = "skipped-off",
) -> None:
    """Emit ``delegate.sandboxed``. Metadata only.

    Fires when bwrap wrapping was activated for the spawn. Decision
    is one of ``bwrap`` (only value emitted today; future variants
    could add ``slirp4netns`` etc.). The mode echoes the resolved
    final_sandbox_mode (advisory / enforcing).
    """
    _write(
        "delegate.sandboxed",
        engine=engine,
        persona=persona,
        mode=mode,
        decision=decision,
    )


def emit_sandbox_unavailable(
    *,
    engine: str,
    persona: str = "",
    mode: str = "off",
    reason: str = "",
) -> None:
    """Emit ``delegate.sandbox_unavailable``. Metadata only.

    Fires when bwrap was requested but unavailable. In ``advisory``
    mode the delegation proceeds natively; in ``enforcing`` mode it
    is denied (caller surfaces the deny via DelegateResult).
    """
    _write(
        "delegate.sandbox_unavailable",
        engine=engine,
        persona=persona,
        mode=mode,
        reason=reason[:120],
    )


def emit_prompt_classified(
    *,
    engine: str,
    persona: str = "",
    mode: str = "off",
    verdict: str = "skipped",
    latency_ms: int = 0,
    blocked: bool = False,
) -> None:
    """Emit ``delegate.prompt_classified``. Metadata only.

    Verdict is one of ``safe`` / ``refuse`` / ``classifier_error``.
    ``blocked`` is True iff blocking-mode + REFUSE actually
    refused the delegation. The classifier's free-form ``notes``
    line is NEVER logged here — same metadata-only rule as
    L23 / L25 / L28 / L29.3a.
    """
    _write(
        "delegate.prompt_classified",
        engine=engine,
        persona=persona,
        mode=mode,
        verdict=verdict,
        latency_ms=int(latency_ms),
        blocked=bool(blocked),
    )


def emit_skill_injected(
    *,
    engine: str,
    persona: str = "",
    skill_count: int = 0,
    skill_chars: int = 0,
) -> None:
    """Emit ``delegate.skill_injected``. Metadata only.

    Fires once per delegate spawn that prepended a skill-context
    block to the worker prompt. Carries the count + total char-length
    so operators can see in one line whether the spawn received
    "no skills" / "a small advisory block" / "5 skills × 4 KB each".
    The skill **names** and **bodies** are NEVER logged here — same
    metadata-only rule as L23 / L25 / L28 / L29.
    """
    _write(
        "delegate.skill_injected",
        engine=engine,
        persona=persona,
        skill_count=int(skill_count),
        skill_chars=int(skill_chars),
    )


def emit_mcp_wired(
    *,
    engine: str,
    persona: str = "",
    mcp_servers: list[str] | tuple[str, ...] = (),
) -> None:
    """Emit ``delegate.mcp_wired``. Metadata only.

    Fires once per delegate spawn that materialised one or more MCP
    server configs into the worker's per-spawn tempdir. Carries the
    list of MCP server names (e.g. ``["forge", "skill_forge"]``) but
    NOT the server commands, args, env, or any content of the
    materialised config files. The server names are curated
    (``forge``, ``skill_forge``) so the chain stays grep-friendly.
    """
    # Normalise to list[str] for stable JSON serialisation. Filter
    # empty / non-string entries defensively.
    cleaned = [str(s) for s in (mcp_servers or ()) if isinstance(s, str) and s]
    _write(
        "delegate.mcp_wired",
        engine=engine,
        persona=persona,
        mcp_servers=cleaned,
    )


__all__ = [
    "DelegateAuditFieldNotAllowed",
    "emit_completed",
    "emit_engine_policy_denied",
    "emit_failed",
    "emit_invoked",
    "emit_mcp_wired",
    "emit_output_judged",
    "emit_prompt_classified",
    "emit_sandbox_unavailable",
    "emit_sandboxed",
    "emit_skill_injected",
    "emit_zone_policy_denied",
]
