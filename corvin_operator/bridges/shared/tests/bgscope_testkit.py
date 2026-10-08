"""Shared helpers for the background-scope tests (ADR-2236, PLAN-0938).

Cross-platform on purpose: the fake CLI is a Python script, but the engine
executes ``CORVIN_CLAUDE_BIN`` directly, so a tiny launcher is generated per
platform (POSIX shell script / Windows ``.cmd``) instead of relying on a
shebang.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "bgscope"
FAKE = HERE / "fake_claude.py"

# The fixtures were captured from this CLI. A different live CLI version is
# what the slow canary test (test_bg_scope_real_cli_canary.py) is for.
CAPTURED_WITH = "2.1.294"


def load_fixture(name: str) -> list[dict]:
    with open(FIXTURES / f"{name}.jsonl", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def make_fake_claude_bin(tmp: Path) -> Path:
    """Write a launcher that runs fake_claude.py with the current interpreter."""
    tmp.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        launcher = tmp / "claude.cmd"
        launcher.write_text(f'@echo off\r\n"{sys.executable}" "{FAKE}" %*\r\n')
    else:
        launcher = tmp / "claude"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return launcher


def fake_env(tmp: Path, fixture: str, *, speedup: float = 1.0,
             silent_s: float = 0.0) -> dict[str, str]:
    """Environment that makes the engine spawn the fake CLI replaying *fixture*."""
    launcher = make_fake_claude_bin(tmp / "fake-bin")
    return {
        "CORVIN_CLAUDE_BIN": str(launcher),
        "FAKE_CLAUDE_FIXTURE": str(FIXTURES / f"{fixture}.jsonl"),
        "FAKE_CLAUDE_SPEEDUP": str(speedup),
        "FAKE_CLAUDE_SILENT_S": str(silent_s),
        "FAKE_CLAUDE_PIDFILE": str(tmp / "fake.pid"),
        "FAKE_CLAUDE_SPAWNLOG": str(tmp / "spawns.log"),
    }


def spawn_count(tmp: Path) -> int:
    p = tmp / "spawns.log"
    return len(p.read_text().splitlines()) if p.exists() else 0


def pid_alive(pidfile: Path) -> bool:
    if not pidfile.exists():
        return False
    try:
        pid = int(pidfile.read_text().strip())
    except ValueError:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # A zombie answers kill(pid, 0); read the state on Linux where available.
    stat_path = Path(f"/proc/{pid}/stat")
    if stat_path.exists():
        try:
            return stat_path.read_text().rsplit(")", 1)[1].split()[0] != "Z"
        except (OSError, IndexError):
            return True
    return True


def check_stream_contract(events: list[dict], *, expect_children: bool = True) -> list[str]:
    """Return the violations of the CLI stream contract the scope tracker relies on.

    Empty list = contract holds. Used offline on the committed fixtures AND live
    by the canary against the real CLI, so a CLI update that changes the schema
    fails loudly in exactly one place. Only fields the tracker/adapter read are
    checked; extra fields are ignored on purpose (tolerant parser, ADR-2236 D1).
    """
    bad: list[str] = []

    def need(ev: dict, fields: tuple[str, ...], label: str) -> None:
        for f in fields:
            if f not in ev:
                bad.append(f"{label} lacks '{f}'")

    results = [e for e in events if e.get("type") == "result"]
    if not results:
        bad.append("no result event")
    for r in results:
        need(r, ("subtype", "result"), "result")
    started = [e for e in events if e.get("subtype") == "task_started"]
    notified = [e for e in events if e.get("subtype") == "task_notification"]
    snaps = [e for e in events if e.get("subtype") == "background_tasks_changed"]
    if expect_children:
        if not started:
            bad.append("no task_started")
        if not notified:
            bad.append("no task_notification")
        if not snaps:
            bad.append("no background_tasks_changed")
        if len(results) < 2:
            bad.append(f"expected >=2 results (wake-up turn), got {len(results)}")
        elif not any((r.get("origin") or {}).get("kind") == "task-notification"
                     for r in results[1:]):
            bad.append("no wake-up result carries origin.kind=task-notification")
    for e in started:
        need(e, ("task_id", "run_id", "task_type", "description"), "task_started")
    for e in notified:
        need(e, ("task_id", "status", "summary"), "task_notification")
    for e in snaps:
        if not isinstance(e.get("tasks"), list):
            bad.append("background_tasks_changed.tasks is not a list")
        for t in e.get("tasks") or []:
            need(t, ("task_id", "task_type"), "snapshot task")
    for e in [x for x in events if x.get("subtype") == "task_updated"]:
        if "status" not in (e.get("patch") or {}):
            bad.append("task_updated lacks patch.status")
    if expect_children and snaps and snaps[-1].get("tasks"):
        bad.append("last background_tasks_changed snapshot is not empty (children never finished)")
    return bad
