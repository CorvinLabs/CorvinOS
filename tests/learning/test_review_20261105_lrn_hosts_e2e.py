"""Adversarial review 2026-11-05, area LRN (learning hosts, stores and gates).

Every case drives the real boundary:

* LRN-1  the console web-chat worker's skill_forge MCP server (built by the console's own
         ``chat_runtime._persona_mcp_config`` through the cowork resolver, started as a
         subprocess with exactly that command/args/env) treated the worker as a wildcard caller:
         it could force-promote session->user and its self-chosen run ids graded organically.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
for _p in (_REPO, _REPO / "core" / "console", _REPO / "corvin_operator" / "skill-forge",
           _REPO / "corvin_operator" / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


# --------------------------------------------------------------------------- LRN-1

class _Stdio:
    def __init__(self, argv: list[str], env: dict[str, str]) -> None:
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, bufsize=1, env=env,
                                     encoding="utf-8", errors="replace")
        self._buf: list[dict] = []
        self._lock = threading.Lock()
        self._n = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.proc.stdout:  # type: ignore[union-attr]
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            with self._lock:
                self._buf.append(msg)

    def request(self, method: str, params: dict | None = None, timeout: float = 20.0) -> dict:
        self._n += 1
        msg = {"jsonrpc": "2.0", "id": self._n, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")  # type: ignore[union-attr]
        self.proc.stdin.flush()  # type: ignore[union-attr]
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                for i, m in enumerate(self._buf):
                    if m.get("id") == self._n:
                        return self._buf.pop(i)
            time.sleep(0.01)
        raise TimeoutError(method)

    def call(self, tool: str, args: dict) -> dict:
        return self.request("tools/call", {"name": tool, "arguments": args})["result"]

    def close(self) -> None:
        try:
            self.proc.stdin.close()  # type: ignore[union-attr]
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 - teardown
            self.proc.kill()


def _console_skill_forge_server(tmp_path: Path, monkeypatch) -> dict:
    """The skill_forge entry of the --mcp-config the console hands its worker."""
    from corvin_console import chat_runtime

    if chat_runtime._cowork is None:
        pytest.skip("cowork resolver not importable in this layout")
    monkeypatch.setattr(chat_runtime._cowork, "MCP_CACHE_DIR", tmp_path / "mcp-cache")
    path = chat_runtime._persona_mcp_config("_default", workdir=tmp_path / "wd")
    assert path, "the console attached no MCP config"
    servers = json.loads(Path(path).read_text(encoding="utf-8"))["mcpServers"]
    assert "skill_forge" in servers, sorted(servers)
    return servers["skill_forge"]


def test_lrn1_console_worker_is_not_a_wildcard_skill_forge_caller(tmp_path, monkeypatch):
    srv = _console_skill_forge_server(tmp_path, monkeypatch)
    home = tmp_path / "home"
    home.mkdir()
    # Seed a session-scope assistant.* skill in the sandbox (the worker's own namespace).
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_PLUGIN_SLOT_DIR", str(tmp_path / "slot"))
    monkeypatch.setenv("CORVIN_TASK_ID", f"lrn1-{tmp_path.name}")
    monkeypatch.delenv("CORVIN_CALLER_PERSONA", raising=False)
    monkeypatch.delenv("SKILL_FORGE_PERSONA", raising=False)
    from corvin_operator.license import capability_api
    monkeypatch.setattr(capability_api, "active_tier", lambda **_k: "member")
    from skill_forge.multi_registry import MultiSkillRegistry
    MultiSkillRegistry(tenant_id="_default").create(
        scope="session", name="assistant.lrn1_skill", type="domain", description="d",
        body_md="# assistant.lrn1_skill\n\nA checklist body long enough for the linter to accept "
                "it as a real skill rather than a stub.\n",
        claim={"predicted_delta_loss": 0.1})

    # Spawn exactly what the console's claude subprocess would spawn: the inherited env
    # (no caller tag — the console never exports one) overlaid with the server's own env.
    env = {k: v for k, v in os.environ.items()
           if k not in ("CORVIN_CALLER_PERSONA", "SKILL_FORGE_PERSONA")}
    env.update(srv.get("env") or {})
    client = _Stdio([srv["command"], *srv.get("args", [])], env)
    try:
        client.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                      "clientInfo": {"name": "lrn1", "version": "0"}})
        r = client.call("skill_promote", {"name": "assistant.lrn1_skill", "to": "user",
                                          "force": True})
        assert r.get("isError") is True, f"console worker force-promoted session->user: {r}"
        assert "scope-gate" in r["content"][0]["text"]
        for n in range(3):
            r = client.call("skill_grade", {"name": "assistant.lrn1_skill",
                                            "run_id": f"self-{n}", "score": 1.0})
            assert r.get("isError") is False, r
        assert r["structuredContent"]["data"]["mean_score"] <= 0.3 + 1e-9, r
    finally:
        client.close()
    chain = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    recs = [json.loads(x) for x in chain.read_text(encoding="utf-8").splitlines() if x.strip()]
    grades = [e for e in recs if e.get("event_type") == "skill.grade"
              and str((e.get("details") or {}).get("run_id", "")).startswith("self-")]
    assert len(grades) == 3 and all(g["details"]["organic"] is False for g in grades), grades
    assert all(g["details"].get("caller_persona") == "assistant" for g in grades), grades
    assert not [e for e in recs if e.get("event_type") == "skill.promote"]


# --------------------------------------------------------------------------- LRN-3

def test_lrn3_a_process_cannot_learn_for_a_foreign_tenant(tmp_path, monkeypatch):
    """LRN-3 asked for one learning host per tenant directory in every process. The audit
    chokepoint refuses any record of a tenant other than the process tenant (ADR-0562 /
    F-A6: AuditTenantMismatch), and every brake and config write is audit-first, so in a
    ``_default`` process a host for ``acme`` could neither freeze nor audit anything, and the
    console cannot write ``acme``'s learned config in the first place. One host per process
    tenant (``CORVIN_TENANT_ID``) is therefore complete; this pins the premise."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    monkeypatch.delenv("CORVIN_TENANT_ID", raising=False)
    from core.deployment import audit_sink
    from core.background import learning_host  # noqa: F401 - registers the host's events
    from core.skills.os_skills import skill_adapter as sa

    with pytest.raises(audit_sink.AuditWriteFailed):
        audit_sink.emit("learning.sources_evicted", {"evicted_count": 1}, tenant_id="acme")
    with pytest.raises(audit_sink.AuditWriteFailed):
        sa.freeze_learning("os.delegation_router", "acme", reason="skill.config_oscillation")
    with pytest.raises(RuntimeError):
        sa.SkillAdapter("os.delegation_router", "acme").apply_config_delta(
            {"confidence_threshold": -0.01}, confidence_delta=0.1)
    assert not sa.skill_config_path("os.delegation_router", "acme").exists()
    # positive control: the process tenant's brake works
    assert sa.freeze_learning("os.delegation_router", "_default",
                              reason="skill.config_oscillation") is True


