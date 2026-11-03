"""LIVE: a fresh CorvinOS install gets its own knowledge base, a working Tasks board and LDD gates
that read it -- with REAL LLM calls (2026-11-03).

Opt-in: ``CORVIN_LIVE_CLAUDE=1`` and the ``claude`` CLI on PATH, logged in. Without both the
module is skipped and proves nothing. Cost: three short ``claude -p --model haiku`` runs plus one
console chat turn.

Nothing is pre-created: a brand-new temp ``CORVIN_HOME`` (no tenant dirs), the console started
exactly like ``corvinos-serve`` (uvicorn ``corvin_console.standalone:create_app --factory``, its own
process, a free port -- never 8765, never the operator's ``~/.corvin``). The plugin source is the
sibling Corvin-Marketplace checkout, as on every install that has one. Asserted, in order:

1. boot alone installs AND enables the Knowledge Graph plugin and creates
   ``<CORVIN_HOME>/tenants/_default/knowledge`` (a git repo with the pinned engine, LF bytes);
   ``/kb/status`` is ok+enabled; the Knowledge Graph panel and Quality Gates read that KB;
2. the console Tasks API creates initiative -> epic -> task IN the KB and moves the task through
   the state machine (in_progress -> done needs a definition of done); the board shows it;
3. ``kb new concept`` / ``kb new decision`` (the gate skills' sanctioned path) work in the tenant KB;
4. the ADR gate and the Concept gate, run by a REAL model with the injected (rendered) skill text and
   the real path gate as PreToolUse hook, call the tenant KB's ``kb.py`` by its pinned path, find
   the existing ADR / concept that exist ONLY in this KB, and return a verdict;
5. the console chat agent (a REAL chat turn) files a task into the tenant KB through ``kb.py`` and
   the board shows it;
6. ``kb check`` reports ``blocking: 0``;
7. a second tenant (its own host process, ``CORVIN_TENANT_ID=acme``) gets its OWN KB and sees
   none of the first tenant's items, entities or cards, and the path gate pins only its own KB.

Run (Windows):  set CORVIN_LIVE_CLAUDE=1 && .venv\\Scripts\\python.exe -m pytest -q -s -p no:cacheprovider
                -o addopts="" --timeout=1800 tests/e2e/live/test_knowledge_graph_fresh_install_live.py
Run (Linux):    CORVIN_LIVE_CLAUDE=1 .venv/bin/python -m pytest -q -s -p no:cacheprovider -o addopts="" \\
                --timeout=1800 tests/e2e/live/test_knowledge_graph_fresh_install_live.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
HOOK = REPO / "corvin_operator" / "voice" / "hooks" / "path_gate.py"
ENGINE_JSON = (REPO.parent / "Corvin-Marketplace" / "plugins" / "buildin" / "knowledge_management"
               / "corvin_knowledge" / "src" / "kb_engine" / "ENGINE.json")
TT = "/task-tracking"

pytestmark = [
    pytest.mark.live,
    pytest.mark.timeout(1800),
    pytest.mark.skipif(
        os.environ.get("CORVIN_LIVE_CLAUDE", "") != "1" or shutil.which("claude") is None,
        reason="live knowledge-graph E2E needs CORVIN_LIVE_CLAUDE=1 and the claude CLI on PATH",
    ),
]

httpx = pytest.importorskip("httpx")
websockets = pytest.importorskip("websockets")

_SCRUB = ("FORGE_ROOT", "VOICE_AUDIT_PATH", "CORVIN_OS_MODEL_OVERRIDE", "CORVIN_ACP_PHASE", "CORVIN_SUPERVISED",
          "VOICE_CONFIG_DIR", "CORVIN_KB_REPO", "CORVIN_KB_TENANT", "CORVIN_ADR_ROOT", "CLAUDECODE",
          "CLAUDE_CODE_ENTRYPOINT", "CORVIN_SESSION_DIR",
          # a console started by this test is a real install: kb_projection.start() refuses under pytest
          "PYTEST_CURRENT_TEST", "PYTEST_VERSION", "PYTEST_ADDOPTS")


def _log(msg: str) -> None:
    print(f"[kg-live] {msg}", flush=True)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _env(home: Path, tenant: str) -> dict[str, str]:
    env = os.environ.copy()
    for k in _SCRUB:
        env.pop(k, None)
    env.update({"CORVIN_HOME": str(home), "CORVIN_TENANT_ID": tenant, "CORVIN_LOCAL_AUTOLOGIN": "1",
                "XDG_CONFIG_HOME": str(home.parent / "xdg"), "PYTHONUTF8": "1"})
    return env


def _start_console(home: Path, port: int, log_path: Path, tenant: str) -> subprocess.Popen:
    assert port != 8765
    cmd = [sys.executable, "-m", "uvicorn", "corvin_console.standalone:create_app", "--factory",
           "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"]
    kwargs: dict = {}
    if sys.platform == "win32":
        cmd += ["--loop", "asyncio"]
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(cmd, env=_env(home, tenant), cwd=str(home.parent), stdin=subprocess.DEVNULL,
                            stdout=open(log_path, "w", encoding="utf-8"), stderr=subprocess.STDOUT, **kwargs)


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":   # only OUR process tree, never the operator's console
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()


def _wait(pred, what: str, timeout: float, proc: subprocess.Popen, log_path: Path, every: float = 1.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        assert proc.poll() is None, f"console exited rc={proc.returncode}: " + \
            log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
        try:
            last = pred()
            if last:
                return last
        except (httpx.HTTPError, OSError, ValueError, KeyError):
            pass
        time.sleep(every)
    raise AssertionError(f"timed out after {timeout:.0f}s waiting for {what} (last={last!r})\n"
                         + log_path.read_text(encoding="utf-8", errors="replace")[-3000:])


def _kb(kb: Path, *args: str, actor: str = "human:operator") -> tuple[int, dict]:
    r = subprocess.run([sys.executable, str(kb / "scripts" / "kb.py"), "--repo", str(kb), *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "KB_ACTOR": actor, "PYTHONUTF8": "1"}, timeout=180)
    out = r.stdout.strip()
    try:
        return r.returncode, json.loads(out) if out.startswith("{") else {"raw": out + r.stderr}
    except ValueError:
        return r.returncode, {"raw": out[-2000:] + r.stderr[-2000:]}


def _rendered_gate_skill(name: str, home: Path, tenant: str, monkeypatch) -> str:
    """The skill text exactly as skill_inject puts it into a turn for this tenant."""
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_TENANT_ID", tenant)
    monkeypatch.delenv("CORVIN_KB_REPO", raising=False)
    from corvin_operator.bridges.shared import skill_inject  # noqa: PLC0415

    bodies = {spec.name: spec.body for _s, spec in skill_inject._load_core_quality_skills(tenant)}
    return bodies[name]


def _run_gate(name: str, skill: str, task: str, work: Path, env: dict[str, str]) -> dict:
    """One REAL model run of a gate skill: haiku, shell tools only (Bash; PowerShell is the shell tool of Claude Code on Windows), the real path gate as PreToolUse hook."""
    sp = work / f"{name}.system.md"
    sp.write_text(skill, encoding="utf-8")
    py = Path(sys.executable).as_posix()
    settings = work / f"{name}.settings.json"
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash|PowerShell", "hooks": [
        # program + args (no shell string): the hook runs the same under Git Bash, PowerShell and sh
        {"type": "command", "command": py, "args": [HOOK.as_posix()], "timeout": 60,
         "onFailure": "block"}]}]}}), encoding="utf-8")
    prompt = (
        f"Run the {name} for this completed task, following the skill in your system prompt.\n\n"
        f"Completed task: {task}\n\n"
        "Do ONLY step 1 of the shared knowledge-gate mechanism plus the check: search the knowledge base with "
        "`find` (decisions AND concepts) using the exact kb.py command line from your instructions, open a hit "
        "with `show`, and run `check`. Do NOT create, edit or commit anything. Then end your answer with ONE "
        'line exactly of the form: VERDICT {"gate": "<gate>", "decision": "write|amend|skip", '
        '"existing_id": "<ID of the existing entry to amend, or null>", "kb_blocking": <blocking count>}'
    )
    cmd = [shutil.which("claude"), "-p", prompt, "--model", "haiku", "--output-format", "stream-json", "--verbose",
           "--append-system-prompt-file", str(sp), "--settings", str(settings), "--allowedTools", "Bash", "PowerShell",
           "--max-turns", "12"]
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(work), env=env, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, stdin=subprocess.DEVNULL)
    (work / f"{name}.stream.jsonl").write_text(r.stdout, encoding="utf-8")   # evidence, kept with tmp_path
    events = []
    for ln in r.stdout.splitlines():
        try:
            events.append(json.loads(ln))
        except ValueError:
            continue
    bash, results, final = [], [], ""
    for ev in events:
        if ev.get("type") == "assistant":
            for c in (ev.get("message") or {}).get("content") or []:
                if c.get("type") == "tool_use":
                    inp = c.get("input") or {}
                    bash.append(str(inp.get("command") or json.dumps(inp))[:2000])
        elif ev.get("type") == "user":
            for c in (ev.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    body = c.get("content")
                    results.append(json.dumps(body) if not isinstance(body, str) else body)
        elif ev.get("type") == "result":
            final = str(ev.get("result") or "")
    m = re.findall(r"VERDICT\s*(\{.*?\})", final, flags=re.S)
    verdict = json.loads(m[-1]) if m else None
    out = {"rc": r.returncode, "bash": bash, "results": results, "final": final, "verdict": verdict,
           "seconds": round(time.time() - t0, 1), "stderr": r.stderr[-1500:],
           "model": next((ev.get("model") for ev in events if ev.get("type") == "system" and ev.get("model")), None),
           "cost_usd": next((ev.get("total_cost_usd") for ev in events if ev.get("type") == "result"), None)}
    _log(f"{name}: rc={out['rc']} model={out['model']} {out['seconds']}s cost=${out['cost_usd']} "
         f"bash={len(bash)} verdict={verdict}")
    for c in bash:
        _log(f"  {name} Bash> {c[:220]}")
    return out


async def _chat_turn(ws_url: str, cookie: str, text: str, timeout: float) -> list[dict]:
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


def test_fresh_install_knowledge_graph_board_and_gates_live(tmp_path: Path, monkeypatch):
    home = tmp_path / "corvin_home"          # does NOT exist yet: a truly fresh install
    assert not home.exists()
    kb = home / "tenants" / "_default" / "knowledge"
    port_a = _free_port()
    log_a = tmp_path / "console-default.log"
    token = "ZEBRA" + secrets.token_hex(3).upper()
    proc_a = _start_console(home, port_a, log_a, "_default")
    proc_b = None
    try:
        api = f"http://127.0.0.1:{port_a}/v1/console"
        _wait(lambda: httpx.get(f"{api}/healthz", timeout=3).status_code == 200, "console A healthz", 240,
              proc_a, log_a)
        a = httpx.Client(base_url=api, timeout=180, follow_redirects=False)
        assert a.get("/auth/local-login").status_code in (200, 302, 303, 307)
        cookie = a.cookies.get("corvin_console_sid")
        who = a.get("/auth/whoami").json()
        assert who["tenant_id"] == "_default"
        a.headers["X-CSRF-Token"] = who["csrf_token"]

        # ── 1. boot alone: plugin installed + enabled, tenant KB created and bound ─────────────
        def _plugin():
            rows = {p["plugin_id"]: p for p in a.get("/plugins").json()["plugins"]}
            rec = rows.get("corvin_knowledge")
            return rec if rec and rec.get("enabled") else None
        rec = _wait(_plugin, "the Knowledge Graph plugin installed+enabled by boot", 240, proc_a, log_a)
        _log(f"plugin: version={rec.get('version')} origin={rec.get('origin')} enabled={rec.get('enabled')} "
             f"type={rec.get('plugin_type')}")
        st = _wait(lambda: (lambda s: s if s.get("state") == "ok" and s.get("enabled") else None)(
            a.get(f"{TT}/kb/status").json()), "the tenant KB projected (kb/status ok)", 180, proc_a, log_a)
        _log(f"kb/status: state={st['state']} enabled={st['enabled']} items={st.get('items')}")
        assert (kb / "scripts" / "kb.py").is_file() and (kb / ".git").exists()
        assert (kb / "kb" / "_meta" / "tenant_kb.json").is_file()
        pins = json.loads(ENGINE_JSON.read_text(encoding="utf-8"))["files"]
        for n, digest in pins.items():   # byte-exact: what the path gate pins (LF, even from a CRLF checkout)
            assert hashlib.sha256((kb / "scripts" / n).read_bytes()).hexdigest() == digest, n
        assert a.get("/plugins/corvin-knowledge/config").json()["repo_path"] == str(kb)
        g0 = a.get("/plugins/corvin-knowledge/graph")
        assert g0.status_code == 200, g0.text[:300]
        q = a.get("/api/quality/gates/status").json()
        assert Path(q["source_root"]) == kb, q.get("source_root")
        _log(f"tenant KB: {kb}  (graph entities={len(g0.json().get('entities', []))}, quality root ok)")

        # ── 2. Tasks board: create in the KB, move through the state machine ──────────────────
        r = a.post(f"{TT}/items", json={"kind": "initiative", "title": f"Reliability {token}"})
        assert r.status_code == 201, r.text
        ini = r.json()
        r = a.post(f"{TT}/items", json={"kind": "epic", "title": "Webhook delivery", "parent_id": ini["id"]})
        assert r.status_code == 201, r.text
        epic = r.json()
        r = a.post(f"{TT}/items", json={"kind": "task", "title": "Retry budget config", "parent_id": epic["id"],
                                         "description": "Make the retry budget configurable."})
        assert r.status_code == 201, r.text
        task = r.json()
        _log(f"board created: {ini['title']} / {epic['title']} / {task['title']} (refs {task['external_ref']})")
        assert all(x["external_ref"].startswith("kb:") for x in (ini, epic, task))
        mv = lambda to, **kw: a.post(f"{TT}/items/{task['id']}/kb-transition", json={"to": to, **kw})  # noqa: E731
        assert mv("in_progress").status_code == 200
        refused = mv("complete")
        assert refused.status_code == 409 and "definition_of_done" in refused.text, refused.text
        r = mv("complete", dod="retry budget read from config; 5 attempts by default")
        assert r.status_code == 200 and r.json()["status"] == "complete", r.text
        board = {i["id"]: i for i in a.get(f"{TT}/items").json()["items"]}
        assert board[task["id"]]["status"] == "complete" and board[epic["id"]]["parent_id"] == ini["id"]
        tfile = next((kb / "kb" / "tasks").glob("T-0001-*.md")).read_text(encoding="utf-8")
        assert "status: done" in tfile
        _log(f"state machine: open -> in_progress -> (done refused without DoD: 409) -> done; board={board[task['id']]['status']}")

        # ── 3. concept + decision through the sanctioned writer ────────────────────────────────
        rc, c = _kb(kb, "new", "concept", "--title", f"{token} bounded retry method")
        assert rc == 0, c
        rc, d = _kb(kb, "new", "decision", "--title", f"{token} webhook retry budget",
                    "--link", f"formalized_as={c['id']}")
        assert rc == 0, d
        _log(f"kb new: {c['id']} {d['id']}")

        # ── 4. REAL gate runs (haiku) against the tenant KB, real path gate as hook ────────────
        work = tmp_path / "gate-cwd"
        work.mkdir()
        genv = {**_env(home, "_default")}
        kb_py = (kb / "scripts" / "kb.py").as_posix()
        verdicts = {}
        for gate, task_text in (
                ("adr_gate", f"We decided that webhook delivery retries 5 times with exponential backoff "
                             f"(the {token} retry budget) and made it configurable."),
                ("concept_gate", f"While fixing webhook delivery we again used the {token} bounded-retry way "
                                 f"of working (cap attempts, back off, surface the final failure).")):
            skill = _rendered_gate_skill(gate, home, "_default", monkeypatch)
            assert kb_py in skill and "/home/shumway" not in skill
            out = _run_gate(gate, skill, task_text, work, genv)
            assert out["rc"] == 0, out["stderr"]
            assert any(kb_py in b or str(kb / "scripts" / "kb.py") in b for b in out["bash"]), out["bash"]
            assert not any("path_gate:" in x for x in out["results"]), \
                [x for x in out["results"] if "path_gate:" in x]
            v = out["verdict"]
            assert v is not None, out["final"][-1500:]
            assert v.get("decision") in ("write", "amend", "skip"), v
            joined = " ".join(out["results"])
            assert (d["id"] if gate == "adr_gate" else c["id"]) in joined, "the gate never read the tenant KB"
            verdicts[gate] = v
        assert verdicts["adr_gate"].get("existing_id") == d["id"], verdicts
        assert verdicts["concept_gate"].get("existing_id") == c["id"], verdicts

        # ── 5. the console chat agent files a task into the tenant KB (REAL chat turn) ─────────
        ctoken = "OKAPI" + secrets.token_hex(3).upper()
        sid = a.post("/chat/sessions", json={"title": "kb task live"}).json()["session"]["sid"]
        ws_url = f"ws://127.0.0.1:{port_a}/v1/console/chat/sessions/{sid}/stream"
        # No path and no CLI hint: the console's own system prompt (_knowledge_base_block) must be
        # enough for the agent to find this tenant's KB and file the task the sanctioned way.
        prompt = (f"Lege bitte eine neue Aufgabe \"Rotate backup keys {ctoken}\" im Epic "
                  f"{epic['title'].split(' · ')[0]} an, Definition of Done: keys rotated. "
                  f"Antworte nur mit der neuen Task-ID.")
        events = asyncio.run(_chat_turn(ws_url, cookie, prompt, timeout=600))
        errors = [e for e in events if e.get("type") == "error"]
        assert not errors, errors
        reply = " ".join(str(e.get("text") or "") for e in events if e.get("type") == "result")
        _log(f"chat reply: {reply[:200]!r}")
        hits = [p for p in (kb / "kb" / "tasks").glob("T-*.md") if ctoken in p.read_text(encoding="utf-8")]
        assert len(hits) == 1, f"the chat agent did not file the task into {kb}: {reply[:500]}"
        new_id = hits[0].name.split("-")[0] + "-" + hits[0].name.split("-")[1]
        a.post(f"{TT}/kb/sync")
        assert any(ctoken in i["title"] for i in a.get(f"{TT}/items").json()["items"]), "not on the board"
        _log(f"chat agent filed {new_id} into the tenant KB; board shows it")

        # ── 6. kb check: blocking 0 ───────────────────────────────────────────────────────────
        rc, chk = _kb(kb, "check")
        _log(f"kb check: rc={rc} entities={chk.get('entities')} blocking={chk.get('blocking')} "
             f"counts={chk.get('counts')}")
        assert rc == 0 and chk["blocking"] == 0, chk
        g1 = {e["label"] for e in a.get("/plugins/corvin-knowledge/graph").json()["entities"]}
        assert {"I-01", "E-001", "T-0001", c["id"], d["id"]} <= g1, g1

        # ── 7. a second tenant: own host process, own KB, sees nothing of _default ─────────────
        port_b = _free_port()
        log_b = tmp_path / "console-acme.log"
        (home / "tenants" / "acme").mkdir(parents=True, exist_ok=True)
        proc_b = _start_console(home, port_b, log_b, "acme")
        api_b = f"http://127.0.0.1:{port_b}/v1/console"
        _wait(lambda: httpx.get(f"{api_b}/healthz", timeout=3).status_code == 200, "console B healthz", 240,
              proc_b, log_b)
        monkeypatch.setenv("CORVIN_HOME", str(home))
        monkeypatch.setenv("CORVIN_TENANT_ID", "acme")
        for p in (str(REPO / "core" / "console"), str(REPO / "corvin_operator" / "forge")):
            if p not in sys.path:
                sys.path.insert(0, p)
        from corvin_console import auth as session_auth  # noqa: PLC0415
        rec_b = session_auth.create_session(tenant_id="acme")
        b = httpx.Client(base_url=api_b, timeout=120, headers={
            "X-CSRF-Token": session_auth.derive_csrf_token(rec_b.csrf_secret, rec_b.sid)})
        b.cookies.set("corvin_console_sid", rec_b.sid)
        kb_b = home / "tenants" / "acme" / "knowledge"
        st_b = _wait(lambda: (lambda s: s if s.get("state") == "ok" and s.get("enabled") else None)(
            b.get(f"{TT}/kb/status").json()), "acme's own KB projected", 240, proc_b, log_b)
        assert (kb_b / "scripts" / "kb.py").is_file() and kb_b != kb
        b_items = b.get(f"{TT}/items").json()["items"]
        assert not any(token in i["title"] or ctoken in i["title"] for i in b_items), b_items
        assert b.post(f"{TT}/items/{task['id']}/kb-transition", json={"to": "open"}).status_code == 404
        g_b = b.get("/plugins/corvin-knowledge/graph").json()
        assert g_b.get("entities", []) == [], g_b
        assert b.get("/plugins/corvin-knowledge/config").json()["repo_path"] == str(kb_b)
        assert not list((kb_b / "concepts").glob("*.md")) if (kb_b / "concepts").exists() else True
        sys.path.insert(0, str(HOOK.parent))
        import path_gate  # noqa: PLC0415
        ok_own, _ = path_gate.check({"tool_name": "Bash", "tool_input": {
            "command": f"python {(kb_b / 'scripts' / 'kb.py').as_posix()} check"}})
        ok_other, why = path_gate.check({"tool_name": "Bash", "tool_input": {"command": f"python {kb_py} check"}})
        assert ok_own and not ok_other, why
        _log(f"tenant acme: own KB {kb_b} (state={st_b['state']}), 0 of _default's items/entities visible, "
             f"move of _default's card -> 404, path gate pins only acme's kb.py")
    finally:
        if proc_b is not None:
            _stop(proc_b)
        _stop(proc_a)
