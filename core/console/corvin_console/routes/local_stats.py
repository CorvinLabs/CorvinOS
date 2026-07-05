"""Local instance stats — GET /v1/console/local-stats.

Returns a snapshot of this CorvinOS instance's current state from purely
local sources (no remote API calls).  Used by the /local-stats dashboard
page served by standalone.py.

Auth: requires a valid console session so the endpoint isn't world-readable.
"""
from __future__ import annotations

import importlib.metadata
import sys
import threading
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from .. import auth as session_auth
from ..deps import require_session
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


def _instance_id(tenant_id: str) -> str:
    try:
        from corvin_console.aco.htrace_consent import load_or_create_instance_id  # type: ignore
        home = _forge_paths.corvin_home()
        iid = load_or_create_instance_id(home)
        return iid[:8] + "…" if iid else "—"
    except Exception:
        return "—"


def _ping_enabled(tenant_id: str) -> bool:
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


@router.get("/local-stats")
def local_stats(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return a local instance snapshot — no remote API calls."""
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
        "engine":          _active_engine(rec.tenant_id),
        "instance_id":     _instance_id(rec.tenant_id),
        "ping_enabled":    _ping_enabled(rec.tenant_id),
        "heartbeat_alive": _heartbeat_alive(),
        "active_sessions": _active_sessions(),
        "uptime_seconds":  uptime_s,
        "uptime_label":    f"{uptime_h}h {uptime_m:02d}m" if uptime_h else f"{uptime_m}m",
        "sampled_at":      time.strftime("%H:%M:%S", time.localtime()),
    }