# --------------------------------------------------------------------------- LRN-4

def test_lrn4_model_learner_is_credited_to_the_tasks_own_tenant(tmp_path, monkeypatch):
    from corvin_core.task_manager import TaskManager
    import core.learning.model_selection_learning_listener as msl

    seen: list[str] = []

    class _Listener:
        def process_outcome(self, outcome, tenant_id):
            seen.append(tenant_id)
            return True

    monkeypatch.setattr(msl, "get_listener", lambda: _Listener())
    tm = TaskManager(tmp_path / "tasks")
    tid = tm.create_task("web:x", "do it", check_quota=False, tenant_id="acme",
                         model_selected="claude-haiku-4-5", task_type="code_gen")
    # how chat_runtime / task_worker_pool close a task: no tenant_id argument
    tm.record_event(tid, {"event": "task.completed", "exit_code": 0})
    assert seen == ["acme"], f"learner credited tenant {seen}"


def test_lrn4_a_task_without_a_tenant_is_dropped_not_guessed(tmp_path, monkeypatch):
    from corvin_core.task_manager import TaskManager
    import core.learning.model_selection_learning_listener as msl

    seen: list[str] = []

    class _Listener:
        def process_outcome(self, outcome, tenant_id):
            seen.append(tenant_id)
            return True

    monkeypatch.setattr(msl, "get_listener", lambda: _Listener())
    tm = TaskManager(tmp_path / "tasks")
    tid = tm.create_task("web:x", "do it", check_quota=False, tenant_id="acme",
                         model_selected="claude-haiku-4-5", task_type="code_gen")
    task = tm.get_task(tid)
    task.input.pop("tenant_id")
    TaskManager._update_model_selection_confidence(task, {"event": "task.completed"})
    assert seen == []


# --------------------------------------------------------------------------- LRN-10

