"""Live model-usage signal — one POST per finished engine span (ADR-0851).

Called from ``engine_span.emit_end`` (the one emitter the OS, console, AWP and A2A
paths share), so the record goes out as the span closes, not at session end.
The payload is the model the engine REPORTED, never a configured id, plus the
four-way token split. Metadata only: no prompt, output or transcript text, and
no tenant id.

Consent: the same ``ping_enabled`` opt-out as the other anonymous instance
channels (``spec.telemetry.ping_enabled: false`` disables it). A span whose
engine reported no model is not sent: an unknown model is not a ranking entry.

The send runs on a daemon thread with a short timeout and never raises, so a
slow or unreachable stats endpoint can not delay or fail a turn.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_TIMEOUT_S = 5
_STATE_FILENAME = "model_usage_state.json"
_state_lock = threading.Lock()


def state_path(home: Path) -> Path:
    """Where the send outcomes live; the console transparency panel renders it."""
    return Path(home) / "aco" / "telemetry" / _STATE_FILENAME


def read_state(home: Path) -> dict:
    """The recorded outcomes, or ``{}`` when nothing was ever attempted."""
    try:
        data = json.loads(state_path(home).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _record_state(home: Path, *, ok: bool, detail: str, url: str) -> None:
    """Outcomes only — timestamps, an HTTP status or exception class, counters.
    Never the payload, never a token. Must not break the send."""
    try:
        with _state_lock:
            p = state_path(home)
            p.parent.mkdir(parents=True, exist_ok=True)
            st = read_state(home)
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            st["last_attempt"] = now
            st["last_detail"] = detail[:64]
            st["attempts"] = int(st.get("attempts") or 0) + 1
            if ok:
                st["last_success"] = now
                st["successes"] = int(st.get("successes") or 0) + 1
                st["consecutive_failures"] = 0
            else:
                st["consecutive_failures"] = int(st.get("consecutive_failures") or 0) + 1
            st["url"] = url
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(st, sort_keys=True), encoding="utf-8")
            tmp.replace(p)
    except Exception:  # noqa: BLE001
        pass
_ROLES = frozenset({"os", "manager", "worker"})
_RE_MODEL = re.compile(r"^[a-z0-9][a-z0-9._:/-]{0,95}\Z")
_RE_ENGINE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}\Z")
_RE_STATUS = re.compile(r"^[a-z_]{1,24}\Z")
_TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def _endpoint() -> str:
    # Imported lazily: htrace_uploader resolves the base URL at its own import.
    from core.console.corvin_core.aco.htrace_uploader import _PING_BASE  # noqa: PLC0415
    return f"{_PING_BASE}/api/telemetry/model-usage"


def build_payload(details: dict[str, Any]) -> dict[str, Any] | None:
    """The closed model-usage record for one span, or None when it must not be sent."""
    model_id = details.get("model_id") or ""
    if not isinstance(model_id, str) or not _RE_MODEL.match(model_id):
        return None
    engine_id = details.get("engine_id") or ""
    if not isinstance(engine_id, str) or not _RE_ENGINE.match(engine_id):
        engine_id = ""
    role = details.get("role") or "os"
    if role not in _ROLES:
        return None
    span_status = details.get("status") or "ok"
    if not isinstance(span_status, str) or not _RE_STATUS.match(span_status):
        span_status = "unknown"
    payload: dict[str, Any] = {
        "event_type": "model_usage",
        "model_id": model_id,
        "engine_id": engine_id,
        "role": role,
        "status": span_status,
    }
    for field in _TOKEN_FIELDS:
        value = details.get(field, 0)
        payload[field] = int(value) if isinstance(value, int) and not isinstance(value, bool) else 0
    return payload


def _post(home: Path, payload: dict[str, Any]) -> None:
    try:
        from core.console.corvin_core.aco.htrace_consent import (  # noqa: PLC0415
            _open_no_redirect,
            load_or_create_instance_id,
        )
        from core.console.corvin_core.aco.htrace_uploader import (  # noqa: PLC0415
            _load_instance_token,
            _load_telemetry_token,
            _telemetry_user_agent,
        )
        import urllib.request  # noqa: PLC0415

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_load_telemetry_token(home)}",
            "X-HTTrace-Instance-Token": _load_instance_token(home),
            "X-HTrace-Instance-Id": load_or_create_instance_id(home),
            "User-Agent": _telemetry_user_agent("ModelUsage"),
        }
        url = _endpoint()
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers=headers,
        )
        try:
            with _open_no_redirect(req, _TIMEOUT_S) as resp:
                status = resp.getcode()
        except urllib.error.HTTPError as http_err:
            status = int(http_err.code)
        ok = 200 <= status < 300
        _record_state(home, ok=ok, detail=f"http {status}", url=url)
        if not ok:
            logger.debug("model-usage: stats endpoint returned %d", status)
    except Exception as exc:  # noqa: BLE001 — fail-soft: never reaches the turn
        _record_state(home, ok=False, detail=type(exc).__name__, url="")
        logger.debug("model-usage: send failed (non-fatal): %s", type(exc).__name__)


def send_span_end(details: dict[str, Any]) -> bool:
    """Send one model-usage record for a finished span. Returns True when a send was started."""
    if os.environ.get("CORVIN_MODEL_USAGE_LIVE", "1").strip() == "0":
        return False
    try:
        # Top-level import on purpose: callers load this package's modules as
        # top-level names (``import engine_span``), so a relative import would
        # fail there and silently disable the signal.
        from paths import corvin_home  # type: ignore  # noqa: PLC0415
        from core.console.corvin_core.aco.htrace_consent import ping_enabled  # noqa: PLC0415

        home = corvin_home()
        if not ping_enabled(home):
            return False
        payload = build_payload(details)
        if payload is None:
            return False
    except Exception:  # noqa: BLE001
        return False
    threading.Thread(target=_post, args=(home, payload), name="model-usage-live", daemon=True).start()
    return True
