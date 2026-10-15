"""Auto-update on start is commit-based, runs once per remote commit, and is CALLED.

Real git repos (bare origin + clone), real updater subprocess, real lifespans.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from corvin_console import update_manager as um
from corvin_console.update_manager import UpdateManager

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="fake updater is a sh script")


def _git(cwd: Path, *a: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *a],
                          check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """origin (bare) + a checkout with pyproject.toml and a fake update.sh."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    src = tmp_path / "src"
    subprocess.run(["git", "clone", "-q", str(origin), str(src)], check=True, capture_output=True)
    (src / "pyproject.toml").write_text('[project]\nname = "corvinos"\nversion = "1.0.0"\n')
    (src / "update.sh").write_text('#!/bin/sh\ndate +%s >> "$(dirname "$0")/runs.txt"\nexit 0\n')
    (src / "update.ps1").write_text("")
    _git(src, "checkout", "-q", "-B", "main")
    _git(src, "add", "-A"); _git(src, "commit", "-q", "-m", "init"); _git(src, "push", "-q", "origin", "main")
    monkeypatch.setenv("CORVIN_SRC_DIR", str(src))
    monkeypatch.setattr(UpdateManager, "state_path", lambda self: tmp_path / "state.json")
    monkeypatch.setattr(um, "_use_systemd_run", lambda: False)
    return src, origin


def _push_commit_without_version_bump(src: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(src.parent / "origin.git"), str(other)], check=True, capture_output=True)
    (other / "feature.txt").write_text("x")
    _git(other, "add", "-A"); _git(other, "commit", "-q", "-m", "feat: no version bump"); _git(other, "push", "-q", "origin", "main")


def test_commit_without_version_bump_is_an_update(repo, tmp_path):
    src, _ = repo
    res = asyncio.run(UpdateManager("_default").check_update())
    assert (res.has_update, res.behind) == (False, 0)
    _push_commit_without_version_bump(src, tmp_path)
    res = asyncio.run(UpdateManager("_default").check_update())
    assert res.has_update is True and res.behind == 1
    assert res.current_version == "1.0.0"          # version unchanged, still an update
    assert res.local_sha != res.remote_sha


@pytest.mark.asyncio
async def test_auto_update_disabled_and_up_to_date_do_nothing(repo):
    src, _ = repo
    assert await um.auto_update_on_start("_default", enabled=False, delay_s=0) == "disabled"
    assert await um.auto_update_on_start("_default", enabled=True, delay_s=0) == "up-to-date"
    assert not (src / "runs.txt").exists()


@pytest.mark.asyncio
async def test_auto_update_runs_on_new_commit_and_only_once_per_commit(repo, tmp_path):
    src, _ = repo
    _push_commit_without_version_bump(src, tmp_path)
    with patch("corvin_console.audit.action_performed"), patch("corvin_console.audit.action_failed"):
        assert await um.auto_update_on_start("_default", enabled=True, delay_s=0) == "started"
        mgr = UpdateManager("_default")
        end = time.time() + 10
        while mgr.lock.locked() and time.time() < end:
            await asyncio.sleep(0.05)
        assert (src / "runs.txt").read_text().count("\n") == 1
        # The fake updater does not pull, so HEAD is still behind - exactly the
        # rolled-back case. The same remote commit must not be attempted again.
        assert await um.auto_update_on_start("_default", enabled=True, delay_s=0) == "already-tried"
    assert (src / "runs.txt").read_text().count("\n") == 1


def test_isolated_wrapper_records_result_even_if_console_is_gone(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"status": "running", "started_at": 1.0, "version_before": "1.0.0"}))
    tree = tmp_path / "tree"; tree.mkdir()
    (tree / "pyproject.toml").write_text('[project]\nversion = "7.7.7"\n')
    subprocess.run([sys.executable, "-m", "corvin_console.update_manager", "--record", str(state), "1", str(tree)],
                   check=True, cwd=str(Path(um.__file__).resolve().parents[1]))
    st = json.loads(state.read_text())
    assert st["version_after"] == "7.7.7"
    assert st["status"] == "failed" and "rolled back" in st["detail"] and st["finished_at"] > 1.0
    assert st["phase"] == "failed" and st["steps"]["update_script"]["status"] == "failed"


def test_systemd_run_command_runs_updater_then_records(tmp_path):
    cmd = um.isolated_command(tmp_path, tmp_path / "s.json", tmp_path / "l.log")
    assert cmd[0] == "systemd-run" and "--wait" in cmd and "--collect" in cmd
    assert str(tmp_path / "update.sh") in cmd and "--record" in " ".join(cmd)


# ── the call sites: the mechanism is actually reached by both hosts ─────────

def test_console_lifespan_calls_the_hook():
    from fastapi.testclient import TestClient
    from corvin_console.app import create_app
    with patch.object(um, "schedule_auto_update") as hook:
        with TestClient(create_app()):
            pass
    assert hook.call_count == 1


def test_gateway_lifespan_calls_the_hook():
    from fastapi.testclient import TestClient
    from corvin_gateway.app import app
    with patch.object(um, "schedule_auto_update") as hook:
        with TestClient(app):
            pass
    assert hook.call_count == 1
