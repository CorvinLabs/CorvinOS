"""Unit tests for ADR-0166 Session Participation Gate (spg.py)."""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

# Add shared dir to sys.path so we can import spg directly
import importlib.util, os
_SHARED = Path(__file__).parent.parent / "shared"
sys.path.insert(0, str(_SHARED))
import spg  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def session_dir(tmp_path):
    return tmp_path / "session"


# ── Default state ─────────────────────────────────────────────────────────────

def test_default_state_is_private(session_dir):
    ok, reason = spg.is_sender_allowed(session_dir, "user1")
    assert not ok
    assert "private" in reason


def test_default_load_returns_private(session_dir):
    state = spg._load(session_dir)
    assert state["mode"] == "private"
    assert state["invitations"] == {}


# ── Mode: open ────────────────────────────────────────────────────────────────

def test_open_mode_allows_everyone(session_dir):
    spg.set_mode(session_dir, "open")
    ok, reason = spg.is_sender_allowed(session_dir, "any_user")
    assert ok
    assert reason == "open"


def test_set_mode_rejects_invalid(session_dir):
    with pytest.raises(ValueError):
        spg.set_mode(session_dir, "superuser")


# ── Mode: invited ─────────────────────────────────────────────────────────────

def test_invited_mode_allows_guest(session_dir):
    spg.set_mode(session_dir, "invited")
    spg.add_guest(session_dir, "alice", None, "owner")
    ok, reason = spg.is_sender_allowed(session_dir, "alice")
    assert ok
    assert reason == "invited"


def test_invited_mode_blocks_non_guest(session_dir):
    spg.set_mode(session_dir, "invited")
    spg.add_guest(session_dir, "alice", None, "owner")
    ok, reason = spg.is_sender_allowed(session_dir, "bob")
    assert not ok


def test_invite_auto_sets_invited_mode_from_private(session_dir):
    # mode starts as private
    spg.add_guest(session_dir, "alice", None, "owner")
    state = spg._load(session_dir)
    assert state["mode"] == "invited"


def test_remove_guest_reverts_to_private_when_empty(session_dir):
    spg.add_guest(session_dir, "alice", None, "owner")
    spg.remove_guest(session_dir, "alice")
    state = spg._load(session_dir)
    assert state["mode"] == "private"
    assert state["invitations"] == {}


def test_remove_guest_returns_true_if_existed(session_dir):
    spg.add_guest(session_dir, "alice", None, "owner")
    assert spg.remove_guest(session_dir, "alice") is True


def test_remove_guest_returns_false_if_not_present(session_dir):
    assert spg.remove_guest(session_dir, "nobody") is False


# ── TTL ───────────────────────────────────────────────────────────────────────

def test_invitation_with_ttl_expires(session_dir):
    spg.add_guest(session_dir, "alice", 0.01, "owner")  # 10ms
    time.sleep(0.02)
    # Reload — expired entries pruned
    ok, reason = spg.is_sender_allowed(session_dir, "alice")
    assert not ok
    assert "expired" in reason


def test_invitation_with_none_ttl_never_expires(session_dir):
    spg.add_guest(session_dir, "alice", None, "owner")
    ok, reason = spg.is_sender_allowed(session_dir, "alice")
    assert ok


def test_parse_ttl_minutes(session_dir):
    assert spg.parse_ttl("5m") == pytest.approx(300.0)


def test_parse_ttl_hours(session_dir):
    assert spg.parse_ttl("2h") == pytest.approx(7200.0)


def test_parse_ttl_days(session_dir):
    assert spg.parse_ttl("7d") == pytest.approx(7 * 86400.0)


def test_parse_ttl_none_keywords(session_dir):
    for kw in ("session", "forever", "null", "none", ""):
        assert spg.parse_ttl(kw) is None


def test_parse_ttl_invalid_raises(session_dir):
    with pytest.raises(ValueError):
        spg.parse_ttl("banana")


# ── State persistence ─────────────────────────────────────────────────────────