def _run_path_gate_hook(home: Path, payload: dict) -> int:
    """The REAL PreToolUse hook as Claude Code runs it: tool call JSON on stdin,
    exit 0 = allow, 2 = deny."""
    hook = _REPO / "corvin_operator" / "voice" / "hooks" / "path_gate.py"
    env = dict(os.environ, CORVIN_HOME=str(home), CORVIN_TENANT_ID="_default")
    proc = subprocess.run([sys.executable, str(hook)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, timeout=60)
    return proc.returncode


@pytest.mark.parametrize("leaf", ["ce_stage_grades.json", "usage_epoch.json",
                                  "model_confidence_stats.json"])
def test_lrn10_tenant_global_learning_state_is_write_protected(tmp_path, leaf):
    home = tmp_path / "corvin_home"
    target = home / "tenants" / "_default" / "global" / leaf
    target.parent.mkdir(parents=True)
    target.write_text("{}\n")
    for payload in (
        {"tool_name": "Write", "tool_input": {"file_path": str(target), "content": "{}"}},
        {"tool_name": "Bash", "tool_input": {"command": f"rm -f {target}"}},
        {"tool_name": "Bash", "tool_input": {"command": f"echo '{{}}' > {target}"}},
    ):
        assert _run_path_gate_hook(home, payload) == 2, payload


def test_lrn10_positive_controls_stay_writable(tmp_path):
    home = tmp_path / "corvin_home"
    for target in (home / "tenants" / "_default" / "global" / "notes.txt",
                   home / "tenants" / "_default" / "sessions" / "w" / "usage_epoch.json"):
        payload = {"tool_name": "Write", "tool_input": {"file_path": str(target), "content": "x"}}
        assert _run_path_gate_hook(home, payload) == 0, target


# --------------------------------------------------------------------------- LRN-11

def _grades(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    from corvin_operator.context_engineering.stages import grades

    p = tmp_path / "ce_stage_grades.json"
    monkeypatch.setattr(grades, "_store_path", lambda t: p)
    return grades, p


def test_lrn11_concurrent_grades_are_not_lost(tmp_path, monkeypatch):
    grades, _ = _grades(tmp_path, monkeypatch)
    n, workers = 50, 4
    errors: list[BaseException] = []

    def run(i):
        try:
            for _ in range(n):
                grades.grade_stage("t1", f"stage_{i % 2}", 1.0, grader="operator")
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    ts = [threading.Thread(target=run, args=(i,)) for i in range(workers)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors, errors
    total = sum(grades.get_grade("t1", f"stage_{k}")["n_grades"] for k in (0, 1))
    assert total == n * workers, f"{n * workers - total} of {n * workers} grades lost"


def test_lrn11_unreadable_store_fails_closed_and_keeps_prior_grades(tmp_path, monkeypatch):
    grades, p = _grades(tmp_path, monkeypatch)
    grades.grade_stage("t1", "s", 1.0, grader="operator")
    grades.grade_stage("t1", "s", 1.0, grader="operator")
    torn = p.read_text()[:-1]
    p.write_text(torn, encoding="utf-8")  # one torn byte (disk full, foreign writer)
    with pytest.raises(grades.GradeStoreUnreadable):
        grades.grade_stage("t1", "s", 0.0, grader="__loop__")
    assert p.read_text() == torn, "the store was rewritten"
    assert grades.is_default_eligible("t1", "s", builtin_ids=()) is False
    grades.record_turn_outcome("t1", ["s"], True)  # the loop logs, never raises, never rewrites
    assert p.read_text() == torn


# --------------------------------------------------------------------------- LRN-14

def test_lrn14_console_turn_records_token_metrics_in_the_tenant_home(tmp_path, monkeypatch):
    """A real console turn (stream_turn against a fake claude subprocess) writes its usage
    to <tenant_home>/learning/token_metrics.db - never the shared file under the user's
    home - and the L36 handler erases it."""
    for extra in (_REPO / "corvin_operator" / "bridges" / "shared" / "tests",
                  _REPO / "corvin_operator" / "bridges" / "shared",
                  _REPO / "core" / "console" / "tests"):
        if str(extra) not in sys.path:
            sys.path.insert(0, str(extra))
    import test_chat_turn_usage_stream_e2e as tu  # the turn_usage harness

    tu._sandbox(tmp_path, monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path / "userhome"))
    import core.learning.token_measurement_hook as hook_mod
    monkeypatch.setattr(hook_mod, "_hook", None, raising=False)
    monkeypatch.setattr(hook_mod, "_autoinit_attempted", False, raising=False)
    tu._replay(tmp_path, monkeypatch, tu._stream(init_model=tu.CONFIRMED, usage=tu.USAGE))
    from corvin_console import chat_runtime as cr

    sess = cr.create_session("_default")

    async def _collect():
        evs = [ev async for ev in cr.stream_turn(sess, "say done")]
        await asyncio.sleep(0.5)  # the DB insert is a fire-and-forget task on this loop
        return evs

    events = asyncio.run(_collect())
    assert [e for e in events if e.get("type") == "turn_usage"], events
    home = Path(os.environ["CORVIN_HOME"])
    db = home / "tenants" / "_default" / "learning" / "token_metrics.db"
    assert db.is_file(), "the console turn recorded no token metrics"
    assert not list((tmp_path / "userhome").rglob("token_metrics.db"))
    import sqlite3
    import contextlib
    with contextlib.closing(sqlite3.connect(db)) as conn:
        rows = conn.execute(
            "SELECT session_id, input_tokens, output_tokens FROM token_metrics").fetchall()
    assert rows == [(sess.sid, tu.USAGE["input_tokens"], tu.USAGE["output_tokens"])], rows

    from erasure_handlers import TokenMetricsHandler, real_handler_chain  # type: ignore
    assert any(isinstance(h, TokenMetricsHandler) for h in real_handler_chain("_default"))
    res = TokenMetricsHandler(tenant_id="_default").purge(sess.sid, "req-lrn14")
    assert res.count == 1, res
    with contextlib.closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM token_metrics").fetchone() == (0,)


# --------------------------------------------------------------------------- LRN-18

def test_lrn18_learning_integration_creates_no_tenantless_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    from core.learning.integration import LearningIntegration

    integ = LearningIntegration(tenant_id="acme")
    assert integ.pattern_clusterer.base_dir == (
        tmp_path / "tenants" / "acme" / "learning" / "discoveries")
    assert not (tmp_path / "learning").exists(), "a tenant-less <home>/learning appeared"
