"""One writer for skill-system audit events → the tenant CORE audit chain.

Every skill subsystem used to pick its own sink: SkillForge wrote to
``<scope_root>/audit.jsonl``, ``ContextSelectorSkill`` and ``FeatureFlagsAudit``
wrote ``AuditChainWriter`` records (a different genesis / record format) to a
hard-coded ``~/.corvin/audit.jsonl`` that ignored ``CORVIN_HOME`` and the
tenant, and ``skill_manager`` / ``executor`` emitted nothing. None of those
records were in the chain the boot tripwire, ``audit_query`` and the
compliance reports read — ``<tenant_home>/global/forge/audit.jsonl``
(``corvin_compliance_reports.audit_query.audit_chain_path``).

This module is that ONE sink. It resolves the tenant chain through
``forge.paths`` and appends through ``forge.security_events.write_event`` — the
same hash-chain writer the core audit uses — so a skill event is a link in the
same verifiable chain as every other event of the tenant.

Metadata only (ADR-0129 floor): callers pass identifiers, counters and
timings — never inputs, outputs, prompts or user content. ``write_event``
drops forbidden keys fail-closed; callers still must not send them.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _ensure_operator_on_path() -> None:
    """Make ``forge`` importable in a source checkout (no-op in a wheel)."""
    try:
        import corvin_core._bootstrap  # noqa: F401  — inserts corvin_operator/forge etc.
    except Exception:  # noqa: BLE001 — packaged layout without corvin_core
        pass


def audit_chain_path(tenant_id: str, corvin_home: Optional[Path] = None) -> Path:
    """``<tenant_home>/global/forge/audit.jsonl`` — the chain the tripwire reads.

    Resolved through ``tenant_audit_chain()`` — the ONE resolver every writer of a
    hash-chained record shares (``forge.paths``, mirrored byte-identically by
    ``core.paths.tenant``; pinned by ``tests/security/test_audit_chain_ssot.py``). Until
    2026-11-19 this function composed the path by hand in three places (review PLS-8).

    ``corvin_home`` pins the runtime root for callers that already own one
    (``SkillManager(corvin_home, tenant_id)``). The resolver takes no root argument, so the
    pinned path is the resolver's own path re-rooted: its tail below the resolver's
    ``corvin_home()``, under the pinned root. Nothing about the layout is restated here.
    """
    chain, home = _resolve_chain(tenant_id)
    if corvin_home is None:
        return chain
    pinned = Path(corvin_home)
    try:
        return pinned / chain.relative_to(home)
    except ValueError:
        # The tenant home is not below corvin_home() (a relocated tenant root): a pinned
        # root cannot be honoured without guessing the layout, so the resolver's path stands.
        logger.error("skill audit: cannot re-root the audit chain under a pinned corvin_home; "
                     "using the resolver's path")
        return chain


def _resolve_chain(tenant_id: str) -> "tuple[Path, Path]":
    """``(tenant_audit_chain(tenant_id), corvin_home())`` from ``forge.paths``, else from its
    byte-identical mirror ``core.paths.tenant`` (forge not importable in a stripped layout)."""
    _ensure_operator_on_path()
    try:
        from forge import paths as _paths  # type: ignore[import-not-found]

        return Path(_paths.tenant_audit_chain(tenant_id)), Path(_paths.corvin_home())
    except Exception:  # noqa: BLE001 — forge not importable (stripped layout)
        from core.paths import tenant as _paths  # noqa: PLC0415

        return Path(_paths.tenant_audit_chain(tenant_id)), Path(_paths.corvin_home())


def emit_skill_audit(
    tenant_id: str,
    event_type: str,
    *,
    tool: str = "",
    run_id: str = "",
    details: Optional[dict[str, Any]] = None,
    severity: Optional[str] = None,
    corvin_home: Optional[Path] = None,
) -> bool:
    """Append one hash-chained event to the tenant core chain.

    Returns True when the record was chained. Returns False — and logs at
    ERROR, never silently — when the core writer is unreachable or refuses
    the record; the caller's own operation is not aborted and this function
    never raises (the boot tripwire is what guarantees the writer exists in
    a real install; here we only refuse to pretend).
    """
    if not tenant_id or not isinstance(tenant_id, str):
        logger.error("skill audit rejected: missing tenant_id for %s", event_type)
        return False
    _ensure_operator_on_path()
    try:
        from forge.security_events import write_event  # type: ignore[import-not-found]
        from forge.security_events import register_event_allowlist  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        logger.error(
            "core audit writer unavailable — skill event %s NOT chained", event_type
        )
        return False
    # Positive, content-free allowlist for every skill.* record this module
    # emits (default-deny floor, ADR-0640). Structural keys only — never
    # inputs, outputs, prompts or user content.
    register_event_allowlist(event_type, {
        "skill_id", "skill_version", "status", "operation", "flag_id", "enabled",
        "latency_ms", "duration_ms", "error_type", "lom", "lom_hash", "decision",
        "tenant_id", "audit_ref", "tool", "run_id", "tier", "reason_code",
    })
    body: dict[str, Any] = dict(details or {})
    body["tenant_id"] = tenant_id
    try:
        path = audit_chain_path(tenant_id, corvin_home)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_event(
            path,
            event_type,
            severity=severity,
            tool=tool,
            run_id=run_id,
            details=body,
            hash_chain=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — logged, never propagated into the skill op
        logger.error("skill audit write failed for %s (%s): %s", event_type, type(exc).__name__, exc)
        return False
