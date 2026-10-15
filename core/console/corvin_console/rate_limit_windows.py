"""Provider quota windows observed in the Claude CLI stream.

``claude -p --output-format stream-json`` emits a ``rate_limit_event`` whose
``rate_limit_info`` carries the subscription windows the account is counted
against (``five_hour``, ``seven_day``): the reset instant (unix seconds) and the
utilisation (0..1) at that moment. Nothing else in this repo knows when a quota
resets — the other "reset" values (license/skill quotas) are computed UTC
midnights — so this is the only provider-sourced reset time there is.

What is stored: the LAST observation per tenant, numbers and a closed window-name
vocabulary only. No session id, uuid or overage reason is kept. The value is as
fresh as the last turn that ran the CLI; ``observed_at`` travels with it so the
panel can say so instead of presenting a stale utilisation as current.

Shape of ``<tenant>/global/rate_limit_windows.json``::

    {"version": 1, "observed_at": 1791500000.0, "status": "allowed",
     "windows": {"five_hour": {"utilization": 0.08, "resets_at": 1791567000.0}, ...}}
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

_FILENAME = "rate_limit_windows.json"
_SCHEMA_VERSION = 1
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_STATUS_RE = re.compile(r"^[a-z_]{1,32}$")
_MAX_WINDOWS = 8


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def parse(evt: dict[str, Any]) -> dict[str, Any] | None:
    """The storable record for one stream event, or None if it carries no window."""
    info = evt.get("rate_limit_info")
    if not isinstance(info, dict):
        return None
    windows: dict[str, dict[str, float | None]] = {}
    unified = info.get("unifiedWindows")
    if isinstance(unified, dict):
        for name, w in list(unified.items())[:_MAX_WINDOWS]:
            if not (isinstance(name, str) and _NAME_RE.match(name) and isinstance(w, dict)):
                continue
            resets = _num(w.get("resetsAt"))
            if resets is None or resets <= 0:
                continue
            util = _num(w.get("utilization"))
            windows[name] = {
                "utilization": util if util is not None and util >= 0 else None,
                "resets_at": resets,
            }
    # An older / narrower event: one window named by rateLimitType.
    top_type, top_reset = info.get("rateLimitType"), _num(info.get("resetsAt"))
    if (isinstance(top_type, str) and _NAME_RE.match(top_type) and top_type not in windows
            and top_reset is not None and top_reset > 0):
        windows[top_type] = {"utilization": None, "resets_at": top_reset}
    if not windows:
        return None
    status = info.get("status")
    return {
        "version": _SCHEMA_VERSION,
        "observed_at": time.time(),
        "status": status if isinstance(status, str) and _STATUS_RE.match(status) else None,
        "windows": windows,
    }


def _path(tenant_id: str) -> Path | None:
    try:
        from core.paths import tenant_home  # noqa: PLC0415

        return Path(tenant_home(tenant_id)) / "global" / _FILENAME
    except Exception:  # noqa: BLE001
        return None


def read(tenant_id: str) -> dict[str, Any]:
    """The last observation, or ``{}`` — never raises (an unreadable file is "not observed")."""
    path = _path(tenant_id)
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("windows"), dict):
        return {}
    return data


def record(tenant_id: str, evt: dict[str, Any]) -> bool:
    """Persist a ``rate_limit_event`` for ``tenant_id``. Best effort: False on no-op/failure.

    Called from the chat stream loop, so it must never raise into a turn.
    """
    try:
        rec = parse(evt)
        path = _path(tenant_id)
        if rec is None or path is None:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".rate_limit_windows.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(rec, handle, indent=2, sort_keys=True)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    except Exception:  # noqa: BLE001
        return False
