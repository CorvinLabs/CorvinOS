"""Health and metrics endpoints for the Instance Agent.

Exposed at:
  GET /health   — structured health check (JSON)
  GET /metrics  — Prometheus text format (basic counters)

Both endpoints are unauthenticated by design: they are only reachable
from the Management API over mTLS, so the network layer provides auth.
The health endpoint MUST NOT expose PII or secret material.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

_START_TIME = time.time()
_BYOK_ROTATIONS: dict[str, int] = {}   # key_name → count (in-process only)
_CONFIG_PUSHES: int = 0


def record_byok_rotation(key_name: str) -> None:
    _BYOK_ROTATIONS[key_name] = _BYOK_ROTATIONS.get(key_name, 0) + 1


def record_config_push() -> None:
    global _CONFIG_PUSHES
    _CONFIG_PUSHES += 1


def health_payload(*, agent_dir: Path | None = None) -> dict[str, Any]:
    """Return the /health response dict."""
    tid = os.environ.get("CORVIN_TENANT_ID", "_default")
    uptime = time.time() - _START_TIME

    keypair_ready = False
    if agent_dir is not None:
        keypair_ready = (
            (agent_dir / "byok_privkey.pem").exists()
            and (agent_dir / "byok_pubkey.pem").exists()
        )

    mtls_cert_present = False
    if agent_dir is not None:
        mtls_cert_present = (agent_dir / "mtls_cert.pem").exists()

    return {
        "ok": True,
        "uptime_s": round(uptime, 1),
        "tenant_id": tid,
        "keypair_ready": keypair_ready,
        "mtls_cert_present": mtls_cert_present,
        "byok_rotations_total": sum(_BYOK_ROTATIONS.values()),
        "config_pushes_total": _CONFIG_PUSHES,
        "ts": time.time(),
    }


def metrics_text(*, agent_dir: Path | None = None) -> str:
    """Return Prometheus text-format metrics (basic)."""
    tid = os.environ.get("CORVIN_TENANT_ID", "_default")
    uptime = time.time() - _START_TIME
    lines = [
        f'# HELP corvin_agent_uptime_seconds Time since agent start',
        f'# TYPE corvin_agent_uptime_seconds gauge',
        f'corvin_agent_uptime_seconds{{tenant="{tid}"}} {uptime:.1f}',
        f'# HELP corvin_agent_byok_rotations_total BYOK secret rotations',
        f'# TYPE corvin_agent_byok_rotations_total counter',
        f'corvin_agent_byok_rotations_total{{tenant="{tid}"}} {sum(_BYOK_ROTATIONS.values())}',
        f'# HELP corvin_agent_config_pushes_total Config-push events received',
        f'# TYPE corvin_agent_config_pushes_total counter',
        f'corvin_agent_config_pushes_total{{tenant="{tid}"}} {_CONFIG_PUSHES}',
    ]
    return "\n".join(lines) + "\n"
