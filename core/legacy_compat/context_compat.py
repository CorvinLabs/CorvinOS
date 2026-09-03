"""
Context Engineering v1 Compat Layer → HybridContextModel (ADR-0555)

Transparent routing: old create_snapshot_v1() calls → the tenant-bound
``core.learning.hybrid_context.HybridContextModel``.

**Fail-closed guarantee:** Errors propagate (never silent fallback).
**Audit trail:** Every call logged as DeprecatedAPIEvent; the snapshot itself is
audited on the tenant's CORE hash chain by ``snapshot_base_context`` (ADR-0232).
**Tenant-safe:** The model is constructed with the CURRENT tenant
(``forge.tenants.current_tenant`` — explicit arg → ``CORVIN_TENANT_ID`` →
``_default``, always validated). The previous version constructed
``HybridContextModel()`` with no tenant at all (adversarial review N-04).
"""

from __future__ import annotations

import copy
from dataclasses import asdict
from typing import Optional, Any, Dict

from core.telemetry.deprecated_api_calls import log_deprecated_call, log_deprecated_error
from core.learning.hybrid_context import HybridContextModel

_V1_MODULE = "core.context_engineering.snapshot"


def _resolve_tenant(tenant_id: Optional[str]) -> str:
    """Validated tenant id: explicit → ``CORVIN_TENANT_ID`` → ``_default``."""
    try:
        import corvin_core._bootstrap  # noqa: F401 — puts operator/forge on sys.path in a checkout
    except Exception:  # noqa: BLE001 — packaged layout
        pass
    try:
        from forge.tenants import current_tenant  # type: ignore[import-not-found]
    except ImportError:  # stripped layout: same precedence, validated
        import os
        from core.tenants.validation import validate_tenant_id

        return validate_tenant_id(tenant_id or os.environ.get("CORVIN_TENANT_ID") or "_default")
    return current_tenant(tenant_id)


def _model_for(tenant_id: Optional[str]) -> tuple[HybridContextModel, str]:
    tid = _resolve_tenant(tenant_id)
    return HybridContextModel(tid), tid


def create_snapshot_v1(
    context: Dict[str, Any],
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Deprecated: Use HybridContextModel directly.

    Old API (Context Engineering v1): Create snapshot of task context.

    **Phase B behavior:**
    - Transparently calls ``HybridContextModel.snapshot_base_context`` (Tier 1)
      for the current tenant
    - Returns an immutable snapshot dict: ``tenant_id``, ``user_id``,
      ``session_id``, ``base_hash`` and the Tier 1 ``base``
    - Logged to audit trail (deprecated-API event + core-chain snapshot event)

    **Phase C (week 8+):** This function will be deleted.

    Args:
        context: Task context to snapshot. Recognised keys: ``recent_decisions``
            (list), ``user_profile`` (dict), ``success_rate`` (float),
            ``attention_budget`` (int), ``session_id`` / ``task_id``, ``user_id``.
        tenant_id: Tenant scope (ADR-0007, GDPR Art. 5). ``None`` → current tenant.
        user_id: Optional user ID (scrubbed, no PII in audit)

    Returns:
        dict: Immutable snapshot

    Raises:
        Exception: If snapshot creation fails (fail-closed)
    """
    tid = tenant_id
    try:
        model, tid = _model_for(tenant_id)

        # Log deprecated call (tenant already validated)
        log_deprecated_call(
            api_name="create_snapshot_v1",
            module=_V1_MODULE,
            tenant_id=tid,
            user_id=user_id,
        )

        uid = str(user_id or context.get("user_id") or "anonymous")
        sid = str(context.get("session_id") or context.get("task_id") or "v1")
        base_hash = model.snapshot_base_context(
            user_id=uid,
            session_id=sid,
            decisions=list(context.get("recent_decisions") or []),
            profile=dict(context.get("user_profile") or {}),
            success_rate=float(context.get("success_rate", 0.5)),
            attention_budget=int(context.get("attention_budget", 0)),
        )
        base = model.base_snapshots[f"{uid}:{sid}"]
        return {
            "tenant_id": tid,
            "user_id": uid,
            "session_id": sid,
            "base_hash": base_hash,
            "base": asdict(base),
        }

    except Exception as e:
        # Fail-closed: error propagates
        log_deprecated_error(
            api_name="create_snapshot_v1",
            module=_V1_MODULE,
            error=e,
            tenant_id=tid or "_default",
            user_id=user_id,
        )
        raise


def restore_snapshot_v1(
    snapshot: Dict[str, Any],
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Deprecated: Use HybridContextModel directly.

    Old API (Context Engineering v1): Restore context from snapshot.

    **Phase B behavior:**
    - Verifies the snapshot belongs to the current tenant and that its
      ``base_hash`` recomputes (tamper-evident, fail-closed)
    - Returns the Tier 1 context fields (old shape: a plain dict)
    - Logged to audit trail

    **Phase C (week 8+):** This function will be deleted.
    """
    tid = tenant_id
    try:
        tid = _resolve_tenant(tenant_id)
        log_deprecated_call(
            api_name="restore_snapshot_v1",
            module=_V1_MODULE,
            tenant_id=tid,
        )

        base = dict(snapshot.get("base") or {})
        if snapshot.get("tenant_id") != tid or base.get("tenant_id") != tid:
            raise ValueError("snapshot tenant does not match the current tenant")
        expected = HybridContextModel._compute_hash(
            "base",
            {
                "decisions": base.get("recent_decisions", []),
                "profile": base.get("user_profile", {}),
                "success_rate": base.get("success_rate", 0.5),
            },
            base.get("prev_base_hash", ""),
        )
        if expected != snapshot.get("base_hash") or expected != base.get("base_hash"):
            raise ValueError("snapshot base_hash does not verify (tampered or corrupt)")

        return copy.deepcopy(base)

    except Exception as e:
        log_deprecated_error(
            api_name="restore_snapshot_v1",
            module=_V1_MODULE,
            error=e,
            tenant_id=tid or "_default",
        )
        raise