def test_state_file_written_with_0600_mode(session_dir):
    spg.set_mode(session_dir, "open")
    p = spg._state_path(session_dir)
    assert p.exists()
    mode = oct(p.stat().st_mode & 0o777)
    assert mode == "0o600"


def test_state_survives_reload(session_dir):
    spg.add_guest(session_dir, "alice", 3600.0, "bob")
    spg.set_mode(session_dir, "invited")
    state = spg._load(session_dir)
    assert state["mode"] == "invited"
    assert "alice" in state["invitations"]


# ── list_guests ───────────────────────────────────────────────────────────────

def test_list_guests_empty(session_dir):
    result = spg.list_guests(session_dir)
    assert result["mode"] == "private"
    assert result["guest_count"] == 0
    assert result["guests"] == []


def test_list_guests_shows_remaining_time(session_dir):
    spg.add_guest(session_dir, "carol", 600.0, "owner")
    result = spg.list_guests(session_dir)
    assert result["guest_count"] == 1
    g = result["guests"][0]
    assert g["uid"] == "carol"
    assert g["remaining_s"] is not None and g["remaining_s"] > 0


# ── GDPR: uid_hash ────────────────────────────────────────────────────────────

def test_uid_hash_is_8_chars_hex(session_dir):
    h = spg._uid_hash("user@example.com")
    assert len(h) == 8
    assert all(c in "0123456789abcdef" for c in h)


def test_uid_hash_different_for_different_uids(session_dir):
    assert spg._uid_hash("alice") != spg._uid_hash("bob")


# ── CLI (subprocess) ──────────────────────────────────────────────────────────

SPG_PY = str(_SHARED / "spg.py")


def _cli(tmp_dir, *args):
    """Run spg.py CLI with a fake session dir resolved via env.

    CLI signature: spg.py <cmd> <channel> <chat_key> [extra...]
    args = (cmd, extra...) — channel and chat_key are fixed to discord/testchat.
    """
    env = {
        **os.environ,
        "CORVIN_HOME": str(tmp_dir),
        "CORVIN_TENANT_ID": "_default",
    }
    # We need the voice path to exist for resolver to pick it up
    session_path = tmp_dir / "tenants" / "_default" / "sessions" / "voice" / "discord" / "testchat"
    session_path.mkdir(parents=True, exist_ok=True)
    cmd = args[0]
    extra = list(args[1:])
    r = subprocess.run(
        [sys.executable, SPG_PY, cmd, "discord", "testchat", *extra],
        capture_output=True, text=True, env=env, timeout=10,
    )
    return r, session_path


def test_cli_set_mode_open(tmp_path):
    r, session_path = _cli(tmp_path, "set-mode", "open")
    assert r.returncode == 0
    j = json.loads(r.stdout)
    assert j["ok"] is True
    assert j["mode"] == "open"
    state = spg._load(session_path)
    assert state["mode"] == "open"


def test_cli_add_guest_then_list(tmp_path):
    _cli(tmp_path, "add-guest", "alice123", "30m")
    r, _ = _cli(tmp_path, "list")
    assert r.returncode == 0
    j = json.loads(r.stdout)
    assert j["guest_count"] == 1
    assert j["guests"][0]["uid"] == "alice123"


def test_cli_rm_guest(tmp_path):
    _cli(tmp_path, "add-guest", "bob456")
    r, _ = _cli(tmp_path, "rm-guest", "bob456")
    assert r.returncode == 0
    j = json.loads(r.stdout)
    assert j["existed"] is True


def test_cli_mode_command(tmp_path):
    r, _ = _cli(tmp_path, "mode")
    assert r.returncode == 0
    j = json.loads(r.stdout)
    assert j["mode"] == "private"


def test_cli_no_command_exits_nonzero(tmp_path):
    r = subprocess.run([sys.executable, SPG_PY], capture_output=True, text=True, timeout=5)
    assert r.returncode != 0


def test_cli_invalid_mode_exits_nonzero(tmp_path):
    r, _ = _cli(tmp_path, "set-mode", "superadmin")
    assert r.returncode != 0
