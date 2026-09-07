"""F-E2: a configured→effective OS-engine substitution is audited.

``chat_runtime._effective_os_engine`` swaps a Claude-configured tenant onto
Hermes when the claude binary is missing or unauthenticated. That swap used
to be silent; it now writes ``os_turn.engine_substituted {configured,
effective, reason}`` on the tenant's console audit chain
(``corvin_console.audit.system_event`` → forge ``security_events`` chain).
The pre-turn WebSocket guard (``get_engine_unavailable_message``) resolves
the same answer WITHOUT emitting, so a turn produces exactly one record.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_CONSOLE_PKG = Path(__file__).resolve().parents[1]
if str(_CONSOLE_PKG) not in sys.path:
    sys.path.insert(0, str(_CONSOLE_PKG))

from corvin_console import chat_runtime as cr  # noqa: E402


@pytest.fixture
def corvin_home(tmp_path, monkeypatch):
    home = tmp_path / "corvin_home"
    (home / "tenants" / "_default" / "global").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    return home


def _chain(home: Path) -> list[dict]:
    path = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def _events(home: Path, name: str) -> list[dict]:
    return [r for r in _chain(home) if r.get("event") == name or r.get("event_type") == name]


def test_missing_claude_binary_substitution_is_audited(corvin_home, monkeypatch):
    monkeypatch.setattr(cr, "_configured_os_engine", lambda tenant_id: "claude_code")
    monkeypatch.setattr(cr, "_claude_binary", lambda: "/nonexistent/claude-bin")

    assert cr._effective_os_engine("_default") == "hermes"

    recs = _events(corvin_home, "os_turn.engine_substituted")
    assert len(recs) == 1, _chain(corvin_home)
    details = recs[0].get("details") or {}
    assert details["configured"] == "claude_code"
    assert details["effective"] == "hermes"
    assert details["reason"] == "claude-binary-missing"
    # chained record, not a side-channel line
    assert recs[0].get("hash") or recs[0].get("h")


def test_unauthenticated_claude_substitution_is_audited(corvin_home, monkeypatch, tmp_path):
    fake_bin = tmp_path / "claude"
    fake_bin.write_text("#!/bin/sh\nexit 0\n")
    fake_bin.chmod(0o755)
    monkeypatch.setattr(cr, "_configured_os_engine", lambda tenant_id: "claude_code")
    monkeypatch.setattr(cr, "_claude_binary", lambda: str(fake_bin))
    monkeypatch.setattr(cr, "_claude_authenticated", lambda: False)

    assert cr._effective_os_engine("_default") == "hermes"
    recs = _events(corvin_home, "os_turn.engine_substituted")
    assert len(recs) == 1
    assert recs[0]["details"]["reason"] == "claude-not-authenticated"


def test_no_substitution_no_event(corvin_home, monkeypatch, tmp_path):
    fake_bin = tmp_path / "claude"
    fake_bin.write_text("#!/bin/sh\nexit 0\n")
    fake_bin.chmod(0o755)
    monkeypatch.setattr(cr, "_configured_os_engine", lambda tenant_id: "claude_code")
    monkeypatch.setattr(cr, "_claude_binary", lambda: str(fake_bin))
    monkeypatch.setattr(cr, "_claude_authenticated", lambda: True)

    assert cr._effective_os_engine("_default") == "claude_code"
    assert _events(corvin_home, "os_turn.engine_substituted") == []

    monkeypatch.setattr(cr, "_configured_os_engine", lambda tenant_id: "hermes")
    assert cr._effective_os_engine("_default") == "hermes"
    assert _events(corvin_home, "os_turn.engine_substituted") == []


def test_pre_turn_guard_does_not_double_emit(corvin_home, monkeypatch):
    monkeypatch.setattr(cr, "_configured_os_engine", lambda tenant_id: "claude_code")
    monkeypatch.setattr(cr, "_claude_binary", lambda: "/nonexistent/claude-bin")

    # WebSocket guard path: resolves, does not audit
    cr.get_engine_unavailable_message("_default")
    assert _events(corvin_home, "os_turn.engine_substituted") == []

    # the turn itself audits exactly once
    cr._effective_os_engine("_default")
    assert len(_events(corvin_home, "os_turn.engine_substituted")) == 1
