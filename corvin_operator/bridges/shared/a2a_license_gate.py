"""A2A network membership gate — the ``a2a.network`` capability (ADR-0700 §2.1).

The A2A network is a MEMBER-only capability: an installation that sends,
pings or receives A2A traffic needs its own member seat. Free installs keep
every other feature; they just cannot join the network.

This module is the ONE place the three A2A transport chokepoints ask that
question:

* outbound — ``RemoteTriggerSender.send`` / ``ping`` / ``send_reconnect``
* inbound  — ``RemoteTriggerReceiver.receive`` (every transport — HTTP server,
  relay, Google adapter — funnels into it)

Contract (ADR-0703 §2.2): enforcement failure of any kind (import error,
tenant error, credential parse error) resolves to the FREE allowance, which for
a member-only capability is a deny — "the checker is missing" never reads as
"the check passed".

Honest limit (ADR-0703 §2.4): this local gate runs in-process on Apache-2.0
code and can be edited out of a fork. It is deterrence, not a lock. What holds
in a fork is class N — the PEER verifying the other side's Member Credential
with its own ring (ADR-0702), which is a separate, not-yet-built phase.
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

CAPABILITY = "a2a.network"
UPGRADE_HINT = "a2a.network requires a member seat"


class A2ANetworkDenied(PermissionError):
    """Raised when the local installation holds no member credential."""


#: (tenant_id) -> monotonic expiry of a remembered ALLOW. The connectivity manager pings every
#: ~30 s and `require_capability` audits every call: 4 347 identical `license.capability_decision`
#: records in three days on one idle member install (review 2026-10-21; CLAUDE.md names this
#: failure class). An ALLOW is therefore re-evaluated (and audited) once per window, so a lapsed
#: or revoked licence is enforced at the next call after the window.
_ALLOW_CACHE: dict[str, float] = {}
ALLOW_CACHE_TTL_S = 600.0
#: (tenant_id) -> monotonic expiry of a remembered DENY. A Free install that a member peer keeps
#: probing wrote 68 ``remote_trigger_receiver.receive`` deny records and 20 of its own
#: ``remote_trigger_sender.ping`` denies in ten idle minutes (measured on a fresh install, round 2,
#: 2026-10-21). Remembering a DENY delays only the deny→allow transition, by at most this window —
#: fail-closed — and a licence reload that picks up a new token (``validator.reload_from_disk``)
#: forgets it at once through :func:`reset_allow_cache`.
_DENY_CACHE: dict[str, float] = {}
DENY_CACHE_TTL_S = 60.0


#: (tenant_id) -> (monotonic expiry, verdict) of a STATUS read — ``network_status``. The console asks
#: on every poll of ``friendship/connections`` (15 s), ``a2a/diagnostics`` and ``GET /a2a/feed`` (4 s per open
#: peer chat), and every evaluation of a DENY writes ``license.capability_decision`` + ``license.free_tier``
#: to the chain: a Free install with one peer chat open wrote a deny pair every 4 s for a status badge
#: (measured on a fresh install, 2026-10-21 round 2). A status read is not an enforcement point — the send,
#: ping and receive chokepoints still call ``require_a2a_network`` and re-evaluate a DENY every time — so it
#: remembers BOTH verdicts for a short window. ``reset_allow_cache`` (licence reload) forgets it too.
_STATUS_CACHE: dict[str, tuple[float, dict[str, object]]] = {}
STATUS_CACHE_TTL_S = 30.0


def reset_allow_cache() -> None:
    """Forget remembered ALLOWs, DENYs and status verdicts (licence reload, tests)."""
    _ALLOW_CACHE.clear()
    _DENY_CACHE.clear()
    _STATUS_CACHE.clear()


def network_status(*, entry_point: str, tenant_id: str | None = None) -> dict[str, object]:
    """``{"allowed": bool, "reason": str | None}`` for a READ-ONLY status display.

    The verdict is ``require_a2a_network``'s (same evaluation, same audit record when it evaluates),
    remembered for ``STATUS_CACHE_TTL_S`` whichever way it went. Fail-closed like the gate: an
    unavailable checker reads as ``licence_check_unavailable``, never as allowed. Never raises.
    """
    import time as _time  # noqa: PLC0415

    try:
        from corvin_operator.forge.forge.tenants import current_tenant  # noqa: PLC0415
        tid = tenant_id or current_tenant()
    except Exception:  # noqa: BLE001 — no tenant resolver: cache under the literal key
        tid = tenant_id or "_default"
    cached = _STATUS_CACHE.get(tid)
    now = _time.monotonic()
    if cached is not None and cached[0] > now:
        return dict(cached[1])
    try:
        require_a2a_network(entry_point=entry_point, tenant_id=tid)
        verdict: dict[str, object] = {"allowed": True, "reason": None}
    except A2ANetworkDenied as exc:
        unavailable = "unavailable" in str(exc) or "licensing module" in str(exc)
        verdict = {"allowed": False,
                   "reason": "licence_check_unavailable" if unavailable else "member_seat_required"}
    except Exception:  # noqa: BLE001 — anything else is a refusal, never a pass
        verdict = {"allowed": False, "reason": "licence_check_unavailable"}
    _STATUS_CACHE[tid] = (now + STATUS_CACHE_TTL_S, dict(verdict))
    return verdict


def require_a2a_network(*, entry_point: str, tenant_id: str | None = None) -> None:
    """Return normally iff this installation may use the A2A network.

    Raises :class:`A2ANetworkDenied` on every other outcome, including an
    enforcement failure (fail-closed).
    """
    try:
        try:
            import corvin_operator  # noqa: F401  # pylint: disable=unused-import
        except ImportError:  # pragma: no cover — script run without repo root
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
        from corvin_operator.license.capability_api import (
            Decision, LicenseDenied, require_capability,
        )
        from corvin_operator.forge.forge.tenants import current_tenant
    except ImportError as exc:
        raise A2ANetworkDenied(
            f"{CAPABILITY} denied: licensing module unavailable (fail-closed)"
        ) from exc
    import time as _time  # noqa: PLC0415

    tid = tenant_id or current_tenant()
    if _ALLOW_CACHE.get(tid, 0.0) > _time.monotonic():
        return
    if _DENY_CACHE.get(tid, 0.0) > _time.monotonic():
        raise A2ANetworkDenied(f"{CAPABILITY} denied: {UPGRADE_HINT}")
    try:
        decision = require_capability(
            CAPABILITY, requested=1,
            tenant_id=tid,
            entry_point=entry_point,
        )
    except LicenseDenied as exc:
        _ALLOW_CACHE.pop(tid, None)
        _DENY_CACHE[tid] = _time.monotonic() + DENY_CACHE_TTL_S
        raise A2ANetworkDenied(f"{CAPABILITY} denied: {UPGRADE_HINT}") from exc
    except Exception as exc:  # noqa: BLE001 — enforcement failure = deny
        _log.warning("a2a.network enforcement failure (%s): %s",
                     type(exc).__name__, exc)
        raise A2ANetworkDenied(
            f"{CAPABILITY} denied: licence enforcement unavailable"
        ) from exc
    if getattr(decision, "decision", None) is not Decision.ALLOW:
        raise A2ANetworkDenied(f"{CAPABILITY} denied: no allow verdict")
    _DENY_CACHE.pop(tid, None)
    _ALLOW_CACHE[tid] = _time.monotonic() + ALLOW_CACHE_TTL_S
