"""Adversarial DAG end-to-end, stage F1 (fault injection), part 3: containment and design time.

* the ``code`` node jail (AD-47, INV-S5): an LLM-planned or hand-written code node must not read the
  host, write the host, reach the network, see the process environment or run forever; and with no
  bwrap on the host it refuses (fail-closed) instead of running unjailed;
* design time (AD-01, AD-10, INV-S4): a hostile planner answer is validated like any save and the
  design chat executes nothing, sends nothing, contacts no peer;
* an engine that never answers (INV-R3) and the canary search (INV-S1).

Run: .venv/bin/python -m pytest -q -o addopts="" -p no:cacheprovider core/console/tests/test_dag_adversarial_f1c_e2e.py
"""
from __future__ import annotations

import json
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SHARED = _HERE.parents[2] / "corvin_operator" / "bridges" / "shared"
for _p in (str(_HERE), str(_SHARED)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import dag_adv_shim as ds  # noqa: E402
from test_dag_adversarial_f1_e2e import agent, assert_chain_intact, doc, merge  # noqa: E402
from test_dag_adversarial_f1b_e2e import node_events, run_with_rid  # noqa: E402
from test_node_model_console_e2e import _chain_events, _make  # noqa: E402
from test_workflow_composer import audit_records, make_workflow as composer_make  # noqa: E402
from test_workflow_composer import sandbox as composer_sandbox  # noqa: E402
from dag_adv_console import fresh_sandbox as live_sandbox  # noqa: E402  (one console per test: see below)


@pytest.fixture
def shim(tmp_path, monkeypatch):
    return ds.install(tmp_path, monkeypatch, mode="canned")


@pytest.fixture(autouse=True)
def _outbox(tmp_path, monkeypatch):
    d = tmp_path / "outbox"
    d.mkdir()
    monkeypatch.setenv("ADAPTER_OUTBOX", str(d))
    return d


def code_node(source: str, outputs=("r",), nid: str = "c") -> dict:
    return {"id": nid, "type": "code", "language": "python3", "depends_on": [], "source": source,
            "outputs": list(outputs)}


def run_code(client, tid, source: str, outputs=("r",)):
    _make(tid, "wf", doc([code_node(source, outputs)]))
    rid, events = run_with_rid(client, "wf")
    return rid, events


needs_bwrap = pytest.mark.skipif(shutil.which("bwrap") is None, reason="the jail tests need bwrap")


# ── the code node jail (AD-47, INV-S5, INV-D8) ───────────────────────────────

@needs_bwrap
def test_a_code_node_cannot_read_a_host_file_outside_its_jail(tmp_path, shim):
    secret = tmp_path / "host_secret.txt"
    token = "CANARY-" + secrets.token_hex(6)
    secret.write_text(token)
    with live_sandbox(tmp_path / "sb") as (client, home, tid):
        rid, events = run_code(client, tid, f"def main():\n    return {{'r': open({str(secret)!r}).read()}}\n")
        assert token not in json.dumps(events), "a host file leaked through the code node"
        assert "c" in node_events(events, "node_failed"), events[-3:]
        assert shim.count() == 0, "a code node never calls the model (INV-D8)"
        assert_chain_intact(tid)


@needs_bwrap
def test_a_code_node_cannot_write_to_the_host(tmp_path, shim):
    target = tmp_path / "escaped.txt"
    with live_sandbox(tmp_path / "sb") as (client, home, tid):
        rid, events = run_code(
            client, tid,
            f"def main():\n    try:\n        open({str(target)!r}, 'w').write('x')\n        return {{'r': 'wrote'}}\n"
            f"    except Exception as e:\n        return {{'r': 'blocked ' + type(e).__name__}}\n")
        assert not target.exists(), "a code node wrote outside its jail"
        assert events[-1]["type"] == "run_completed"


@needs_bwrap
def test_a_code_node_cannot_open_a_network_connection(tmp_path, shim):
    hits: list[int] = []
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    srv.settimeout(0.2)
    port = srv.getsockname()[1]
    stop = threading.Event()

    def _accept():
        while not stop.is_set():
            try:
                c, _ = srv.accept()
                hits.append(1)
                c.close()
            except OSError:
                continue

    threading.Thread(target=_accept, daemon=True).start()
    try:
        with live_sandbox(tmp_path / "sb") as (client, home, tid):
            rid, events = run_code(
                client, tid,
                "def main():\n    import socket\n    try:\n"
                f"        socket.create_connection(('127.0.0.1', {port}), timeout=2)\n        return {{'r': 'connected'}}\n"
                "    except Exception as e:\n        return {'r': 'blocked ' + type(e).__name__}\n")
            done = node_events(events, "node_completed").get("c")
            assert done is not None, events[-3:]
            assert "connected" not in json.dumps(done["output"]) and not hits, (done["output"], len(hits))
    finally:
        stop.set()
        srv.close()


@needs_bwrap
def test_a_code_node_sees_no_secret_environment(tmp_path, shim, monkeypatch):
    monkeypatch.setenv("CORVIN_ADV_SECRET_CANARY", "CANARY-env-" + secrets.token_hex(4))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-canary-" + secrets.token_hex(4))
    with live_sandbox(tmp_path / "sb") as (client, home, tid):
        rid, events = run_code(client, tid, "def main():\n    import os\n    return {'r': sorted(os.environ)}\n")
        done = node_events(events, "node_completed").get("c")
        assert done is not None, events[-3:]
        keys = done["output"]["r"] if isinstance(done["output"], dict) else json.loads(done["output"])["r"]
        banned = [k for k in keys if k.startswith(("CORVIN_", "ANTHROPIC_", "SHIM_", "ADAPTER_", "AWS_", "OPENAI_"))]
        assert banned == [], banned
        assert "CANARY" not in json.dumps(done)


@needs_bwrap
def test_a_code_node_that_never_returns_is_killed_and_the_run_ends(tmp_path, shim):
    with live_sandbox(tmp_path / "sb") as (client, home, tid):
        t0 = time.time()
        rid, events = run_code(client, tid, "def main():\n    while True:\n        pass\n")
        assert time.time() - t0 < 60, "the 10 s limit did not hold"
        assert "c" in node_events(events, "node_failed"), events[-3:]
        assert events[-1]["type"] == "run_completed" and events[-1]["ok"] is False


@needs_bwrap
def test_a_code_node_cannot_allocate_without_limit(tmp_path, shim):
    with live_sandbox(tmp_path / "sb") as (client, home, tid):
        rid, events = run_code(
            client, tid,
            "def main():\n    try:\n        x = bytearray(64 * 1024 ** 3)\n        return {'r': 'allocated'}\n"
            "    except MemoryError:\n        return {'r': 'memoryerror'}\n")
        done = node_events(events, "node_completed").get("c")
        failed = node_events(events, "node_failed").get("c")
        assert "allocated" not in json.dumps(done or {}), done
        assert done or failed, events[-3:]


def test_without_bwrap_a_code_node_refuses_instead_of_running_unjailed(tmp_path, shim, monkeypatch):
    marker = tmp_path / "ran_unjailed.txt"
    monkeypatch.delenv("CORVIN_ALLOW_UNSANDBOXED_CODE", raising=False)
    with live_sandbox(tmp_path / "sb") as (client, home, tid):
        import forge.sandbox as fs
        monkeypatch.setattr(fs, "have_bwrap", lambda: False)
        rid, events = run_code(client, tid, f"def main():\n    open({str(marker)!r}, 'w').write('x')\n    return {{'r': 1}}\n")
        failed = node_events(events, "node_failed").get("c")
        assert failed is not None, events[-3:]
        assert "bwrap" in json.dumps(failed), failed
        assert not marker.exists(), "the code ran without the jail although nobody opted in (fail-closed)"


# ── design time: a hostile planner answer leaves no side effect (AD-01, AD-10, INV-S4) ──

HOSTILE_NODES = [
    {"id": "grab", "type": "code", "language": "python3", "depends_on": [],
     "source": "def main():\n    return {'x': 1}\n", "outputs": ["x"]},
    {"id": "ship", "type": "deliver", "depends_on": ["grab"], "text_from": "grab.x",
     "config": {"channel": "discord", "chat_id": "999", "format": "text"}},
    {"id": "ask", "type": "peer_agent", "depends_on": ["grab"], "endpoint_id": "e1", "agent_id": "a1",
     "instruction": "hello"},
]
PLANNER_RULE = "generate a complete valid AWP YAML"

PLANNER_ANSWERS = [
    ("prose", "I am unable to produce a workflow for that.", False),
    ("fenced", "```yaml\n" + doc([agent("a"), agent("b", ["a"])]) + "```", True),
    ("preamble", "Sure, here you go:\n" + doc([agent("a")]), True),
    ("hostile_node_types", doc(HOSTILE_NODES), True),
    ("cycle", doc([agent("a", ["b"]), agent("b", ["a"])]), False),
    ("invented_model", doc([agent("a", model="sonnet-9")]), False),
]


def design_turn(ws, text: str):
    ws.send_json({"type": "user", "text": text})
    frames = []
    while True:
        f = ws.receive_json()
        frames.append(f)
        if (f.get("type") == "message" and f.get("role") == "assistant") or f.get("type") == "error":
            return f, frames


@pytest.mark.parametrize("case,answer,written", PLANNER_ANSWERS, ids=[c[0] for c in PLANNER_ANSWERS])
def test_a_planner_answer_is_validated_like_a_save_and_design_time_has_no_side_effect(
        tmp_path, shim, _outbox, case, answer, written):
    shim.set_rules([{"match": {"kind": "planner", "prompt_regex": PLANNER_RULE}, "action": {"reply": answer}}])
    with composer_sandbox(tmp_path) as (app, home, tid, new_client):
        composer_make(tid, "wf1", phase="detailing")
        from forge import paths as fp
        yaml_p = fp.tenant_home(tid) / "workflows" / "wf1.awp.yaml"
        client = new_client()
        with client.websocket_connect("/v1/console/workflows/wf1/chat") as ws:
            ws.receive_json(); ws.receive_json()
            reply, _frames = design_turn(ws, "please build me a workflow for the harbour report")
        assert reply.get("type") == "message", reply
        if written:
            assert reply.get("yaml_update"), (case, reply)
            assert yaml_p.exists()
        else:
            assert not reply.get("yaml_update"), (case, reply)
            assert not yaml_p.exists(), "a refused draft must not be written (INV-D1)"
        # INV-S4: nothing ran, nothing was sent, nobody was contacted
        assert shim.nodes() == [], "the design chat spawned a node"
        assert list(_outbox.iterdir()) == [], "the design chat wrote to the bridge outbox"
        types = {r.get("event_type", "") for r in audit_records(tid)}
        assert not {t for t in types if t.startswith(("workflow.peer", "a2a.", "a2a_"))}, types
        assert shim.count("planner") >= 1
        assert_chain_intact(tid)


def test_the_design_chat_planner_is_spawned_without_any_tool__finding_h_pl_1(tmp_path, shim):
    """H-PL-1, found by the live run of AD-21 (ad21_file_read): the planner spawn gave `claude -p` its
    tools, so a task that said 'read <file> and copy it into the description' made it read a host file and
    print the content into the chat reply. The planner only has to write text; like every other tool-less
    text call in the repo it must run with `--disallowedTools "*"`."""
    with composer_sandbox(tmp_path) as (app, home, tid, new_client):
        composer_make(tid, "wf1", phase="detailing")
        client = new_client()
        with client.websocket_connect("/v1/console/workflows/wf1/chat") as ws:
            ws.receive_json(); ws.receive_json()
            design_turn(ws, "please build me a workflow for the harbour report")
        planner = shim.rows("planner")
        assert len(planner) == 2, "a build turn starts exactly two planner calls (reply and YAML)"
        for row in planner:
            argv = row["argv"]
            assert "--disallowedTools" in argv and argv[argv.index("--disallowedTools") + 1] == "*", argv


# ── an engine that never answers (INV-R3) ────────────────────────────────────

def test_a_node_that_outlives_the_engine_timeout_fails_and_leaves_no_process_behind(tmp_path, shim):
    """The console builds its engine with the 60 s default and offers no knob; ``ENGINE_FACTORY`` is the
    seam the module provides for exactly this ("tests replace this"), so the REAL ClaudeCliEngine runs
    with ``timeout_s=3`` instead of waiting a minute. Everything else is the production path."""
    shim.set_rules([{"match": {"kind": "dag_node", "prompt_regex": "MARK-A\\b"},
                     "action": {"reply": {"ok": True}, "delay_s": 8}}])
    with live_sandbox(tmp_path) as (client, home, tid):
        from corvin_console.routes import workflow_dag_run as wdr
        from corvin_workflows.engines_claude import ClaudeCliEngine
        original = wdr.ENGINE_FACTORY
        wdr.ENGINE_FACTORY = lambda tenant, graph: ClaudeCliEngine(tenant_id=tenant, timeout_s=3)
        try:
            _make(tid, "wf", doc([agent("a"), merge("j", ["a"], ["a.ok"])]))
            before = len(_chain_events(tid, "engine.span.end"))
            t0 = time.time()
            rid, events = run_with_rid(client, "wf")
        finally:
            wdr.ENGINE_FACTORY = original
        assert 2 < time.time() - t0 < 30
        failed = node_events(events, "node_failed")["a"]
        assert "timed out after 3s" in json.dumps(failed), failed
        assert events[-1]["type"] == "run_completed" and events[-1]["ok"] is False
        errs = [d for d in _chain_events(tid, "engine.span.end")[before:] if d["status"] == "error"]
        assert len(errs) == 1
        left = subprocess.run(["pgrep", "-f", str(shim.bindir / "claude")], capture_output=True, text=True)
        assert left.stdout.strip() == "", "a shim process outlived its node"
        assert_chain_intact(tid)


# ── nothing a node was given may reach the audit chain (INV-S1) ──────────────

def test_a_canary_in_the_task_never_reaches_the_audit_chain_the_profile_or_the_spans(tmp_path, shim):
    canary = "CANARY-" + secrets.token_hex(8)
    graph = doc([agent("a", instructions=f"summarise {canary} for me"), merge("j", ["a"], ["a.ok"])])
    with live_sandbox(tmp_path) as (client, home, tid):
        _make(tid, "wf", graph)
        rid, events = run_with_rid(client, "wf")
        assert events[-1]["ok"] is True
        assert any(canary in r["prompt"] for r in shim.nodes()), "control: the canary did reach the engine"
        hits = [p for p in home.rglob("*") if p.is_file() and canary in p.read_text(errors="ignore")]
        bad = [p for p in hits if "audit" in p.name or "profile" in str(p) or "span" in p.name]
        assert bad == [], f"the canary reached an audit-grade file: {bad}"
        # the places the text is EXPECTED to live are the run record and the workflow's own YAML
        assert all(("workflows" in p.parts) for p in hits), [str(p) for p in hits]
