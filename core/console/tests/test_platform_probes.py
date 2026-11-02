"""PLAN-0969 WP-2: platform probes, the nerve fiber that carries them, and the report route.

Three layers, because a probe nobody calls is a dead mechanism:

* each probe is green on a healthy store and RED against a deliberately broken one;
* the Windows-like condition (no ``os.O_NOFOLLOW``, ``:`` illegal) leaves the probes green,
  because the writers were fixed, and goes red if a writer regresses;
* the fiber is reachable through ``NerveRegistry.scan_all()`` (the call the boot healer makes)
  and the route is mounted (see ``tests/sandbox/test_platform_probe_route.py`` for HTTP).
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "core" / "console", _REPO / "corvin_operator" / "forge", _REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from corvin_core.aco import platform_probes as pp  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def scratch(home):
    return pp.scratch_root(home)


# ── healthy ──────────────────────────────────────────────────────────────────

def test_all_probes_pass_on_a_healthy_host(home):
    results = pp.run_all(home)
    assert [r.name for r in results] == list(pp.PROBE_NAMES)
    assert all(r.ok for r in results), [r.to_dict() for r in results if not r.ok]


def test_scratch_and_probe_session_are_removed(home):
    pp.run_all(home)
    assert not (home / ".platform_probe").exists()
    sessions = list(home.rglob("platform-probe*"))
    assert sessions == [], sessions


# ── red against a deliberately broken store ──────────────────────────────────

def test_session_memory_probe_red_when_the_writer_fails(home):
    def boom(*a, **k):
        raise OSError(123, "invalid name")  # WinError 123 shape

    r = pp.probe_session_memory(write_files=boom)
    assert not r.ok and r.error_type == "OSError" and r.stage == "write"


def test_recall_probe_red_without_fts5(scratch):
    class NoFts(sqlite3.Connection):
        def execute(self, sql, *a):
            if "fts5" in sql.lower():
                raise sqlite3.OperationalError("no such module: fts5")
            return super().execute(sql, *a)

    r = pp.probe_recall_store(scratch, connect=lambda *a, **k: sqlite3.connect(*a, factory=NoFts, **k))
    assert not r.ok and r.error_type == "OperationalError" and r.stage == "fts5"


def test_run_log_probe_red_when_open_fails(scratch):
    def deny(*a, **k):
        raise PermissionError(13, "denied")

    r = pp.probe_run_log(scratch, os_open=deny)
    assert not r.ok and r.error_type == "PermissionError" and r.stage == "open"


def test_voice_summary_probe_red_when_replace_is_blocked(scratch):
    def locked(src, dst):
        raise PermissionError(13, "file in use")  # antivirus / player holding the file

    r = pp.probe_voice_summary_store(scratch, replace=locked)
    assert not r.ok and r.error_type == "PermissionError" and r.stage == "replace"


def test_a_failure_never_carries_a_message_or_path(scratch):
    def deny(*a, **k):
        raise PermissionError(13, "C:\\Users\\someone\\secret.jsonl")

    blob = repr(pp.probe_run_log(scratch, os_open=deny).to_dict())
    assert "someone" not in blob and "secret" not in blob and "\\" not in blob


# ── Windows-like condition ───────────────────────────────────────────────────

def test_probes_stay_green_without_o_nofollow(home, monkeypatch):
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    assert not hasattr(os, "O_NOFOLLOW")
    results = pp.run_all(home)
    assert all(r.ok for r in results), [r.to_dict() for r in results if not r.ok]


def test_run_log_probe_goes_red_if_a_writer_regresses_to_a_bare_flag(scratch, monkeypatch):
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)

    def bare(path, flags, mode=0o777):  # what the old code did on Windows
        return os.open(path, flags | os.O_NOFOLLOW, mode)  # AttributeError

    r = pp.probe_run_log(scratch, os_open=bare)
    assert not r.ok and r.error_type == "AttributeError"


# ── reachability: the fiber, through the registry the healer calls ───────────

def test_fiber_is_registered_and_reached_through_scan_all(home, monkeypatch):
    from corvin_core.aco.nerve import NerveRegistry

    NerveRegistry._fibers.clear()
    NerveRegistry._discovered = False
    seen: list[str] = []
    real = pp.run_all
    monkeypatch.setattr(pp, "run_all", lambda *a, **k: (seen.append("run_all"), real(*a, **k))[1])
    try:
        NerveRegistry.scan_all()
        assert "platform.probe" in {f["fiber_id"] for f in NerveRegistry.list_fibers()}
        assert seen, "scan_all() never reached the platform probes"
    finally:
        NerveRegistry._fibers.clear()
        NerveRegistry._discovered = False


def test_failed_probe_becomes_a_high_signal_without_content(home, monkeypatch):
    from corvin_core.aco import nerve_builtins as nb

    bad = pp.ProbeResult(name="run_log", ok=False, error_type="AttributeError", stage="open")
    monkeypatch.setattr(pp, "run_all", lambda *a, **k: [bad])
    signals = nb.PlatformProbeFiber().scan()
    assert len(signals) == 1
    s = signals[0]
    assert s.severity == "HIGH" and s.fiber_id == "platform.probe"
    assert s.signal_type == "platform.run_log_failed"
    assert s.data == {"probe": "run_log", "stage": "open", "error_type": "AttributeError",
                      "platform": sys.platform}


def test_healthy_host_yields_no_signal(home, monkeypatch):
    from corvin_core.aco import nerve_builtins as nb

    assert nb.PlatformProbeFiber().scan() == []


def test_report_route_is_mounted():
    from corvin_console.routes import aco as aco_mod

    assert "/aco/platform/probe" in [r.path for r in aco_mod.router.routes]
