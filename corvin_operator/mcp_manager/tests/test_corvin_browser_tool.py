"""Tests for the ADR-0193 corvin-browser MCP server (operator/mcp_manager/
servers/corvin-browser/main.py).

Adversarial-review regression coverage (round 2 found this module had ZERO
test coverage at all): the L44 acceptable-use gate must run on EVERY
browser_navigate call, not just the one that creates a new session — an
earlier version of this file gated only inside session creation, silently
skipping the check for every navigate that reused an existing session_id.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_BRIDGES_SHARED = Path(__file__).resolve().parents[3] / "bridges" / "shared"
if str(_BRIDGES_SHARED) not in sys.path:
    sys.path.insert(0, str(_BRIDGES_SHARED))

_CORVIN_BROWSER_MAIN = (Path(__file__).resolve().parents[1]
                        / "servers" / "corvin-browser" / "main.py")

# Deliberately NOT `sys.path.insert(server_dir) + import main` — the sibling
# imagegen-zero-config server also has its own main.py, and
# test_imagegen_zero_config.py does `import main as m` inside its own test
# functions. Both files run in the SAME pytest process/interpreter, so a
# plain `import main` here would cache under the shared `sys.modules["main"]`
# key and silently hand imagegen's tests THIS module instead of their own
# (confirmed live: this exact collision broke 13 unrelated imagegen tests the
# first time this file used sys.path.insert + import main). Loading via an
# explicit spec under a distinct module name avoids the shared cache key
# entirely.
_spec = importlib.util.spec_from_file_location("corvin_browser_main", _CORVIN_BROWSER_MAIN)
corvin_browser_main = importlib.util.module_from_spec(_spec)
sys.modules["corvin_browser_main"] = corvin_browser_main
_spec.loader.exec_module(corvin_browser_main)


@pytest.fixture(autouse=True)
def _reset_session_state(monkeypatch):
    """`_current_session` is module-level, per-server-process state — reset
    it around every test so tests don't leak a cached session id into the
    next one (mirrors the real server's own "fresh subprocess per turn"
    lifecycle, per the module's own docstring)."""
    monkeypatch.setattr(corvin_browser_main, "_current_session", None)
    monkeypatch.setenv("CORVIN_BROWSER_TOKEN", "test-token")
    yield


def test_l44_gate_runs_on_every_navigate_call_not_just_session_creation(monkeypatch):
    l44_calls = []

    def fake_check_l44(prompt, tid, **kw):
        l44_calls.append(prompt)
        return None  # permitted

    request_calls = []

    def fake_request(method, path, *, json_body=None, params=None):
        request_calls.append((method, path, json_body))
        if path == "/v1/console/browser/session":
            return {"session": "sid-1"}
        if path.endswith("/navigate"):
            return {"marks": [], "url": json_body.get("url", "")}
        return {}

    monkeypatch.setattr(corvin_browser_main, "check_l44", fake_check_l44)
    monkeypatch.setattr(corvin_browser_main, "_request", fake_request)

    # First call: creates the session. Second call: REUSES it via the
    # returned session_id — this is exactly the call shape that used to
    # skip the L44 gate entirely.
    r1 = corvin_browser_main.browser_navigate("https://a.example")
    sid = r1["session_id"]
    corvin_browser_main.browser_navigate("https://b.example", session_id=sid)

    assert l44_calls == ["browse to https://a.example", "browse to https://b.example"]


def test_l44_refusal_blocks_navigate_even_with_an_existing_session(monkeypatch):
    def fake_check_l44(prompt, tid, **kw):
        if "denied" in prompt:
            return "This request was refused by house rules."
        return None

    def fake_request(method, path, *, json_body=None, params=None):
        if path == "/v1/console/browser/session":
            return {"session": "sid-1"}
        return {"marks": [], "url": json_body.get("url", "") if json_body else ""}

    monkeypatch.setattr(corvin_browser_main, "check_l44", fake_check_l44)
    monkeypatch.setattr(corvin_browser_main, "_request", fake_request)

    r1 = corvin_browser_main.browser_navigate("https://a.example")
    sid = r1["session_id"]
    with pytest.raises(corvin_browser_main.BrowserToolError):
        corvin_browser_main.browser_navigate("https://denied.example", session_id=sid)


def test_ensure_session_creates_once_and_reuses_for_same_turn(monkeypatch):
    monkeypatch.setattr(corvin_browser_main, "check_l44", lambda *a, **k: None)
    session_creates = []

    def fake_request(method, path, *, json_body=None, params=None):
        if path == "/v1/console/browser/session":
            session_creates.append(json_body)
            return {"session": "sid-1"}
        return {"marks": [], "url": json_body.get("url", "") if json_body else ""}

    monkeypatch.setattr(corvin_browser_main, "_request", fake_request)

    r1 = corvin_browser_main.browser_navigate("https://a.example")
    r2 = corvin_browser_main.browser_navigate("https://b.example")  # no session_id given

    assert r1["session_id"] == r2["session_id"]
    assert len(session_creates) == 1  # only ONE session actually created
    assert session_creates[0]["task_scoped_hosts"] == ["a.example"]
