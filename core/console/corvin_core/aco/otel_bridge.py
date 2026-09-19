"""OTLP export of the presence heartbeat (ADR-0680/0681), wired 2026-09-20.

``core/observability/otel_exporter`` shipped an ``OTELExporter`` with a
gRPC exporter pointed at ``localhost:4318`` and no production caller. This
bridge is that caller: every heartbeat cycle (``aco/heartbeat.py``) it
records the four ``corvin.instance.*`` gauges and pushes them over OTLP/HTTP
(protobuf) to the Corvin-Features intake ``/v1/telemetry/otlp/v1/metrics``,
authenticated with the same HMAC token pair as the heartbeat itself. When
the push fails the exporter's JSON fallback keeps the record locally
(``<home>/telemetry/otel/``) — zero telemetry loss, as the ADR demands — and
``<home>/telemetry/otel_state.json`` records the outcome for the console.

Endpoint resolution: ``OTEL_EXPORTER_OTLP_METRICS_ENDPOINT`` →
``OTEL_EXPORTER_OTLP_ENDPOINT`` (+ ``/v1/metrics``) → ``spec.telemetry.otlp_endpoint``
in tenant.corvin.yaml → the Corvin-Features intake. Gate: the same
``ping_enabled`` opt-out as the heartbeat (the loop checks it before calling).

What leaves: ``corvin.instance.online`` (0/1), ``corvin.instance.uptime``
(s), ``corvin.instance.plugin_count``, ``corvin.instance.memory_usage`` (By),
each with ``tenant_id``, ``instance_id`` (random uuid4), ``platform``,
``python_version``; resource ``service.name``/``service.version``. Nothing
else — the intake drops anything outside that allowlist.
"""
from __future__ import annotations

import json
import logging
import os
import platform as _platform
import resource
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_STATE_FILENAME = "otel_state.json"
_lock = threading.Lock()
_exporter: Any = None
_exporter_endpoint: Optional[str] = None
_process_start = time.time()

METRIC_NAMES = ("corvin.instance.online", "corvin.instance.uptime", "corvin.instance.plugin_count", "corvin.instance.memory_usage")
ATTRIBUTE_KEYS = ("tenant_id", "instance_id", "platform", "python_version")


def state_path(home: Path) -> Path:
    return Path(home) / "telemetry" / _STATE_FILENAME


def fallback_dir(home: Path) -> Path:
    return Path(home) / "telemetry" / "otel"


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
    except Exception:  # noqa: BLE001 — state must never break the export
        pass


