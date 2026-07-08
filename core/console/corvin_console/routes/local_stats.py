"""Local instance stats — GET /v1/console/local-stats.

Returns a snapshot of this CorvinOS instance's current state from purely
local sources (no remote API calls).  Used by the /local-stats dashboard
page served by standalone.py / gateway app.py.

Auth: unauthenticated but LOOPBACK-ONLY (enforced).  The data is low-value
(version, platform, uptime, instance-id prefix, session count) yet still
useful for remote recon, and the standalone server binds 0.0.0.0 — so the
docstring's old "localhost-only" claim was aspirational, not enforced
(PENTEST-10).  We now reject any request whose client address is not a
loopback address, which keeps the local dashboard JS working without a
login while denying remote unauthenticated callers.
"""
from __future__ import annotations

import importlib.metadata
import ipaddress
import sys
import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .. import _bootstrap

_forge_paths = _bootstrap.forge_paths

router = APIRouter()

# Process start-time — approximated once at import time so uptime is stable.
_STARTED_AT: float = time.time()


def _active_engine(tenant_id: str) -> str:
    try:
        import yaml  # type: ignore
        cfg = _forge_paths.tenant_global_dir(tenant_id) / "tenant.corvin.yaml"
        if not cfg.exists():
            return "unknown"
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        spec = data.get("spec", data)
        engine = (spec.get("worker_engine")
                  or spec.get("default_worker_engine")
                  or spec.get("os_engine")
                  or "unknown")
        return str(engine)
    except Exception:
        return "unknown"


def _heartbeat_alive() -> bool:
    return any(t.name == "corvin-heartbeat" and t.is_alive() for t in threading.enumerate())


def _instance_id() -> str:
    try:
        from corvin_console.aco.htrace_consent import load_or_create_instance_id  # type: ignore
        home = _forge_paths.corvin_home()
        iid = load_or_create_instance_id(home)
        return iid[:8] + "…" if iid else "—"
    except Exception:
        return "—"


def _ping_enabled() -> bool:
    try:
        from corvin_console.aco.htrace_consent import ping_enabled  # type: ignore
        home = _forge_paths.corvin_home()
        return ping_enabled(home)
    except Exception:
        return True


def _active_sessions() -> int:
    try:
        from .. import chat_runtime
        sessions = chat_runtime.list_sessions(limit=1000)
        return len([s for s in sessions if getattr(s, "last_active_at", 0) > time.time() - 300])
    except Exception:
        return -1


def _client_is_loopback(request: Request) -> bool:
    """True only when the request's peer address is a loopback address.

    Fail-closed: a missing/unparseable client address is treated as NON-loopback
    (rejected). The bare string ``"localhost"`` is accepted for the rare
    transports that report a hostname instead of an IP."""
    client = request.client
    if client is None:
        return False
    host = (client.host or "").strip()
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@router.get("/local-stats")
def local_stats(request: Request) -> dict[str, Any]:
    """Return a local instance snapshot — no remote API calls, loopback-only.

    PENTEST-10: reject non-loopback callers so a remote unauthenticated client
    cannot read version / platform / engine / instance-id-prefix / session-count
    for reconnaissance. Local dashboards (browser on 127.0.0.1 / ::1) still work
    without a login."""
    if not _client_is_loopback(request):
        raise HTTPException(status_code=403, detail="local-stats is loopback-only")
    tenant_id = "_default"
    try:
        version = importlib.metadata.version("corvinos")
    except Exception:
        version = "dev"

    uptime_s = int(time.time() - _STARTED_AT)
    uptime_h = uptime_s // 3600
    uptime_m = (uptime_s % 3600) // 60

    return {
        "version":         version,
        "platform":        sys.platform,
        "python":          f"{sys.version_info.major}.{sys.version_info.minor}",
        "engine":          _active_engine(tenant_id),
        "instance_id":     _instance_id(),
        "ping_enabled":    _ping_enabled(),
        "heartbeat_alive": _heartbeat_alive(),
        "active_sessions": _active_sessions(),
        "uptime_seconds":  uptime_s,
        "uptime_label":    f"{uptime_h}h {uptime_m:02d}m" if uptime_h else f"{uptime_m}m",
        "sampled_at":      time.strftime("%H:%M:%S", time.localtime()),
    }
