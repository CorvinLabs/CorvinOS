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
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _ensure_operator_on_path() -> None:
    """Make ``forge`` importable in a source checkout (no-op in a wheel)."""
    try:
        import corvin_core._bootstrap  # noqa: F401  — inserts operator/forge etc.
    except Exception:  # noqa: BLE001 — packaged layout without corvin_core
        pass


def audit_chain_path(tenant_id: str, corvin_home: Optional[Path] = None) -> Path:
    """``<tenant_home>/global/forge/audit.jsonl`` — the chain the tripwire reads.

    ``corvin_home`` pins the runtime root for callers that already own one
    (``SkillManager(corvin_home, tenant_id)``); otherwise ``forge.paths``
    resolves it (``CORVIN_HOME`` → repo-local ``.corvin`` → ``~/.corvin``).
    """
    if corvin_home is not None:
        return Path(corvin_home) / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"
    _ensure_operator_on_path()
    try:
        from forge import paths as _forge_paths  # type: ignore[import-not-found]

        return _forge_paths.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"
    except Exception:  # noqa: BLE001 — forge not importable (stripped layout)
        root = os.environ.get("CORVIN_HOME")
        base = Path(os.path.expanduser(root)) if root else Path.home() / ".corvin"
        return base / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"


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
    except Exception:  # noqa: BLE001
        logger.error(
            "core audit writer unavailable — skill event %s NOT chained", event_type
        )
        return False
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
