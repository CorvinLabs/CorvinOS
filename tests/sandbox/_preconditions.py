"""Preconditions of the sandbox suite. Imported before anything heavy, so a CI runner that
lacks one of them SKIPS the suite with the exact reason instead of erroring at import.

Skip conditions (first match wins):
1. ``sys.platform`` is not Linux — the harness reads ``/proc`` (listener ownership, server env,
   orphan check) and arms ``PR_SET_PDEATHSIG``.
2. ``/proc/net/tcp`` is unreadable (no procfs, e.g. a restricted container).
3. ``uvicorn``, ``fastapi`` or ``httpx`` is not importable.
4. ``corvin_console`` or ``corvin_gateway`` is not importable (package not installed / not on path).
5. No loopback TCP port can be bound on 127.0.0.1.
A missing ``web-next/dist`` is NOT a skip: ``npm`` is shimmed and the console serves without the SPA.
"""
from __future__ import annotations

import importlib.util
import socket
import sys
from pathlib import Path

import pytest


def skip_reason() -> str | None:
    if not sys.platform.startswith("linux"):
        return f"sandbox suite is Linux-only (/proc, PR_SET_PDEATHSIG); platform={sys.platform}"
    try:
        Path("/proc/net/tcp").read_text()
    except OSError as exc:
        return f"sandbox suite needs /proc/net/tcp ({type(exc).__name__})"
    for mod in ("uvicorn", "fastapi", "httpx", "corvin_console", "corvin_gateway"):
        try:
            if importlib.util.find_spec(mod) is None:
                return f"sandbox suite needs {mod} importable"
        except (ImportError, ValueError):
            return f"sandbox suite needs {mod} importable"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
    except OSError as exc:
        return f"sandbox suite cannot bind a loopback port ({exc.errno})"
    return None


SKIP_REASON = skip_reason()


def require() -> None:
    """Module-level skip for a test module of this suite."""
    if SKIP_REASON is not None:
        pytest.skip(SKIP_REASON, allow_module_level=True)
