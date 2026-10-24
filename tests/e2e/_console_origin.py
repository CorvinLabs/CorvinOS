"""Console origin policy for the Python Playwright tests (W-0).

Mirror of core/console/corvin_console/web-next/tests/e2e/sandbox-origin.ts: the default origin is
the isolated sandbox (127.0.0.1:8799); a URL on the live console port 8765 is refused unless the run
sets CORVIN_E2E_ALLOW_LIVE=1 explicitly. Stdlib only, so it loads in a pytest-only environment.
"""
from __future__ import annotations

import os
from typing import Mapping
from urllib.parse import urlsplit

SANDBOX_CONSOLE_ORIGIN = "http://127.0.0.1:8799"
LIVE_CONSOLE_PORT = 8765
LIVE_OPT_IN_ENV = "CORVIN_E2E_ALLOW_LIVE"


class LiveConsoleRefused(RuntimeError):
    """A test run targeted the live console without the explicit opt-in."""


def is_live_console_url(raw: str) -> bool:
    """True when the URL points at the live console port (any host: fail closed)."""
    try:
        parts = urlsplit(raw)
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return False
    return bool(parts.scheme) and port == LIVE_CONSOLE_PORT


def assert_not_live_console(raw: str, what: str = "console origin", env: Mapping[str, str] | None = None) -> None:
    env = os.environ if env is None else env
    if not is_live_console_url(raw) or env.get(LIVE_OPT_IN_ENV) == "1":
        return
    raise LiveConsoleRefused(
        f"refusing to run: {what} = {raw} targets the live console (port {LIVE_CONSOLE_PORT}). "
        f"Tests default to the sandbox ({SANDBOX_CONSOLE_ORIGIN}). "
        f"To run against the live console on purpose, set {LIVE_OPT_IN_ENV}=1 explicitly."
    )


def resolve_url_var(var: str, fallback: str = SANDBOX_CONSOLE_ORIGIN, env: Mapping[str, str] | None = None) -> str:
    """env[var] or the fallback; refuses :8765 without the opt-in."""
    env = os.environ if env is None else env
    raw = env.get(var) or fallback
    assert_not_live_console(raw, var, env)
    return raw


def console_origin(env: Mapping[str, str] | None = None) -> str:
    """Origin for the Python Playwright tests (BASE_URL, default sandbox), without a trailing slash."""
    return resolve_url_var("BASE_URL", SANDBOX_CONSOLE_ORIGIN, env).rstrip("/")
