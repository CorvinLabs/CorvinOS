"""Console slash-command dispatcher — every command is handled deterministically
and NEVER leaks to the LLM (the command-center contract).
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))

from corvin_console import slash_commands as SC  # type: ignore

_KW = dict(tier="member", tenant_id="_default", fingerprint="abc123def456",
           configured_engine="claude_code")


def _h(text):
    return SC.handle(text, **_KW)


# ── pass-through (None) ───────────────────────────────────────────────
def test_plain_text_passes_through():
    assert _h("hello, summarize this") is None
    assert _h("") is None


def test_ccc_commands_pass_through():
    for t in ("/create workflow name=x", "/create task", "/erase user uid=1", "/audit last 5"):
        assert _h(t) is None, f"{t!r} must fall through to entity_extract"
    assert SC.is_ccc("/create tool") is True
    assert SC.is_ccc("/whoami") is False


def test_delegate_passes_through_to_stream_turn():
    # /delegate is the force-delegation verb (ADR-0114). It MUST pass through
    # (None) so stream_turn's _force_delegate branch runs — it used to be
    # rejected as "Unknown command", making the command-center's flagship
    # delegation verb dead.
    assert _h("/delegate refactor the auth module") is None
    assert _h("/delegate") is None


# ── functional commands (real data) ──────────────────────────────────
def test_help_lists_commands():
    out = _h("/help")
    assert out and "/whoami" in out and "/engine" in out


def test_whoami_and_role_show_identity():
    for t in ("/whoami", "/role"):
        out = _h(t)
        assert "_default" in out and "abc123def456" in out and "member" in out


def test_quota_unlimited_when_no_limit():
    # In this repo chat is unlimited (limit resolves to None) → "unlimited".
    out = _h("/quota")
    assert "unlimited" in out.lower() or "limit" in out.lower()


def test_engine_shows_configured_engine():
    assert "claude_code" in _h("/engine")
    # with an arg it explains tenant-wide config, still names the engine
    out = _h("/engine hermes")
    assert "claude_code" in out and "Engines" in out


# ── informational pointers (no LLM leak) ─────────────────────────────
def test_pointer_commands_return_guidance():
    assert "Personas" in _h("/persona")
    assert "Skills" in _h("/skills")
    assert "Memory" in _h("/memory")
    assert "Engines" in _h("/dialectic-on") and "Engines" in _h("/dialectic-off")
    assert "erase" in _h("/forget").lower()


def test_bridge_only_commands_are_explained():
    for t in ("/go", "/propose do x", "/btw note", "/share"):
        out = _h(t)
        assert "bridge" in out.lower() and "console" in out.lower()


def test_client_side_commands_point_to_buttons():
    assert "Stop" in _h("/stop") and "Stop" in _h("/cancel") and "Stop" in _h("/halt")
    assert "New chat" in _h("/new") and "New chat" in _h("/clear") and "New chat" in _h("/reset")


def test_unknown_command_is_caught_not_leaked():
    out = _h("/frobnicate xyz")
    assert out is not None and "Unknown command" in out and "/help" in out


def test_case_insensitive():
    assert _h("/WhoAmI") is not None and "_default" in _h("/WHOAMI")


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
