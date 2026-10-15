"""Update trace: every stage of a run is visible, and success is PROVEN, not assumed.

Driven through the real Settings routes (POST install = the user's click, GET check = the
card's poll) against real git repos and a real fake-updater subprocess that speaks the
real progress protocol (``CORVIN_UPDATE_PROGRESS``). Plus the shipped ``update.sh``
``progress`` function itself, so the producer side is not a hand-written imitation.
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
from test_update_button_e2e import _fake_record

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="fake updater is a sh script")

ENTRY = "assets/index-abc123.js"


def _git(cwd: Path, *a: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *a],
                          check=True, capture_output=True, text=True).stdout.strip()


def _updater(body: str) -> str:
    """update.sh stand-in: ``emit PHASE STATUS DETAIL`` writes the same JSON lines update.sh does."""
    return ('#!/bin/sh\nemit() { printf \'{"phase":"%s","status":"%s","detail":"%s","ts":%s}\\n\' '
            '"$1" "$2" "$3" "$(date +%s)" >>"$CORVIN_UPDATE_PROGRESS"; }\n'
            'cd "$(dirname "$0")"\n' + body)


GOOD = _updater('''emit delivery running
git fetch -q origin main && git merge -q --ff-only origin/main
emit delivery ok "$(git rev-parse HEAD)"
emit ui_build running
mkdir -p core/console/corvin_console/web-next/dist
echo '<script src="/console/assets/index-abc123.js"></script>' > core/console/corvin_console/web-next/dist/index.html
emit ui_build ok "assets/index-abc123.js"
emit verification running
emit verification ok
exit 0
''')


@pytest.fixture
def world(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    src = tmp_path / "src"
    subprocess.run(["git", "clone", "-q", str(origin), str(src)], check=True, capture_output=True)
    (src / "pyproject.toml").write_text('[project]\nname = "corvinos"\nversion = "1.0.0"\n')
    (src / "update.ps1").write_text("")
    _git(src, "checkout", "-q", "-B", "main")
    monkeypatch.setenv("CORVIN_SRC_DIR", str(src))
    monkeypatch.setattr(UpdateManager, "state_path", lambda self: tmp_path / "state.json")
    monkeypatch.setattr(um, "_use_systemd_run", lambda: False)

    def publish(updater: str) -> None:
        (src / "update.sh").write_text(updater)
        _git(src, "add", "-A"); _git(src, "commit", "-q", "-m", "init"); _git(src, "push", "-q", "-f", "origin", "main")
        other = tmp_path / "other"
        if not other.exists():
            subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True, capture_output=True)
        (other / "feature.txt").write_text(str(time.time()))
        _git(other, "add", "-A"); _git(other, "commit", "-q", "-m", "feat: new"); _git(other, "push", "-q", "origin", "main")
        _git(src, "fetch", "-q", "origin", "main")

    return src, publish, tmp_path


@pytest.fixture
def client(world):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from corvin_console import deps as console_deps
    from corvin_console.routes import settings as settings_route
    app = FastAPI()
    app.include_router(settings_route.router)
    rec = _fake_record()
    app.dependency_overrides[console_deps.require_session] = lambda: rec
    app.dependency_overrides[console_deps.require_csrf] = lambda: rec
    with TestClient(app) as c:   # one event loop for the whole test: the watcher task must survive the POST
        yield c


def _settle(client, timeout: float = 15.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        body = client.get("/settings/update/check").json()
        if not body["running"]:
            return body
        time.sleep(0.1)
    raise AssertionError("updater never finished")


def test_full_run_through_the_settings_click_is_visible_and_proven(world, client):
    src, publish, tmp = world
    publish(GOOD)
    before = time.time()
    with patch("corvin_console.audit.action_performed"), patch("corvin_console.audit.action_failed"):
        assert client.post("/settings/update/install", json={}).status_code == 200
        body = _settle(client)
    last = body["last_update"]
    assert last["status"] == "success" and last["phase"] == "completed"
    assert last["trigger"] == "manual"
    assert [last["steps"][p]["status"] for p in um.PHASES] == ["ok"] * 5
    assert last["checks"] == {"delivered": True, "ui_rebuilt": True, "script_ok": True,
                              "verified": True, "up_to_date": True}
    assert last["last_success_at"] >= before
    assert body["has_update"] is False          # live check: nothing left to install
    assert last["head_after"] == _git(src, "rev-parse", "HEAD") == last["target_sha"]


def test_exit_zero_without_a_rebuilt_ui_is_not_a_success(world, client):
    src, publish, tmp = world
    prior = 1234567.0
    (tmp / "state.json").write_text(json.dumps({"status": "success", "finished_at": prior, "last_success_at": prior}))
    publish(_updater('''emit delivery running
git fetch -q origin main && git merge -q --ff-only origin/main
emit delivery ok "$(git rev-parse HEAD)"
emit ui_build failed "frontend not rebuilt"
emit verification ok
exit 0
'''))
    with patch("corvin_console.audit.action_performed"), patch("corvin_console.audit.action_failed"):
        client.post("/settings/update/install", json={})
        last = _settle(client)["last_update"]
    assert last["status"] == "failed" and last["failed_phase"] == "ui_build"
    assert last["checks"]["ui_rebuilt"] is False and last["checks"]["script_ok"] is True
    assert last["last_success_at"] == prior     # the earlier real success is kept, not overwritten


def test_a_delivery_claim_that_does_not_match_the_tree_is_caught(world, client):
    src, publish, tmp = world
    publish(GOOD.replace('emit delivery ok "$(git rev-parse HEAD)"', 'emit delivery ok "deadbeefdeadbeef"'))
    with patch("corvin_console.audit.action_performed"), patch("corvin_console.audit.action_failed"):
        client.post("/settings/update/install", json={})
        last = _settle(client)["last_update"]
    assert last["checks"]["delivered"] is False
    assert last["status"] != "success" and last["last_success_at"] is None


def test_updater_that_never_moved_the_code_is_not_up_to_date(world, client):
    src, publish, tmp = world
    publish(GOOD.replace("git fetch -q origin main && git merge -q --ff-only origin/main\n", ""))
    with patch("corvin_console.audit.action_performed"), patch("corvin_console.audit.action_failed"):
        client.post("/settings/update/install", json={})
        last = _settle(client)["last_update"]
    assert last["checks"]["up_to_date"] is False
    assert last["status"] == "unverified" and "verification" in last["detail"]
    assert last["last_success_at"] is None


def test_rolled_back_run_reports_failed_verification(world, client):
    src, publish, tmp = world
    publish(_updater('emit delivery ok "x"\nemit ui_build ok "assets/index-abc123.js"\n'
                     'emit verification failed "the updated console did not verify"\nexit 1\n'))
    with patch("corvin_console.audit.action_performed"), patch("corvin_console.audit.action_failed") as failed:
        client.post("/settings/update/install", json={})
        last = _settle(client)["last_update"]
    assert (last["status"], last["phase"], last["failed_phase"]) == ("failed", "failed", "verification")
    assert last["steps"]["update_script"]["status"] == "failed" and "rolled back" in last["detail"]
    assert failed.call_count == 1


def test_running_update_shows_the_live_phase(world, client):
    src, publish, tmp = world
    publish(_updater('emit delivery running\nsleep 1.5\nemit delivery ok "x"\nexit 0\n'))
    with patch("corvin_console.audit.action_performed"), patch("corvin_console.audit.action_failed"):
        client.post("/settings/update/install", json={})
        deadline, seen = time.time() + 5, None
        while time.time() < deadline and seen is None:
            last = client.get("/settings/update/check").json()["last_update"]
            if last["phase"] == "delivery":
                seen = last
            time.sleep(0.05)
        assert seen and seen["status"] == "running" and seen["steps"]["delivery"]["status"] == "running"
        _settle(client)


def test_auto_update_run_is_marked_auto(world):
    src, publish, tmp = world
    publish(GOOD)
    with patch("corvin_console.audit.action_performed"), patch("corvin_console.audit.action_failed"):
        async def go():
            assert await um.auto_update_on_start("_default", enabled=True, delay_s=0) == "started"
            mgr = UpdateManager("_default")
            while mgr.lock.locked():
                await asyncio.sleep(0.05)
            return mgr.last_update("1.0.0")
        last = asyncio.run(go())
    assert last["trigger"] == "auto" and last["status"] == "success" and last["last_success_at"]


def test_shipped_update_sh_progress_function_writes_valid_json(tmp_path):
    text = (um.find_source_tree() / "update.sh").read_text()
    start = text.index('PROGRESS="${CORVIN_UPDATE_PROGRESS:-}"')
    fn = text[start:text.index("\n}\n", start) + 3]
    out = tmp_path / "p.jsonl"
    subprocess.run(["sh", "-c", fn + '\nprogress delivery ok "abc123 \\"quoted\\" $(x)"\nprogress ui_build failed\n'],
                   check=True, env={"CORVIN_UPDATE_PROGRESS": str(out), "PATH": "/usr/bin:/bin"})
    events = um.read_progress(out)
    assert [(e["phase"], e["status"]) for e in events] == [("delivery", "ok"), ("ui_build", "failed")]
    assert "\"" not in events[0]["detail"] and "$" not in events[0]["detail"]
    unset = subprocess.run(["sh", "-c", fn + "\nprogress delivery ok x"], env={"PATH": "/usr/bin:/bin"})
    assert unset.returncode == 0               # a manual run (no env) is a silent no-op


def test_torn_progress_lines_are_ignored(tmp_path):
    f = tmp_path / "p.jsonl"
    f.write_text('{"phase":"delivery","status":"ok","detail":"x","ts":1}\n{"phase":"deliv\nnot json\n'
                 '{"phase":"bogus","status":"ok"}\n')
    assert [e["phase"] for e in um.read_progress(f)] == ["delivery"]
