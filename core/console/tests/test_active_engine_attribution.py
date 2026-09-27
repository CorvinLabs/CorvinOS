"""Tests for cross-process OS-engine attribution in the anonymous ping.

Root cause fixed here: the activity ping (ADR-0180) fires from the
out-of-process corvin-serve console, whose environment never inherits the
engine ladder the bridge adapter resolves. So ``_detect_active_engine`` fell
through to "unknown" for nearly every install (live server showed
engine_distribution = {"unknown": 17, "claude_code": 1}).

Fix: the bridge writes the resolved engine to an ``active_engine`` state file
via ``record_active_engine``; ``_detect_active_engine`` reads it. This test
suite pins that contract plus the tenant-YAML key fix (``default_engine`` was
previously missed by the ``worker_engine`` regex).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from corvin_console.aco import htrace_uploader as hu


def _make_home(tmp_path: Path) -> Path:
    home = tmp_path / ".corvin"
    (home / "aco" / "telemetry").mkdir(parents=True, exist_ok=True)
    return home


@pytest.fixture(autouse=True)
def _clear_engine_env(monkeypatch):
    """The env vars win over the state file — clear them so the file path is
    exercised (they are only set inside the bridge process, not the ping's)."""
    monkeypatch.delenv("CORVIN_WORKER_ENGINE", raising=False)
    monkeypatch.delenv("CORVIN_OS_ENGINE", raising=False)


# ── record_active_engine ────────────────────────────────────────────────────

def test_record_then_detect_roundtrip(tmp_path):
    home = _make_home(tmp_path)
    hu.record_active_engine(home, "claude_code")
    assert hu._active_engine_path(home).read_text(encoding="utf-8").strip() == "claude_code"
    assert hu._detect_active_engine(home) == "claude_code"


def test_record_rejects_unknown_engine(tmp_path):
    home = _make_home(tmp_path)
    hu.record_active_engine(home, "totally-made-up")
    # Nothing persisted → detection stays "unknown", never a spoofed value.
    assert not hu._active_engine_path(home).exists()
    assert hu._detect_active_engine(home) == "unknown"


def test_record_normalises_case_and_whitespace(tmp_path):
    home = _make_home(tmp_path)
    hu.record_active_engine(home, "  OpenCode  ")
    assert hu._detect_active_engine(home) == "opencode"


def test_record_is_idempotent_no_rewrite_when_unchanged(tmp_path):
    home = _make_home(tmp_path)
    hu.record_active_engine(home, "codex_cli")
    p = hu._active_engine_path(home)
    mtime_first = p.stat().st_mtime_ns
    hu.record_active_engine(home, "codex_cli")  # same value → must not rewrite
    assert p.stat().st_mtime_ns == mtime_first


def test_record_updates_on_engine_change(tmp_path):
    home = _make_home(tmp_path)
    hu.record_active_engine(home, "claude_code")
    hu.record_active_engine(home, "copilot")
    assert hu._detect_active_engine(home) == "copilot"


# ── resolution precedence ────────────────────────────────────────────────────

def test_env_var_wins_over_state_file(tmp_path, monkeypatch):
    home = _make_home(tmp_path)
    hu.record_active_engine(home, "opencode")
    monkeypatch.setenv("CORVIN_OS_ENGINE", "claude_code")
    assert hu._detect_active_engine(home) == "claude_code"


def test_state_file_wins_over_yaml(tmp_path):
    home = _make_home(tmp_path)
    hu.record_active_engine(home, "opencode")
    cfg = hu._tenant_cfg_path(home)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("spec:\n  default_engine: codex_cli\n", encoding="utf-8")
    assert hu._detect_active_engine(home) == "opencode"


# ── tenant YAML fallback key fix ─────────────────────────────────────────────

@pytest.mark.parametrize(
    "line",
    [
        "  default_engine: codex_cli\n",
        '  default_engine: "codex_cli"\n',
        "  default_worker_engine: codex_cli\n",
        "  worker_engine: codex_cli\n",
    ],
)
def test_yaml_fallback_matches_all_engine_keys(tmp_path, line):
    home = _make_home(tmp_path)
    cfg = hu._tenant_cfg_path(home)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(f"spec:\n{line}", encoding="utf-8")
    assert hu._detect_active_engine(home) == "codex_cli"


def test_no_signal_anywhere_is_unknown(tmp_path):
    home = _make_home(tmp_path)
    assert hu._detect_active_engine(home) == "unknown"


# ── ADR-2091: a stale stored ``hermes`` degrades to "unknown" ────────────────

def test_hermes_is_not_recorded_any_more(tmp_path):
    home = _make_home(tmp_path)
    hu.record_active_engine(home, "hermes")
    assert not hu._active_engine_path(home).exists()


def test_stale_hermes_state_and_yaml_degrade_to_unknown_and_ping_stays_safe(tmp_path):
    """An old install still carrying ``hermes`` in the state file AND the
    tenant YAML must keep pinging: the value falls out of the closed enum as
    "unknown" BEFORE the fail-closed ``_assert_ping_safe`` backstop."""
    home = _make_home(tmp_path)
    p = hu._active_engine_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("hermes", encoding="utf-8")
    cfg = hu._tenant_cfg_path(home)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("spec:\n  default_engine: hermes\n", encoding="utf-8")

    engine = hu._detect_active_engine(home)
    assert engine == "unknown"
    body = {"corvin_version": "1.2.3", "platform": "linux",
            "python_minor": "3.11", "active_engine": engine}
    hu._assert_ping_safe(body)  # must not raise
    with pytest.raises(ValueError):
        hu._assert_ping_safe({**body, "active_engine": "hermes"})