def resolve_endpoint(home: Path) -> str:
    env = os.environ.get("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "").strip()
    if env:
        return env
    env = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if env:
        return env.rstrip("/") + "/v1/metrics"
    try:
        from .htrace_consent import _tenant_cfg_path  # noqa: PLC0415
        import yaml  # noqa: PLC0415

        data = yaml.safe_load(_tenant_cfg_path(home).read_text(encoding="utf-8"))
        tele = (data.get("spec", data) if isinstance(data, dict) else {}).get("telemetry", {})
        cfg = str((tele or {}).get("otlp_endpoint") or "").strip()
        if cfg:
            return cfg
    except Exception:  # noqa: BLE001 — absent/broken config → default
        pass
    from .htrace_uploader import _TELEMETRY_BASE  # noqa: PLC0415

    return f"{_TELEMETRY_BASE}/v1/telemetry/otlp/v1/metrics"


def _auth_headers(home: Path) -> Dict[str, str]:
    from .htrace_consent import load_or_create_instance_id  # noqa: PLC0415
    from .htrace_uploader import _load_instance_token, _load_telemetry_token, _telemetry_user_agent  # noqa: PLC0415

    return {
        "Authorization": f"Bearer {_load_telemetry_token(home)}",
        "X-HTTrace-Instance-Token": _load_instance_token(home),
        "X-HTrace-Instance-Id": load_or_create_instance_id(home),
        "User-Agent": _telemetry_user_agent("OTLP"),
    }


def _plugin_count() -> int:
    try:
        from corvin_plugins import registry as _reg  # type: ignore[import]  # noqa: PLC0415

        for name in ("loaded_plugins", "all_plugins", "plugins"):
            fn = getattr(_reg, name, None)
            if callable(fn):
                val = fn()
                return len(val) if hasattr(val, "__len__") else 0
    except Exception:  # noqa: BLE001
        pass
    return 0


def _memory_bytes() -> int:
    try:
        rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(rss_kb) * (1 if sys.platform == "darwin" else 1024)
    except Exception:  # noqa: BLE001
        return 0


def _get_exporter(home: Path):
    """One exporter per process, rebuilt when the endpoint changes."""
    global _exporter, _exporter_endpoint  # noqa: PLW0603
    endpoint = resolve_endpoint(home)
    with _lock:
        if _exporter is not None and _exporter_endpoint == endpoint:
            return _exporter
        from core.observability.otel_exporter.exporter import OTELExporter  # noqa: PLC0415
        from .htrace_consent import load_or_create_instance_id  # noqa: PLC0415

        _exporter = OTELExporter(
            tenant_id="_default",
            instance_id=load_or_create_instance_id(home),
            json_fallback_dir=fallback_dir(home),
            otel_collector_url=endpoint,
            headers=_auth_headers(home),
            audit_logger=logging.getLogger("corvin.telemetry.otel"),
        )
        _exporter_endpoint = endpoint
        return _exporter


def export_heartbeat_metrics(home: Path, *, is_alive: bool = True) -> Dict[str, Any]:
    """Record the gauges and push them now. Returns the outcome it also writes
    to ``otel_state.json``. Never raises."""
    home = Path(home)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        exporter = _get_exporter(home)
        ok, message = exporter.export_heartbeat(
            is_alive=is_alive,
            uptime_seconds=int(time.time() - _process_start),
            plugin_count=_plugin_count(),
            memory_usage_bytes=_memory_bytes(),
            platform=sys.platform if sys.platform in ("linux", "win32", "darwin") else "other",
            python_version=f"{sys.version_info[0]}.{sys.version_info[1]}",
            geo_attrs=None,
        )
        detail = getattr(exporter, "last_export_detail", None) or message
        st = read_state(home)
        fields: Dict[str, Any] = {
            "last_attempt": now, "last_detail": str(detail)[:96], "endpoint": _exporter_endpoint,
            "transport": getattr(exporter, "transport", "unknown"),
            "attempts": int(st.get("attempts") or 0) + 1,
            "sdk_version": getattr(exporter, "sdk_version", None),
        }
        if ok:
            fields.update(last_success=now, successes=int(st.get("successes") or 0) + 1, consecutive_failures=0)
        else:
            fields.update(consecutive_failures=int(st.get("consecutive_failures") or 0) + 1,
                          fallback_writes=int(st.get("fallback_writes") or 0) + 1)
        _write_state(home, **fields)
        return {"ok": ok, "detail": detail, "endpoint": _exporter_endpoint}
    except Exception as exc:  # noqa: BLE001 — fail-soft
        logger.debug("otel bridge: export failed (non-fatal): %s", exc)
        st = read_state(home)
        _write_state(home, last_attempt=now, last_detail=type(exc).__name__,
                     attempts=int(st.get("attempts") or 0) + 1,
                     consecutive_failures=int(st.get("consecutive_failures") or 0) + 1)
        return {"ok": False, "detail": type(exc).__name__, "endpoint": _exporter_endpoint}


def reset_for_tests() -> None:
    global _exporter, _exporter_endpoint  # noqa: PLW0603
    with _lock:
        _exporter = None
        _exporter_endpoint = None


__all__ = ["export_heartbeat_metrics", "resolve_endpoint", "read_state", "state_path", "fallback_dir", "METRIC_NAMES", "ATTRIBUTE_KEYS", "_platform"]
