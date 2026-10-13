"""Update button: thin client for the repo's own update.sh / update.ps1.

Real subprocess against a fake source tree, real FastAPI route (only session /
CSRF overridden), and the audit call is checked against the real signature.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from corvin_console import update_manager as um
from corvin_console.update_manager import UpdateManager, UpdateNotAllowedError

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="fake updater is a sh script")


def _tree(tmp_path: Path, script_body: str, version: str = "1.0.0") -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "pyproject.toml").write_text(f'[project]\nname = "corvinos"\nversion = "{version}"\n')
    (src / "update.sh").write_text(script_body)
    (src / "update.ps1").write_text("")
    return src


async def _wait_unlocked(mgr: UpdateManager, timeout: float = 10.0) -> None:
    end = time.time() + timeout
    while mgr.lock.locked() and time.time() < end:
        await asyncio.sleep(0.05)
    assert not mgr.lock.locked(), "updater never finished"


def test_real_repo_has_the_canonical_updater():
    assert um.find_source_tree() is not None
    assert (um.find_source_tree() / "update.sh").is_file()
    assert (um.find_source_tree() / "update.ps1").is_file()


def test_updater_command_per_platform(tmp_path):
    with patch.object(um.sys, "platform", "linux"):
        assert um.updater_command(tmp_path) == ["sh", str(tmp_path / "update.sh")]
    with patch.object(um.sys, "platform", "win32"):
        cmd = um.updater_command(tmp_path)
        assert cmd[0] == "powershell" and cmd[-2:] == ["-File", str(tmp_path / "update.ps1")]


def test_missing_updater_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_SRC_DIR", str(tmp_path))
    with patch.object(um, "_is_source_tree", return_value=False):
        with pytest.raises(UpdateNotAllowedError, match="not found"):
            um.find_source_tree()


@pytest.mark.asyncio
async def test_start_update_runs_script_and_records_last_update(tmp_path, monkeypatch):
    src = _tree(tmp_path, "#!/bin/sh\necho ran > \"$(dirname \"$0\")/ran.txt\"\nexit 0\n")
    monkeypatch.setenv("CORVIN_SRC_DIR", str(src))
    mgr = UpdateManager("_default", state_path=tmp_path / "state.json")
    calls = []
    with patch("corvin_console.audit.action_performed", lambda **kw: calls.append(("ok", kw))), \
         patch("corvin_console.audit.action_failed", lambda **kw: calls.append(("fail", kw))):
        before = time.time()
        res = mgr.start_update("sidfp")
        assert res["success"] is True
        await _wait_unlocked(mgr)
    assert (src / "ran.txt").read_text().strip() == "ran"
    last = mgr.last_update("1.0.0")
    assert last["status"] == "success" and before <= last["at"] <= time.time()
    assert [c[1]["action"] for c in calls] == ["settings.update_install", "settings.update_completed"]
    assert all(c[1]["sid_fingerprint"] == "sidfp" for c in calls)


@pytest.mark.asyncio
async def test_failed_updater_is_recorded_with_its_meaning(tmp_path, monkeypatch):
    src = _tree(tmp_path, "#!/bin/sh\nexit 1\n")
    monkeypatch.setenv("CORVIN_SRC_DIR", str(src))
    mgr = UpdateManager("_default", state_path=tmp_path / "state.json")
    mgr.start_update("sidfp")
    await _wait_unlocked(mgr)
    last = mgr.last_update("1.0.0")
    assert last["status"] == "failed" and "rolled back" in last["detail"]


@pytest.mark.asyncio
async def test_second_start_while_running_is_refused(tmp_path, monkeypatch):
    src = _tree(tmp_path, "#!/bin/sh\nsleep 1\nexit 0\n")
    monkeypatch.setenv("CORVIN_SRC_DIR", str(src))
    mgr = UpdateManager("_default", state_path=tmp_path / "state.json")
    mgr.start_update("sidfp")
    with pytest.raises(UpdateNotAllowedError, match="in progress"):
        mgr.start_update("sidfp")
    await _wait_unlocked(mgr)


def test_run_cut_off_by_restart_is_resolved_from_the_version(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"status": "running", "started_at": 1000.0, "version_before": "1.0.0"}))
    mgr = UpdateManager("_default", state_path=state)
    assert mgr.last_update("2.0.0")["status"] == "success"
    state.write_text(json.dumps({"status": "running", "started_at": 1000.0, "version_before": "1.0.0"}))
    assert mgr.last_update("1.0.0")["status"] == "interrupted"
    assert UpdateManager("_default", state_path=tmp_path / "none.json").last_update("1.0.0") is None


# ── real route ────────────────────────────────────────────────────────────

def _fake_record(tenant_id: str = "_default"):
    from corvin_console import auth as session_auth
    values: dict = {}
    for f in dataclasses.fields(session_auth.SessionRecord):
        if f.default is not dataclasses.MISSING:
            continue
        ann = str(f.type)
        if "float" in ann:
            values[f.name] = 1_000_000.0 + (3600 if f.name == "expires_at" else 0)
        elif "bool" in ann:
            values[f.name] = False
        elif f.name == "tier":
            tier = getattr(session_auth, "Tier", None)
            values[f.name] = next(iter(tier)) if tier else "owner"
        elif f.name == "tenant_id":
            values[f.name] = tenant_id
        else:
            values[f.name] = f"test-{f.name}"
    return session_auth.SessionRecord(**values)


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from corvin_console import deps as console_deps
    from corvin_console.routes import settings as settings_route

    monkeypatch.setattr(UpdateManager, "state_path", lambda self: tmp_path / "state.json")
    app = FastAPI()
    app.include_router(settings_route.router)
    rec = _fake_record()
    app.dependency_overrides[console_deps.require_session] = lambda: rec
    app.dependency_overrides[console_deps.require_csrf] = lambda: rec
    return TestClient(app)


def test_route_check_reports_last_update(client, tmp_path):
    (tmp_path / "state.json").write_text(json.dumps(
        {"status": "success", "started_at": 1.0, "finished_at": 1700000000.0,
         "version_before": "1.0.0", "version_after": "1.1.0"}))
    r = client.get("/settings/update/check")
    assert r.status_code == 200
    body = r.json()
    assert body["last_update"]["at"] == 1700000000.0 and body["last_update"]["status"] == "success"
    assert body["running"] is False


def test_route_install_starts_updater_and_reports_missing_one(client, tmp_path, monkeypatch):
    src = _tree(tmp_path, "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("CORVIN_SRC_DIR", str(src))
    r = client.post("/settings/update/install", json={})
    assert r.status_code == 200 and r.json()["success"] is True

    with patch.object(um, "find_source_tree", side_effect=UpdateNotAllowedError("update.sh not found")):
        assert client.post("/settings/update/install", json={}).status_code == 404


class TestAutoUpdateDefault:
    def test_default_is_true_without_config_file(self, client, tmp_path):
        with patch("corvin_console.routes.settings._launcher_config_path", return_value=tmp_path / "x" / "c.json"):
            assert client.get("/settings/auto-update").json()["enabled"] is True

    def test_default_is_true_when_key_absent(self, client, tmp_path):
        cfg = tmp_path / "c.json"
        cfg.write_text("{}")
        with patch("corvin_console.routes.settings._launcher_config_path", return_value=cfg):
            assert client.get("/settings/auto-update").json()["enabled"] is True

    def test_explicit_false_is_respected(self, client, tmp_path):
        cfg = tmp_path / "c.json"
        with patch("corvin_console.routes.settings._launcher_config_path", return_value=cfg):
            assert client.put("/settings/auto-update", json={"enabled": False}).status_code == 200
            assert client.get("/settings/auto-update").json()["enabled"] is False
