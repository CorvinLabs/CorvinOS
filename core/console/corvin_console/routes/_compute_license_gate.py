"""Shared, fail-closed compute-quota gate (ADR-0146 CON-ACS-01 / CON-JOBS-01).

The console exposes more than one compute-execution entrypoint. Round 1 fixed
`POST /compute/runs` (submit_run), but `POST /compute/acs/runs` and
`POST /compute/jobs` spawned/queued compute with NO `compute_units_per_day`
check — a free-tier user could loop the ungated route for unbounded paid compute
while the gated counter stayed at its 1/day cap (the CON-01 "second execute path
skips the shared gate" pattern, in the compute surface).

This module is the single enforcement point so the execution paths cannot drift.
Fail-closed: a missing license module degrades to the FREE_TIER cap
(`compute_units_per_day` = 1), never to unmetered compute.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import HTTPException

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_OPERATOR = _REPO / "operator"
_FORGE = _OPERATOR / "forge"
for _p in (_FORGE, _OPERATOR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from forge import paths as _forge_paths  # noqa: E402

try:
    from license.compute_quota import increment_and_check as _cq_increment  # type: ignore[import]
    from license.compute_quota import refund_one as _cq_refund  # type: ignore[import]
    from license.validator import get_limit as _lic_get_limit  # type: ignore[import]
    from license.limits import LicenseLimitError as _LicLimitError  # type: ignore[import]
    _COMPUTE_QUOTA_OK = True
except ImportError:
    _cq_increment = None  # type: ignore[assignment]
    _cq_refund = None  # type: ignore[assignment]
    try:
        from license.limits import FREE_TIER as _FREE_TIER  # type: ignore[import]
    except ImportError:
        _FREE_TIER = {}  # type: ignore[assignment]
    _lic_get_limit = _FREE_TIER.get  # type: ignore[assignment]
    class _LicLimitError(Exception):  # type: ignore[no-redef]
        pass
    _COMPUTE_QUOTA_OK = False


def enforce_compute_quota(
    tenant_id: str,
    sid_fingerprint: str,
    *,
    audit_action: str,
    channel: str = "console",
) -> None:
    """Increment + check the daily compute quota; raise HTTP 402 when exceeded.

    Fail-CLOSED: if the license module is absent (``_cq_increment is None``) the
    call is refused with 402 rather than granted — a missing quota module must
    never read as "unmetered compute". Mirrors the gated submit_run path; the two
    ungated compute entrypoints (ACS, jobs) call this so they cannot drift.
    """
    if not _COMPUTE_QUOTA_OK or _cq_increment is None:
        _audit_failed(tenant_id, sid_fingerprint, audit_action, "quota_module_unavailable")
        raise HTTPException(
            status_code=402,
            detail={
                "error": "license_limit",
                "feature": "compute_units_per_day",
                "msg": "Compute quota enforcement unavailable — refusing compute (fail-closed).",
                "upgrade_url": "https://corvin-labs.com/pricing",
            },
        )
    try:
        _cq_increment(
            _forge_paths.corvin_home(),
            channel=channel,
            chat_key=f"{channel}:{tenant_id}:{sid_fingerprint[:8]}",
        )
    except _LicLimitError as exc:  # type: ignore[misc]
        _audit_failed(tenant_id, sid_fingerprint, audit_action, "quota_exceeded")
        raise HTTPException(
            status_code=402,
            detail={
                "error": "license_limit",
                "feature": "compute_units_per_day",
                "limit": _lic_get_limit("compute_units_per_day"),
                "msg": str(exc),
                "upgrade_url": "https://corvin-labs.com/pricing",
            },
        ) from exc


def refund_compute_quota() -> None:
    """Give back one compute unit charged by enforce_compute_quota when the run was
    NOT actually dispatched (the worker rejected the submission before doing any
    work). Best-effort, never raises. On the free tier (1 unit/day) this prevents a
    single transient worker error from burning the user's entire daily budget with
    nothing run. No-op when the quota module is unavailable."""
    if not _COMPUTE_QUOTA_OK or _cq_refund is None:
        return
    try:
        _cq_refund(_forge_paths.corvin_home())
    except Exception:  # noqa: BLE001 — refund is best-effort
        pass


def enforce_chat_turns(
    tenant_id: str,
    sid_fingerprint: str,
    *,
    audit_action: str,
    channel: str = "chat",
) -> None:
    """Increment + check the daily chat-turn quota (ADR-0150); raise HTTP 402 when
    exceeded.

    A SEPARATE axis from compute_units_per_day: conversational / design-assistant
    turns must NOT consume the 1/day compute-workload budget. Chat is UNLIMITED on
    every tier (operator decision) — the limit resolves to None, so this returns
    immediately and never charges or blocks. The fail-closed path below only
    applies if a future tier sets a finite chat ceiling.
    WS callers catch the HTTPException and emit an in-band error frame.
    """
    # Chat is unlimited (limit None) → always allow, no charge, no fail-closed.
    # Resolved via FREE_TIER fallback too, so this holds even when the quota
    # module is unavailable — chat must never be blocked.
    try:
        if _lic_get_limit("chat_turns_per_day") is None:
            return
    except Exception:  # noqa: BLE001
        return  # cannot resolve a limit → treat chat as unlimited (never block)

    if not _COMPUTE_QUOTA_OK or _cq_increment is None:
        _audit_failed(tenant_id, sid_fingerprint, audit_action, "quota_module_unavailable")
        raise HTTPException(
            status_code=402,
            detail={
                "error": "license_limit",
                "feature": "chat_turns_per_day",
                "msg": "Chat-turn quota enforcement unavailable — refusing (fail-closed).",
                "upgrade_url": "https://corvin-labs.com/pricing",
            },
        )
    try:
        _cq_increment(
            _forge_paths.corvin_home(),
            channel=channel,
            chat_key=f"{channel}:{tenant_id}:{sid_fingerprint[:8]}",
            feature="chat_turns_per_day",
            counter_file="chat_quota.json",
        )
    except _LicLimitError as exc:  # type: ignore[misc]
        _audit_failed(tenant_id, sid_fingerprint, audit_action, "quota_exceeded")
        raise HTTPException(
            status_code=402,
            detail={
                "error": "license_limit",
                "feature": "chat_turns_per_day",
                "limit": _lic_get_limit("chat_turns_per_day"),
                "msg": str(exc),
                "upgrade_url": "https://corvin-labs.com/pricing",
            },
        ) from exc


def _audit_failed(tenant_id: str, sid_fingerprint: str, action: str, reason: str) -> None:
    try:
        from .. import audit as _ca  # noqa: PLC0415

        _ca.action_failed(
            tenant_id=tenant_id,
            sid_fingerprint=sid_fingerprint,
            action=action,
            target_kind="compute_run",
            target_id="",
            reason=reason,
        )
    except Exception:  # noqa: BLE001
        pass
