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
#: failure class). An ALLOW is therefore re-evaluated (and audited) once per window; a DENY is
#: never cached, so a lapsed or revoked licence is enforced at the next call after the window.
_ALLOW_CACHE: dict[str, float] = {}
ALLOW_CACHE_TTL_S = 600.0


def reset_allow_cache() -> None:
    """Forget remembered ALLOWs (licence reload, tests)."""
    _ALLOW_CACHE.clear()


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
    try:
        decision = require_capability(
            CAPABILITY, requested=1,
            tenant_id=tid,
            entry_point=entry_point,
        )
    except LicenseDenied as exc:
        _ALLOW_CACHE.pop(tid, None)
        raise A2ANetworkDenied(f"{CAPABILITY} denied: {UPGRADE_HINT}") from exc
    except Exception as exc:  # noqa: BLE001 — enforcement failure = deny
        _log.warning("a2a.network enforcement failure (%s): %s",
                     type(exc).__name__, exc)
        raise A2ANetworkDenied(
            f"{CAPABILITY} denied: licence enforcement unavailable"
        ) from exc
    if getattr(decision, "decision", None) is not Decision.ALLOW:
        raise A2ANetworkDenied(f"{CAPABILITY} denied: no allow verdict")
    _ALLOW_CACHE[tid] = _time.monotonic() + ALLOW_CACHE_TTL_S
