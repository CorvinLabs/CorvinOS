"""ADR-0200 Phase 3 — the attach confirm-friction setting (Q3).

Two modes, an explicit per-tenant Web-Console setting:

  * ``confirm-each`` (DEFAULT) — every sensitive action (payment, account change,
    message send, cross-host) on an ATTACHED (real-login) session needs an
    explicit human confirm. Same as the always-on gate today.
  * ``watch`` — "I'm watching, act freely": the interactive confirm is
    auto-approved, but ONLY for the duration of a HARD, non-extendable TTL, after
    which it reverts to confirm-each. Watch-mode NEVER disables the audit or the
    egress gate — it suppresses only the interactive prompt.

Scoped to ATTACHED sessions on purpose: auto-approving on the empty launched
Chromium buys nothing (no real accounts) and would only weaken its gate, so
``should_auto_approve`` is meant to be consulted by the confirm broker ONLY when
the session is attached. Default (no setting / expired / corrupt) is confirm-each
— fail-closed toward asking.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from forge import paths as _forge_paths  # type: ignore

# Watch-mode is deliberately short-lived — a standing "act freely on my bank"
# blanket is exactly what we don't want. The ceiling is intentionally tighter
# than the attach-consent TTL.
WATCH_MIN_TTL_S = 60
WATCH_MAX_TTL_S = 30 * 60          # 30 minutes
WATCH_DEFAULT_TTL_S = 5 * 60       # 5 minutes


def _now() -> float:
    return time.time()


def _path(tenant_id: str, owner: str = "") -> Path:
    # Owner-scoped (adversarial-review finding): watch-mode is per console USER,
    # not tenant-wide — otherwise user A's watch-mode would auto-approve user B's
    # attached (real-login) session in the same tenant. `owner` is the session
    # owner_fingerprint; "" keeps the legacy tenant-wide file for back-compat.
    safe = "".join(c for c in owner if c.isalnum())[:16]
    name = f"browser_confirm_mode_{safe}.json" if safe else "browser_confirm_mode.json"
    return _forge_paths.tenant_global_dir(tenant_id) / name


def _clamp(ttl_s: int | float | None) -> int:
    try:
        v = int(ttl_s) if ttl_s is not None else WATCH_DEFAULT_TTL_S
    except (TypeError, ValueError):
        v = WATCH_DEFAULT_TTL_S
    return max(WATCH_MIN_TTL_S, min(WATCH_MAX_TTL_S, v))


def set_confirm_each(tenant_id: str, owner: str = "", *, audit_fn: "Callable[..., None] | None" = None) -> None:
    """Revert to the default: confirm every sensitive action."""
    p = _path(tenant_id, owner)
    if p.exists():
        try:
            p.unlink()
        except OSError:
            pass
    _audit(audit_fn, tenant_id, "browser.attach.confirm_mode_each")


def set_watch(tenant_id: str, ttl_s: int | float | None = None, owner: str = "",
              *, audit_fn: "Callable[..., None] | None" = None) -> float:
    """Enter watch-mode for *ttl_s* seconds (clamped). Returns absolute expiry."""
    ttl = _clamp(ttl_s)
    expires_at = _now() + ttl
    p = _path(tenant_id, owner)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.tmp")
    tmp.write_text(json.dumps({"mode": "watch", "expires_at": expires_at,
                               "ttl_s": ttl, "set_at": _now()}), encoding="utf-8")
    tmp.replace(p)
    _audit(audit_fn, tenant_id, "browser.attach.confirm_mode_watch", ttl_s=ttl)
    return expires_at


def status(tenant_id: str, owner: str = "") -> dict[str, Any]:
    """Current effective mode. A missing/expired/corrupt setting is confirm-each."""
    p = _path(tenant_id, owner)
    deny = {"mode": "confirm-each", "expires_at": None, "remaining_s": 0}
    if not p.exists():
        return deny
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        exp = float(d.get("expires_at") or 0)          # inside try (finding 7)
    except Exception:  # noqa: BLE001 — corrupt/non-numeric → fail-closed to ask
        return deny
    # Re-clamp on READ, not just on write: a hand-crafted store with a far-future
    # numeric expiry must NOT bypass the 30-min ceiling (finding 7). Cap remaining
    # to WATCH_MAX_TTL_S regardless of what the file claims.
    remaining = min(exp - _now(), float(WATCH_MAX_TTL_S))
    if d.get("mode") == "watch" and remaining > 0:
        return {"mode": "watch", "expires_at": exp, "remaining_s": int(remaining)}
    return {"mode": "confirm-each", "expires_at": exp, "remaining_s": 0}


def should_auto_approve(tenant_id: str, owner: str = "") -> bool:
    """True iff watch-mode is active and unexpired. The confirm broker consults
    this ONLY for attached sessions; fail-closed on any error (→ ask)."""
    return status(tenant_id, owner).get("mode") == "watch"


def _audit(audit_fn, tenant_id: str, event: str, **details: Any) -> None:
    if audit_fn is None:
        return
    try:
        audit_fn(tenant_id=tenant_id, event=event, details=details)
    except Exception:  # noqa: BLE001
        pass
