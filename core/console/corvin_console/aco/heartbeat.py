"""Lightweight presence heartbeat — fires every 5 minutes while CorvinOS runs.

Sends an authenticated POST /v1/telemetry/heartbeat to api.corvin-labs.com.
Uses the same HMAC token triple as the daily ping (no new credentials needed).
Fail-soft: never raises, never blocks the server.

Start with start_heartbeat_thread(home). Only one thread is ever started
(module-level guard). The thread is a daemon so it dies automatically when
the process exits.
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
import urllib.request
from pathlib import Path

from .htrace_uploader import (
    _TELEMETRY_BASE,
    _load_telemetry_token,
    _load_instance_token,
)
from .htrace_consent import (
    load_or_create_instance_id,
    ping_enabled,
)

logger = logging.getLogger(__name__)

_HEARTBEAT_URL = f"{_TELEMETRY_BASE}/v1/telemetry/heartbeat"
_HEARTBEAT_TIMEOUT_S = 5
_INTERVAL = 5 * 60   # 5 minutes
_JITTER = 30         # ±30s random jitter to avoid thundering herd

_started = False
_started_lock = threading.Lock()


def send_heartbeat(home: Path) -> bool:
    """Send a single POST /v1/telemetry/heartbeat.

    Returns True on 2xx, False on any error.
    Fail-soft: catches all exceptions.
    """
    try:
        telemetry_token = _load_telemetry_token(home)
        instance_token = _load_instance_token(home)
        instance_id = load_or_create_instance_id(home)

        payload = json.dumps({}).encode("utf-8")
        req = urllib.request.Request(
            _HEARTBEAT_URL,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {telemetry_token}",
                "X-HTTrace-Instance-Token": instance_token,
                "X-HTrace-Instance-Id": instance_id,
            },
        )
        with urllib.request.urlopen(req, timeout=_HEARTBEAT_TIMEOUT_S) as resp:
            status = resp.getcode()
            if 200 <= status < 300:
                return True
            logger.debug("heartbeat: returned %d", status)
            return False
    except Exception as e:  # noqa: BLE001
        logger.debug("heartbeat: failed (non-fatal): %s", e)
        return False


def _heartbeat_loop(home: Path) -> None:
    """Run forever: wait one interval, then heartbeat every 5 minutes.

    Re-checks ``ping_enabled(home)`` on EVERY iteration (adversarial review
    finding: this previously only checked opt-out once, at thread start —
    a user opting out mid-session on a long-running server kept getting
    heartbeats sent for the rest of the process lifetime, sometimes weeks.
    Mirrors ``ping_if_due``'s own per-call re-check).
    """
    # Wait one interval before first heartbeat (daily ping already ran at boot)
    time.sleep(_INTERVAL + random.randint(-_JITTER, _JITTER))
    while True:
        try:
            if ping_enabled(home):
                send_heartbeat(home)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(_INTERVAL + random.randint(-_JITTER, _JITTER))


def start_heartbeat_thread(home: Path) -> None:
    """Start the 5-minute presence heartbeat daemon thread (idempotent).

    Only one thread is ever started per process (module-level ``_started``
    guard). Respects ``ping_enabled(home)`` — if the user opted out of the
    anonymous instance ping, the heartbeat is also skipped.
    """
    global _started  # noqa: PLW0603
    with _started_lock:
        if _started:
            return
        if not ping_enabled(home):
            logger.debug("heartbeat: skipped — ping_enabled is false")
            return
        t = threading.Thread(
            target=_heartbeat_loop,
            args=(home,),
            daemon=True,
            name="corvin-heartbeat",
        )
        t.start()
        _started = True
        logger.debug("heartbeat: thread started")
