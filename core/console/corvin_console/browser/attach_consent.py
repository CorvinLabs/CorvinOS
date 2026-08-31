"""ADR-0200 Phase 2 — the `real-chrome` attach consent scope.

Attaching a session to the user's OWN logged-in Chrome puts their bank / mail /
cloud sessions in reach, so it is a distinct, EXPLICIT consent — separate from
the generic browser consent, TTL-capped, and revocable at any moment. An attach
session cannot be created without an active grant here.

Deliberately its OWN small store (not the messenger-user `consent.py`, which is
keyed by channel+uid for GDPR message-processing consent — a different domain).
One grant per tenant; granting again refreshes the TTL. Metadata-only audit.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from forge import paths as _forge_paths  # type: ignore

# TTL clamps mirror consent.py's convention so the two feel consistent.
MIN_TTL_S = 60                       # 1 minute
MAX_TTL_S = 12 * 60 * 60             # 12 hours — a real-login grant should not be
                                     # long-lived; re-granting is one click.
DEFAULT_TTL_S = 60 * 60              # 1 hour


def _now() -> float:
    return time.time()


def _path(tenant_id: str) -> Path:
    return _forge_paths.tenant_global_dir(tenant_id) / "browser_attach_consent.json"


def clamp_ttl(ttl_s: int | float | None) -> int:
    try:
        v = int(ttl_s) if ttl_s is not None else DEFAULT_TTL_S
    except (TypeError, ValueError):
        v = DEFAULT_TTL_S
    return max(MIN_TTL_S, min(MAX_TTL_S, v))


def grant(tenant_id: str, ttl_s: int | float | None = None,
          *, audit_fn: "Callable[..., None] | None" = None) -> float:
    """Grant (or refresh) the real-chrome attach consent for *ttl_s* seconds.
    Returns the absolute expiry time. TTL is clamped to [MIN, MAX]."""
    ttl = clamp_ttl(ttl_s)
    expires_at = _now() + ttl
    p = _path(tenant_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.tmp")
    tmp.write_text(json.dumps({
        "granted_at": _now(), "expires_at": expires_at, "ttl_s": ttl,
        "revoked": False,
    }), encoding="utf-8")
    tmp.replace(p)
    _audit(audit_fn, tenant_id, "browser.attach.consent_granted", ttl_s=ttl)
    return expires_at


def revoke(tenant_id: str, *, audit_fn: "Callable[..., None] | None" = None) -> None:
    """Revoke immediately — the next attach action is refused. Idempotent."""
    p = _path(tenant_id)
    if p.exists():
        try:
            p.unlink()
        except OSError:
            pass
    _audit(audit_fn, tenant_id, "browser.attach.consent_revoked")


def status(tenant_id: str) -> dict[str, Any]:
    """(active, expires_at, remaining_s) — safe to call any time, never raises."""
    p = _path(tenant_id)
    if not p.exists():
        return {"active": False, "expires_at": None, "remaining_s": 0}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a corrupt store is treated as NO consent (fail-closed)
        return {"active": False, "expires_at": None, "remaining_s": 0}
    if d.get("revoked"):
        return {"active": False, "expires_at": d.get("expires_at"), "remaining_s": 0}
    exp = float(d.get("expires_at") or 0)
    # Read-time re-clamp against the WRITE time (review F3): a hand-crafted /
    # tampered store with a far-future expires_at must NOT become a permanent
    # real-login consent — the 12h ceiling is enforced on read too, not only on
    # write. Bound the effective expiry at granted_at + MAX_TTL_S; a store with
    # no valid granted_at is treated as expired (fail-closed), since we can't
    # prove it is within the ceiling.
    try:
        granted_at = float(d.get("granted_at"))
    except (TypeError, ValueError):
        return {"active": False, "expires_at": exp, "remaining_s": 0}
    effective_exp = min(exp, granted_at + MAX_TTL_S)
    remaining = effective_exp - _now()
    return {"active": remaining > 0, "expires_at": effective_exp,
            "remaining_s": max(0, int(remaining))}


def active(tenant_id: str) -> bool:
    """True iff a non-revoked, non-expired grant exists. Fail-closed on any
    error (a missing/corrupt store means NO consent → attach refused)."""
    return bool(status(tenant_id).get("active"))


def _audit(audit_fn, tenant_id: str, event: str, **details: Any) -> None:
    if audit_fn is None:
        return
    try:
        audit_fn(tenant_id=tenant_id, event=event, details=details)
    except Exception:  # noqa: BLE001 — audit is best-effort, never blocks
        pass
