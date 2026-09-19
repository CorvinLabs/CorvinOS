"""Feature-stability digest sender (ADR-0288), wired 2026-09-20.

``core.telemetry.telemetry_daemon`` computed an hourly digest of feature-flag
invocation/error counts and POSTed it through a caller-supplied ``send_fn`` —
and no host ever initialised it, and nothing fed the counters. Now:

* ``corvin_core.feature_flags.is_enabled`` counts every evaluation;
* the gateway lifespan starts the daemon through :func:`start_stability_daemon`
  (gated by the same ``ping_enabled`` opt-out as ping and heartbeat, re-checked
  before every send);
* the digest goes to the Corvin-Features intake
  ``/v1/telemetry/feature-stability`` with the instance's HMAC token pair;
* ``<home>/telemetry/stability_state.json`` records the outcome for the console.

What leaves: flag ids, release tier, ``enabled_by`` (a config source label),
invocation/error counts and rate over 24 h, days since last error — never a
message, never a value a flag gates.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_STATE_FILENAME = "stability_state.json"
_INTERVAL_S = 3600
_FIRST_DELAY_S = 300


def endpoint() -> str:
    from .htrace_uploader import _TELEMETRY_BASE  # noqa: PLC0415

    return f"{_TELEMETRY_BASE}/v1/telemetry/feature-stability"


def state_path(home: Path) -> Path:
    return Path(home) / "telemetry" / _STATE_FILENAME


def read_state(home: Path) -> Dict[str, Any]:
    try:
        data = json.loads(state_path(home).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(home: Path, **fields: Any) -> None:
    try:
        st = read_state(home)
        st.update(fields)
        p = state_path(home)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(st, sort_keys=True), encoding="utf-8")
        tmp.replace(p)
    except Exception:  # noqa: BLE001
        pass


def digest_kwargs(home: Path) -> Dict[str, Any]:
    """Identity for ``compute_digest``: the real instance id and where the
    flags come from — never the daemon's placeholders."""
    from .htrace_consent import load_or_create_instance_id  # noqa: PLC0415

    return {
        "tenant_id": "_default",
        "instance_id": load_or_create_instance_id(Path(home)),
        "enabled_by": "tenant.corvin.yaml",
    }


def send_digest(home: Path, payload: dict, *, url: Optional[str] = None) -> int:
    """POST one digest; returns the HTTP status (0 on transport failure).
    Gated by ``ping_enabled`` at call time so an opt-out mid-process holds."""
    import urllib.error  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    from .htrace_consent import _open_no_redirect, load_or_create_instance_id, ping_enabled  # noqa: PLC0415
    from .htrace_uploader import _load_instance_token, _load_telemetry_token, _telemetry_user_agent  # noqa: PLC0415

    home = Path(home)
    if not ping_enabled(home):
        return 0
    url = url or endpoint()
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_load_telemetry_token(home)}",
        "X-HTTrace-Instance-Token": _load_instance_token(home),
        "X-HTrace-Instance-Id": load_or_create_instance_id(home),
        "User-Agent": _telemetry_user_agent("FeatureStability"),
    })
    try:
        with _open_no_redirect(req, 15) as resp:
            return int(resp.getcode())
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except Exception as exc:  # noqa: BLE001
        logger.debug("stability digest: send failed (non-fatal): %s", exc)
        return 0


def record_result(home: Path, status: int, payload: dict) -> None:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    st = read_state(home)
    fields: Dict[str, Any] = {
        "last_attempt": now, "last_status": status, "endpoint": endpoint(),
        "attempts": int(st.get("attempts") or 0) + 1,
        "last_flags": len(payload.get("flags_enabled") or []),
    }
    if 200 <= status < 300:
        fields.update(last_success=now, successes=int(st.get("successes") or 0) + 1, consecutive_failures=0)
    else:
        fields.update(consecutive_failures=int(st.get("consecutive_failures") or 0) + 1)
    _write_state(Path(home), **fields)


def start_stability_daemon(home: Path):
    """Initialise + start the ADR-0288 daemon on the running event loop.
    Returns the daemon, or ``None`` when opted out. Idempotent per process."""
    from .htrace_consent import ping_enabled  # noqa: PLC0415
    from core.telemetry import telemetry_daemon as td  # noqa: PLC0415

    home = Path(home)
    existing = td.get_daemon()
    if existing is not None and getattr(existing, "_task", None) is not None:
        return existing
    enabled = ping_enabled(home)
    daemon = td.initialize_daemon(
        send_fn=lambda payload: send_digest(home, payload),
        enabled=enabled,
        interval_seconds=_INTERVAL_S,
        first_delay_seconds=_FIRST_DELAY_S,
        digest_kwargs=lambda: digest_kwargs(home),
        on_result=lambda status, payload: record_result(home, status, payload),
    )
    if not enabled:
        _write_state(home, disabled=True)
        return None
    daemon.start()
    _write_state(home, started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), endpoint=endpoint(),
                 interval_seconds=_INTERVAL_S, first_delay_seconds=_FIRST_DELAY_S, disabled=False)
    return daemon
