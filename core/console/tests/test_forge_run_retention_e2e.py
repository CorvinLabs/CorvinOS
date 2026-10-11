"""Retention of forge runs: finished ones age out with their directory, a live run never does (ADR-2253 D8; T-0161/T-0163).

Aged files are real files with a real old mtime - no clock mocking. "Boot" is a real fresh interpreter importing
the module, because that is what every host does.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_forge_creator_e2e import ScriptedEngine, console  # noqa: E402

from corvin_console import forge_runs  # noqa: E402

DAY = 86_400


def _put(client, run_id: str, *, age_days: float, with_dir: bool = False, **fields) -> Path:
    rec = {"tenant_id": "_default", "kind": "tool", "status": "success", "phase": "promotion", "phases": ["promotion"],
           "progress": 100, "message": "done", "engine": "claude_code", "error": None,
           "created_at": "2026-09-01T00:00:00+00:00", "boot_id": "another-process", "heartbeat_at": 0, "resumes": 0}
    rec.update(fields)
    d = client.home / "tenants" / "_default" / "global" / "forge_runs"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{run_id}.json"
    f.write_text(json.dumps(rec))
    old = time.time() - age_days * DAY
    os.utime(f, (old, old))
    if with_dir:
        (d / run_id).mkdir(exist_ok=True)
        (d / run_id / "events.jsonl").write_text('{"seq": 1}\n')
    return f


def test_a_finished_run_past_the_ttl_is_removed_together_with_its_directory(tmp_path):
    with console(tmp_path, ScriptedEngine()) as client:
        old = _put(client, "run-aaaaaaaaaaaa", age_days=20, with_dir=True)
        young = _put(client, "run-bbbbbbbbbbbb", age_days=2, with_dir=True)
        forge_runs.prune("_default")
        assert not old.exists() and not old.with_suffix("").exists(), "record AND directory are removed together"
        assert young.exists() and young.with_suffix("").is_dir(), "a run younger than the TTL is untouched"
        # a poll of a pruned finished run is a plain 404
        assert client.get("/v1/console/forge-creator/status/run-aaaaaaaaaaaa").status_code == 404


def test_a_running_record_with_a_live_owner_survives_the_prune_however_old(tmp_path):
    with console(tmp_path, ScriptedEngine()) as client:
        f = _put(client, "run-cccccccccccc", age_days=40, status="running", heartbeat_at=time.time())
        forge_runs.prune("_default")
        assert f.exists() and json.loads(f.read_text())["status"] == "running"


def test_a_dead_owners_running_record_is_first_marked_interrupted_and_pruned_a_ttl_later(tmp_path):
    with console(tmp_path, ScriptedEngine()) as client:
        f = _put(client, "run-dddddddddddd", age_days=30, status="running", heartbeat_at=0)
        forge_runs.prune("_default")
        assert f.exists(), "it is not deleted in the same pass"
        rec = json.loads(f.read_text())
        assert rec["status"] == "failed" and rec["failure_code"] == "interrupted"
        assert (time.time() - f.stat().st_mtime) < DAY, "marking it makes it a fresh finished record"
        forge_runs.prune("_default")
        assert f.exists(), "still inside the TTL after being marked"
        old = time.time() - 20 * DAY
        os.utime(f, (old, old))
        forge_runs.prune("_default")
        assert not f.exists(), "a TTL later it is removed like any finished run"


def test_the_prune_ignores_foreign_files_and_traversal_shaped_names(tmp_path):
    with console(tmp_path, ScriptedEngine()) as client:
        d = client.home / "tenants" / "_default" / "global" / "forge_runs"
        d.mkdir(parents=True, exist_ok=True)
        keep = d / "notes.txt"
        keep.write_text("not a run")
        os.utime(keep, (1, 1))
        outside = client.home / "outside_dir"
        outside.mkdir()
        (d / "run-0123456789ab").symlink_to(outside)  # a planted symlink named like a run directory
        _put(client, "run-0123456789ab", age_days=20)
        forge_runs.prune("_default")
        assert keep.exists() and outside.exists(), "nothing outside the run files is ever touched"


def test_the_prune_runs_when_a_run_starts_for_a_tenant(tmp_path):
    with console(tmp_path, ScriptedEngine()) as client:
        old = _put(client, "run-eeeeeeeeeeee", age_days=20)
        forge_runs.new_run(tenant_id="_default", kind="tool", phases=("planning",), sid_fingerprint=None, resume={"x": 1})
        assert not old.exists()


def test_a_fresh_console_boot_prunes_every_tenant_even_one_that_never_starts_a_run(tmp_path):
    home = tmp_path / "corvin_home"
    runs_dir = home / "tenants" / "quiet_tenant" / "global" / "forge_runs"
    runs_dir.mkdir(parents=True)
    old = runs_dir / "run-ffffffffffff.json"
    old.write_text(json.dumps({"tenant_id": "quiet_tenant", "kind": "tool", "status": "failed", "boot_id": "x"}))
    t = time.time() - 30 * DAY
    os.utime(old, (t, t))
    repo = Path(__file__).resolve().parents[3]
    env = {**os.environ, "CORVIN_HOME": str(home), "CORVIN_TENANT_ID": "_default",
           "XDG_CONFIG_HOME": str(tmp_path / "xdg"), "CORVIN_AUDIT_ANCHOR_KEY": str(tmp_path / "anchor.key"),
           "PYTHONPATH": os.pathsep.join(str(repo / p) for p in ("core/console", ".", "corvin_operator", "corvin_operator/forge"))}
    code = ("import time, corvin_console.forge_runs as f\n"
            "for _ in range(50):\n    time.sleep(0.1)\n    if f.boot_prune_done(): break\n")
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-800:]
    assert not old.exists(), "a tenant that never starts a run must still be pruned at boot"
