"""Installer readiness-probe drift guard.

Both installers poll a health endpoint after starting the console server and
only auto-open the browser once it answers:

- `install.sh`  (POSIX path, `curl`)
- `install.ps1` (Windows path, `Invoke-WebRequest`)

The probe URL must point at a route the standalone app ACTUALLY mounts. It
regressed once: both installers polled ``/api/health`` — a path the app never
serves (`corvin_console/standalone.py` only mounts ``/v1/console/*``, ``/console/*``,
``/local-stats`` and ``/``). On POSIX this passed by accident (``curl -s`` without
``-f`` treats the resulting 404 as success), but on Windows PowerShell 5.1
``Invoke-WebRequest`` raises on any non-2xx status, so the readiness loop always
ran into its 30× timeout and the ``Start-Process`` browser-open branch was never
reached — the console never opened after a fresh Windows install.

This test pins the probe URL in BOTH installers to the same real, mounted,
unauthenticated route (``/v1/console/healthz``, defined in `corvin_console/app.py`
and included at prefix ``/v1/console`` by `corvin_console/standalone.py`), so a
future rename on either side can't silently reintroduce the drift.
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_INSTALL_SH = _REPO / "install.sh"
_INSTALL_PS1 = _REPO / "install.ps1"
_APP_PY = _REPO / "core" / "console" / "corvin_console" / "app.py"
_STANDALONE_PY = _REPO / "core" / "console" / "corvin_console" / "standalone.py"

# The single source of truth this test enforces across all four files.
_HEALTH_PREFIX = "/v1/console"
_HEALTH_ROUTE = "/healthz"
_HEALTH_PATH = _HEALTH_PREFIX + _HEALTH_ROUTE

# Both spellings the installers use: install.sh's literal localhost/127.0.0.1
# and install.ps1's "http://127.0.0.1:$Port/..." / "$TargetPort" (since
# e3092bedc the URL there is always 127.0.0.1 with a configurable port).
_URL_RE = re.compile(r"http://(?:localhost|127\.0\.0\.1):(?:8765|\$\{?[A-Za-z]*Port\}?)(/[^\"'\s]*)")
_SHELL_PATH = "/console/"


def _probe_paths(text: str) -> list[str]:
    """Every console-URL path referenced in an installer file."""
    return [p.rstrip("')") for p in _URL_RE.findall(text)]


def test_posix_installer_polls_the_real_health_route() -> None:
    paths = _probe_paths(_INSTALL_SH.read_text(encoding="utf-8"))
    assert paths, "install.sh no longer references the console server URL"
    for p in paths:
        # Console-URL to open (/console/) is fine; every /api-style probe must
        # be the real health route.
        assert not p.startswith("/api/"), f"install.sh polls a non-existent endpoint: {p}"
    assert _HEALTH_PATH in paths, (
        f"install.sh must poll {_HEALTH_PATH!r} for readiness; found {paths!r}"
    )


def test_windows_installer_polls_the_real_health_route() -> None:
    """install.ps1 waits for the SPA shell (/console/ → 200), which is the
    stronger readiness proof: healthz stays green while a console that booted
    without dist/ serves its 503 page. No /api-style probe may exist."""
    paths = _probe_paths(_INSTALL_PS1.read_text(encoding="utf-8"))
    assert paths, "install.ps1 no longer references the console server URL"
    for p in paths:
        assert not p.startswith("/api/"), f"install.ps1 polls a non-existent endpoint: {p}"
    assert _SHELL_PATH in paths, (
        f"install.ps1 must wait for the app shell {_SHELL_PATH!r}; found {paths!r}"
    )


def test_both_installers_agree_on_the_probe() -> None:
    """Both wait for the SAME thing — the app shell answering 200 (install.sh
    additionally confirms /v1/console/healthz and then runs
    scripts/verify_install.py, which covers local login as well)."""
    sh = set(_probe_paths(_INSTALL_SH.read_text(encoding="utf-8")))
    ps1 = set(_probe_paths(_INSTALL_PS1.read_text(encoding="utf-8")))
    assert _SHELL_PATH in (sh & ps1), (
        "install.sh and install.ps1 must wait for the SAME readiness signal; "
        f"sh={sorted(sh)!r} ps1={sorted(ps1)!r}"
    )


def test_health_route_is_actually_mounted() -> None:
    # The route the installers poll must exist where they expect it: healthz
    # is declared on the router in app.py and included at /v1/console in
    # standalone.py. If either end is renamed, the installer probe is stale.
    app_src = _APP_PY.read_text(encoding="utf-8")
    standalone_src = _STANDALONE_PY.read_text(encoding="utf-8")
    assert re.search(
        r"@router\.get\(\s*[\"']" + re.escape(_HEALTH_ROUTE) + r"[\"']",
        app_src,
    ), f"app.py no longer declares a {_HEALTH_ROUTE!r} route on the console router"
    assert re.search(
        r"include_router\(\s*router\s*,\s*prefix\s*=\s*[\"']"
        + re.escape(_HEALTH_PREFIX)
        + r"[\"']",
        standalone_src,
    ), f"standalone.py no longer mounts the console router at {_HEALTH_PREFIX!r}"
