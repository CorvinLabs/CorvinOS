"""W-0: the Python Playwright tests never target the live console (:8765) by default.

Run (needs only pytest): python -m pytest tests/e2e/test_e2e_console_origin.py --noconftest -q
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.e2e._console_origin import (
    SANDBOX_CONSOLE_ORIGIN,
    LiveConsoleRefused,
    console_origin,
    is_live_console_url,
    resolve_url_var,
)

REPO = Path(__file__).resolve().parents[2]
LIVE = "http://localhost:8765"


def test_default_is_the_sandbox():
    assert console_origin({}) == SANDBOX_CONSOLE_ORIGIN
    assert not is_live_console_url(SANDBOX_CONSOLE_ORIGIN)


def test_live_port_is_detected_on_any_host():
    assert is_live_console_url("http://127.0.0.1:8765/console")
    assert is_live_console_url(LIVE)
    assert not is_live_console_url("http://127.0.0.1:8799")
    assert not is_live_console_url("not a url")


def test_live_base_url_is_refused_without_opt_in():
    with pytest.raises(LiveConsoleRefused, match="CORVIN_E2E_ALLOW_LIVE"):
        console_origin({"BASE_URL": LIVE})
    with pytest.raises(LiveConsoleRefused):
        resolve_url_var("BASE_URL", SANDBOX_CONSOLE_ORIGIN, {"BASE_URL": LIVE, "CORVIN_E2E_ALLOW_LIVE": "0"})


def test_live_base_url_is_allowed_with_the_explicit_opt_in():
    assert console_origin({"BASE_URL": LIVE, "CORVIN_E2E_ALLOW_LIVE": "1"}) == LIVE


# --- static scan: Python Playwright usage never names the live console -------------------------

PLAYWRIGHT_FILE = re.compile(r"page\.goto|from playwright|import playwright|pytest_playwright|async_playwright|sync_playwright")
USAGE = re.compile(r"page\.goto|\bgoto\(|base_url|BASE_URL")


def scan_python(text: str) -> list[str]:
    """Executable lines of a Playwright test file that name :8765 in a page.goto / base_url / BASE_URL."""
    if not PLAYWRIGHT_FILE.search(text):
        return []
    return [f"{i}: {line.strip()[:120]}" for i, line in enumerate(text.split("\n"), 1)
            if "8765" in line and not line.strip().startswith("#") and USAGE.search(line)]


def _python_test_files() -> list[Path]:
    out = []
    for p in sorted((REPO / "tests").rglob("*.py")):
        if "__pycache__" in p.parts or p.name == "_console_origin.py" or p.resolve() == Path(__file__).resolve():
            continue
        out.append(p)
    return out


def test_python_scan_positive_control():
    bad = 'from playwright.async_api import Page\n\nasync def t(page):\n    await page.goto("http://localhost:8765/console/app/tasks")\n'
    assert len(scan_python(bad)) == 1
    assert scan_python('from playwright.sync_api import Page\n# page.goto("http://localhost:8765") in a comment\n') == []
    # not a Playwright file (httpx API test): out of this scan's scope
    assert scan_python('client = AsyncClient(base_url="http://localhost:8765")\n') == []
    reached = {p.relative_to(REPO).as_posix() for p in _python_test_files()}
    for must in ("tests/e2e/test_form_api_contract.py", "tests/e2e/test_full_task_orchestration_e2e.py",
                 "tests/e2e/test_output_rendering.py", "tests/console/conftest.py"):
        assert must in reached, must


def test_no_python_playwright_usage_names_the_live_console():
    violations = []
    for p in _python_test_files():
        for v in scan_python(p.read_text(encoding="utf-8", errors="replace")):
            violations.append(f"{p.relative_to(REPO).as_posix()}:{v}")
    assert violations == [], "\n".join(violations)


def _collect(extra_env: dict[str, str]) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("BASE_URL", "CORVIN_E2E_ALLOW_LIVE")}
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "--noconftest", "-p", "no:cacheprovider",
         "tests/e2e/test_output_rendering.py"],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=120,
    )


def test_a_python_playwright_module_refuses_live_without_opt_in():
    r = _collect({"BASE_URL": LIVE})
    assert r.returncode != 0, r.stdout + r.stderr
    assert "CORVIN_E2E_ALLOW_LIVE" in r.stdout + r.stderr


def test_a_python_playwright_module_collects_with_the_opt_in():
    r = _collect({"BASE_URL": LIVE, "CORVIN_E2E_ALLOW_LIVE": "1"})
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_python_playwright_module_collects_against_the_sandbox_by_default():
    r = _collect({})
    assert r.returncode == 0, r.stdout + r.stderr
