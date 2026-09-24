"""Resolve the console/A2A bind host from the ``a2a_lan_bind`` feature flag.

``python -m corvin_core.bind_host`` prints the host, so a systemd unit or a
shell launcher can pass it to uvicorn — the always-on ``corvin-webui.service``
template and ``bridge.sh`` hard-coded ``--host 127.0.0.1`` and so ignored the
flag entirely, while ``corvin serve``, ``corvin-service`` and the installer
honoured it (found 2026-09-24).

Fail-safe: any failure to resolve the flag (import error, corrupt overlay,
no tenant yet on first boot) yields the loopback-only default, never an
accidentally wide-open bind.
"""
from __future__ import annotations

LOOPBACK = "127.0.0.1"
ALL_INTERFACES = "0.0.0.0"


def resolve_bind_host() -> str:
    try:
        from corvin_core import feature_flags as _ff  # noqa: PLC0415
        if _ff.is_enabled("a2a_lan_bind"):
            return ALL_INTERFACES
    except Exception:  # noqa: BLE001 — degrade to loopback, never to wide-open
        pass
    return LOOPBACK


if __name__ == "__main__":
    print(resolve_bind_host())
