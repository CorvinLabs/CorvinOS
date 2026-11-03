"""LIVE: a fresh CorvinOS console fills the per-tenant memory page through a REAL chat turn.

Opt-in: ``CLAUDE_LIVE_E2E=1`` and the ``claude`` CLI on PATH (one short turn; the console
routes small conversational turns to Haiku by itself).

Nothing is mocked and nothing is pre-created: a brand-new temp ``CORVIN_HOME`` (no tenant
dirs, no recall.db), the console started exactly like ``corvin-serve`` starts it (uvicorn
``corvin_console.standalone:create_app --factory`` in its own process, on a free port), the
loopback local-login, the real chat WebSocket and the native ``claude`` engine. Asserted:

1. the fresh tenant's memory is an honest empty state;
2. after one real turn, ``GET /memory`` lists that turn from the tenant recall store, and
   ``recall.db`` lives under ``<CORVIN_HOME>/tenants/_default/global/memory/``;
3. a note added through the API is listed, readable, searchable (as is the turn) and
   stored under ``<CORVIN_HOME>/tenants/_default/global/memory/notes/``; deleting it
   removes the file;
4. a second tenant (``acme``) sees none of it — not in the index, not by name, not by
   search — and cannot delete it;
5. deleting the chat erases its turns from memory (deletion is erasure);
6. the SPA chunk the console serves for /console/app/memory is the one built from
   ``web-next/src/pages/memory.tsx``;
7. nothing landed in the shared (non-tenant) ``<CORVIN_HOME>/global/memory``.

Run (Windows):  set CLAUDE_LIVE_E2E=1 && .venv\\Scripts\\python.exe -m pytest -q -p no:cacheprovider
                --timeout=600 tests/e2e/live/test_console_memory_live.py
Run (Linux):    CLAUDE_LIVE_E2E=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
                --timeout=600 tests/e2e/live/test_console_memory_live.py
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DIST = REPO / "core" / "console" / "corvin_console" / "web-next" / "dist"

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("CLAUDE_LIVE_E2E", "") != "1" or shutil.which("claude") is None,
        reason="live console memory E2E needs CLAUDE_LIVE_E2E=1 and the claude CLI",
    ),
]

httpx = pytest.importorskip("httpx")
websockets = pytest.importorskip("websockets")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_console(home: Path, port: int, log_path: Path) -> subprocess.Popen:
    env = os.environ.copy()
    for k in ("FORGE_ROOT", "VOICE_AUDIT_PATH", "CORVIN_OS_MODEL_OVERRIDE", "CORVIN_ACP_PHASE",
              "CORVIN_SUPERVISED", "VOICE_CONFIG_DIR"):
        env.pop(k, None)
    env.update({
        "CORVIN_HOME": str(home),
        "CORVIN_TENANT_ID": "_default",
        "CORVIN_LOCAL_AUTOLOGIN": "1",
        "XDG_CONFIG_HOME": str(home.parent / "xdg"),
        "PYTHONUTF8": "1",
    })
    cmd = [sys.executable, "-m", "uvicorn", "corvin_console.standalone:create_app", "--factory",
           "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"]
    if sys.platform == "win32":
        cmd += ["--loop", "asyncio"]  # Proactor loop: engine subprocess spawn (serve_backend)
    log_fh = open(log_path, "w", encoding="utf-8")
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(cmd, env=env, cwd=str(home.parent), stdin=subprocess.DEVNULL,
                            stdout=log_fh, stderr=subprocess.STDOUT, **kwargs)


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        # Kill the whole tree (uvicorn + any engine child) — only OUR pid, never port 8765.
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, check=False)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()


async def _ws_turn(ws_url: str, cookie: str, text: str, timeout: float) -> list[dict]:
    events: list[dict] = []
    async with websockets.connect(ws_url, additional_headers={"Cookie": f"corvin_console_sid={cookie}"},
                                  max_size=16 * 1024 * 1024) as ws:
        ready = json.loads(await ws.recv())
        assert ready.get("type") == "ready", ready
        await ws.send(json.dumps({"type": "user", "text": text, "voice_on": False}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.time())))
            events.append(ev)
            if ev.get("type") == "done":
                return events
    raise AssertionError(f"no done within {timeout}s: {[e.get('type') for e in events]}")


def test_fresh_console_memory_fills_from_a_real_chat_turn(tmp_path: Path, monkeypatch):
    home = tmp_path / "corvin_home"          # does NOT exist yet — a truly fresh install
    port = _free_port()
    assert port != 8765
    log_path = tmp_path / "console.log"
    proc = _start_console(home, port, log_path)
    base = f"http://127.0.0.1:{port}"
    api = f"{base}/v1/console"
    tenant = home / "tenants" / "_default"
    try:
        # ── boot ────────────────────────────────────────────────────────────
        deadline = time.time() + 180
        while True:
            assert proc.poll() is None, f"console exited rc={proc.returncode}: " + \
                log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
            try:
                if httpx.get(f"{api}/healthz", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            assert time.time() < deadline, "console did not come up within 180s"
            time.sleep(1)

        a = httpx.Client(base_url=api, timeout=120, follow_redirects=False)
        r = a.get("/auth/local-login")
        assert r.status_code in (200, 302, 303, 307), r.text[:300]
        cookie = a.cookies.get("corvin_console_sid")
        assert cookie, "local-login set no session cookie"
        who = a.get("/auth/whoami").json()
        assert who["tenant_id"] == "_default"
        a.headers["X-CSRF-Token"] = who["csrf_token"]

        # ── 1. fresh tenant: honest empty state ────────────────────────────
        idx0 = a.get("/memory").json()
        assert idx0["tenant_id"] == "_default" and idx0["available"] is True
        assert idx0["count"] == 0 and idx0["files"] == [], idx0

        # ── 2. one REAL chat turn through the console WebSocket ────────────
        token = "OKAPI" + secrets.token_hex(3).upper()
        sid = a.post("/chat/sessions", json={"title": "memory live"}).json()["session"]["sid"]
        ws_url = f"ws://127.0.0.1:{port}/v1/console/chat/sessions/{sid}/stream"
        prompt = f"The project codeword is {token}. Reply with exactly the single word PONG and nothing else."
        events = asyncio.run(_ws_turn(ws_url, cookie, prompt, timeout=300))
        errors = [e for e in events if e.get("type") == "error"]
        results = [e for e in events if e.get("type") == "result"]
        assert not errors, errors
        assert results and "PONG" in (results[-1].get("text") or "").upper(), \
            [(e.get("type"), str(e)[:200]) for e in events][-10:]

        idx1 = a.get("/memory").json()
        turns = [f for f in idx1["files"] if f["source"] == "conversation"]
        assert idx1["recall"]["turns"] == 1 and len(turns) == 1, idx1
        assert turns[0]["origin"] == f"web:{sid}" and token in turns[0]["title"]
        turn = a.get(f"/memory/{turns[0]['name']}").json()
        assert token in turn["user_text"] and "PONG" in turn["assistant_text"].upper()
        assert (tenant / "global" / "memory" / "recall.db").is_file()
        hits = a.get("/memory/search", params={"q": token}).json()["results"]
        assert [h["name"] for h in hits] == [turns[0]["name"]]

        # ── 3. add / read / search a note ─────────────────────────────────
        r = a.post("/memory", json={"title": "Release rule",
                                    "content": f"Releases need the {token} sign-off.", "type": "project"})
        assert r.status_code == 200, r.text
        note = r.json()["name"]
        note_file = tenant / "global" / "memory" / "notes" / f"{note}.json"
        assert note_file.is_file()
        assert a.get(f"/memory/{note}").json()["content"] == f"Releases need the {token} sign-off."
        names = {h["name"] for h in a.get("/memory/search", params={"q": token}).json()["results"]}
        assert names == {note, turns[0]["name"]}
        idx2 = a.get("/memory").json()
        assert idx2["count"] == 2 and idx2["sources"]["note"] == 1
        assert a.post("/memory", json={"title": "x", "content": "y"},
                      headers={"X-CSRF-Token": ""}).status_code == 403

        # ── 4. a second tenant sees none of it ─────────────────────────────
        monkeypatch.setenv("CORVIN_HOME", str(home))
        for p in (str(REPO / "core" / "console"), str(REPO / "corvin_operator" / "forge")):
            if p not in sys.path:
                sys.path.insert(0, p)
        from corvin_console import auth as session_auth  # noqa: PLC0415
        rec_b = session_auth.create_session(tenant_id="acme")
        b = httpx.Client(base_url=api, timeout=60,
                         headers={"X-CSRF-Token": session_auth.derive_csrf_token(rec_b.csrf_secret, rec_b.sid)})
        b.cookies.set("corvin_console_sid", rec_b.sid)
        ib = b.get("/memory")
        assert ib.status_code == 200, ib.text[:300]
        assert ib.json()["tenant_id"] == "acme" and ib.json()["count"] == 0 and ib.json()["files"] == []
        assert token not in ib.text
        assert b.get(f"/memory/{note}").status_code == 404
        assert b.get(f"/memory/{turns[0]['name']}").status_code == 404
        assert b.get("/memory/search", params={"q": token}).json()["results"] == []
        assert b.delete(f"/memory/{note}").status_code == 404
        assert note_file.is_file()

        # ── 5. delete the note; delete the chat → its turns leave memory ───
        assert a.delete(f"/memory/{note}").status_code == 200
        assert not note_file.exists()
        assert a.delete(f"/chat/sessions/{sid}").status_code in (200, 204)
        idx3 = a.get("/memory").json()
        assert idx3["count"] == 0 and idx3["recall"]["turns"] == 0, idx3

        # ── 6. the console serves the memory page built from memory.tsx ────
        assert a.get(f"{base}/console/app/memory").status_code == 200
        chunks = sorted(DIST.glob("assets/memory-*.js"))
        assert chunks, "web-next/dist has no memory chunk — run `npm run build`"
        served = httpx.get(f"{base}/console/assets/{chunks[-1].name}", timeout=30)
        assert served.status_code == 200 and "memory-add-btn" in served.text

        # ── 7. everything stayed inside the tenant dir ─────────────────────
        assert not (home / "global" / "memory").exists()
        audit = (tenant / "global" / "forge" / "audit.jsonl").read_text(encoding="utf-8")
        assert "memory.turn_indexed" in audit and "memory.note_add" in audit
        assert token not in audit, "turn/note text must never reach the audit chain"
    finally:
        _stop(proc)
