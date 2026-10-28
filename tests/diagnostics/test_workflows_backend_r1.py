"""Adversarial review round 1 (2026-10-20/21) — workflow backend regression tests.

Each test asserts the CORRECT behaviour; the ids (WFB-R1-xx) are the review's finding ids. Written
as repro tests, they became a gate on 2026-10-21 once every finding was fixed or settled against a
documented contract (09, 11, 14, 21 were aligned with the contracts that superseded them).

Every test runs in a sandbox: CORVIN_HOME, XDG_CONFIG_HOME and CORVIN_AUDIT_ANCHOR_KEY are
absolute tmp paths and FORGE_ROOT is unset. No live install is touched.

Run:  .venv/bin/python -m pytest tests/diagnostics/test_workflows_backend_r1.py -q
"""

from __future__ import annotations

import asyncio
import inspect
import io
import json
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_OPERATOR = _REPO / "corvin_operator"
_CONSOLE = _REPO / "core" / "console"
_WORKFLOWS = _REPO / "core" / "workflows"
_SHARED = _OPERATOR / "bridges" / "shared"
for _p in (str(_OPERATOR), str(_OPERATOR / "license"), str(_OPERATOR / "forge"),
           str(_CONSOLE), str(_WORKFLOWS), str(_SHARED)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    home = tmp_path / "corvin_home"
    (home / "tenants" / "_default" / "global").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return home


def _workflows_home(home: Path, tenant: str = "_default") -> Path:
    d = home / "tenants" / tenant / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_console_workflows():
    """Import the console route module against the sandboxed env (fresh each call)."""
    from corvin_console.routes import workflows as wf  # noqa: PLC0415
    return wf


# ── Import / schema ──────────────────────────────────────────────────────────

def test_wfb_r1_01_awpkg_import_bounds_decompressed_yaml(sandbox):
    """WFB-R1-01: a .awpkg whose workflow YAML decompresses far beyond the 512 KiB upload cap
    must be refused before it is fully materialised. Today `zf.read()` decompresses it first."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from corvin_console import auth as _auth
    from corvin_console.app import router

    rec = _auth.create_session(tenant_id="_default", token_fingerprint="fp-r1")
    csrf = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)
    app = FastAPI()
    app.include_router(router, prefix="/v1/console")
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("corvin_console_sid", rec.sid)
    client.headers.update({"X-CSRF-Token": csrf})

    member = 16 * 1024 * 1024  # a valid-YAML comment of 16 MiB compresses to ~16 KB
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("workflows/bomb.awp.yaml", b"# " + b"x" * (member - 2))
    raw = buf.getvalue()
    ratio = member / max(len(raw), 1)

    resp = client.post("/v1/console/workflows/import",
                       files={"file": ("bomb.awpkg", raw, "application/zip")})
    assert resp.status_code == 413, (
        f"expected 413 for a {member}-byte YAML member (zip ratio {ratio:.0f}:1, upload {len(raw)} B), "
        f"got {resp.status_code}: the member was decompressed in full before any size check"
    )


def test_wfb_r1_02_import_registers_code_without_consent_or_audit(sandbox):
    """WFB-R1-02: _register_tools_from_zip writes bundled Python into the tenant forge tools dir
    and overwrites an existing tool of the same name, with no audit event and no review gate."""
    wf = _load_console_workflows()
    tools_dir = sandbox / "tenants" / "_default" / "global" / "forge" / "tools"
    tools_dir.mkdir(parents=True)
    (tools_dir / "helper.py").write_text("def main():\n    return {'ok': 'original'}\n")
    bundle = {"name": "helper", "code": "import os\n# evil\n", "description": "x"}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("tools/helper.json", json.dumps(bundle))
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        wf._register_tools_from_zip(zf, "_default")
    text = (tools_dir / "helper.py").read_text()
    assert text == "def main():\n    return {'ok': 'original'}\n", (
        "an imported bundle silently replaced an existing tenant tool implementation"
    )


# ── Tenant isolation ─────────────────────────────────────────────────────────

def test_wfb_r1_03_skill_content_is_tenant_scoped(sandbox):
    """WFB-R1-03: skill bodies are read from the hard-coded `_default` tenant, not the run's tenant.
    The helper has no tenant parameter at all."""
    wf = _load_console_workflows()
    params = inspect.signature(wf._read_skill_content).parameters
    assert "tenant_id" in params, (
        f"_read_skill_content({', '.join(params)}) cannot know the run's tenant; it reads "
        "tenant `_default` for every run"
    )


def test_wfb_r1_04_resolve_skill_stays_inside_skill_dir(sandbox):
    """WFB-R1-04: _resolve_skill joins the workflow-supplied skill name into a path unvalidated.
    A traversal name reads a SKILL.md outside the tenant's skill store (the export route embeds it)."""
    wf = _load_console_workflows()
    (sandbox / "tenants" / "_default" / "global" / "skill-forge" / "skills").mkdir(parents=True)
    outside = sandbox / "escape"  # ../×5 from skills/ lands on CORVIN_HOME
    outside.mkdir(parents=True)
    (outside / "SKILL.md").write_text("SECRET-OUTSIDE-SKILL")
    got = wf._resolve_skill("_default", "../../../../../escape")
    assert got is None, f"traversal skill name escaped the skill store and returned {len(got)} bytes"


def test_wfb_r1_05_run_routes_do_not_validate_rid(sandbox):
    """WFB-R1-05 (Windows-relevant, unverified on Linux): get/delete run routes build paths from
    `rid` without a check. A backslash traversal segment reaches the file system on Windows."""
    wf = _load_console_workflows()
    src = inspect.getsource(wf.delete_run) + inspect.getsource(wf.get_run)
    assert "_validate_rid" in src or "_RID_RE" in src or "_is_run_id" in src, (
        "delete_run/get_run pass `rid` into _run_meta_path/_run_log_path with no shape check"
    )


# ── Compliance gates ─────────────────────────────────────────────────────────

def test_wfb_r1_06_compliance_zone_fails_closed_on_corrupt_tenant_yaml(sandbox):
    """WFB-R1-06: a forbid_engines policy in tenant.corvin.yaml is honoured while the file parses,
    but a corrupt file silently yields {} (no policy) and the run is allowed."""
    wf = _load_console_workflows()
    cfg = sandbox / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
    cfg.write_text("spec:\n  data_residency:\n    forbid_engines: [claude-code]\n")
    assert wf._check_compliance_zone("_default", "wid", "rid", "fp") is not None
    cfg.write_text("spec:\n  data_residency:\n    forbid_engines: [claude-code\n")  # broken YAML
    assert wf._check_compliance_zone("_default", "wid", "rid", "fp") is not None, (
        "a corrupt tenant.corvin.yaml removed the forbid_engines policy instead of refusing the run"
    )


def test_wfb_r1_07_refused_spawn_in_delegation_loop_fails_the_node(sandbox, monkeypatch):
    """WFB-R1-07: an L44/L34 refusal inside delegation_loop is RETURNED as the node output, so
    the node completes successfully and the run can report ok=True."""
    wf = _load_console_workflows()
    monkeypatch.setattr(wf._spawn_gates, "check_console_spawn_or_refusal",
                        lambda *a, **k: "REFUSED-BY-GATE")
    node = {"id": "dl", "instructions": "do it", "config": {"budget": {"max_loops": 1}}}
    with pytest.raises(RuntimeError):
        wf._run_delegation_loop_node(node, {}, None, "_default", "dl", "wid")


def test_wfb_r1_08_failed_claude_call_is_not_a_successful_node(sandbox, monkeypatch):
    """WFB-R1-08: a timeout / CLI-missing / non-zero exit from `claude -p` is returned as a plain
    string. _stream_run then emits node_completed and the downstream nodes consume the error text."""
    wf = _load_console_workflows()

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=180)

    monkeypatch.setattr(wf.subprocess, "run", boom)
    with pytest.raises(Exception):
        wf._run_node_claude("hello", None)


def test_wfb_r1_09_dag_engine_applies_the_same_spawn_gate(sandbox, monkeypatch):
    """WFB-R1-09: the DAG engine (`corvin-flow --engine claude`, the console resume route and the
    scheduler) spawns `claude -p` without any pre-spawn gate. A spawn the console gate refuses
    must be refused here too."""
    import spawn_gates  # the bridge-shared gate module the DAG engine calls (ADR-2271)
    from corvin_workflows.engines_claude import ClaudeCliEngine, ClaudeEngineError
    from corvin_workflows.engines import EngineCall

    monkeypatch.setattr(spawn_gates, "check_l44", lambda *a, **k: "REFUSED")   # the engine's first gate
    monkeypatch.setattr("corvin_workflows.engines_claude.shutil.which", lambda _n: "/bin/claude")
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout='{"ok": true}', stderr="")

    monkeypatch.setattr("corvin_workflows.engines_claude.subprocess.run", fake_run)
    eng = ClaudeCliEngine()
    with pytest.raises(ClaudeEngineError):
        eng.spawn(EngineCall(agent="a", instructions="refused text"))
    assert calls == [], "the DAG engine spawned claude although the console gate refused it"


def test_wfb_r1_10_stream_gate_covers_upstream_context(sandbox, monkeypatch):
    """WFB-R1-10: the console agent path gates only the node's own `instructions`. Upstream node
    output, workflow inputs and skill bodies are concatenated into the spawned prompt un-gated."""
    wf = _load_console_workflows()
    seen: list[str] = []
    monkeypatch.setattr(wf._spawn_gates, "check_console_spawn_or_refusal",
                        lambda text, **k: seen.append(text) or None)
    replies = iter(["REPRO-UPSTREAM-MARKER", "done"])
    prompts: list[str] = []

    def fake_claude(prompt, mcp_config=None, **_kw):  # ADR-2308: model= / meta= keywords
        prompts.append(prompt)
        return next(replies)

    monkeypatch.setattr(wf, "_run_node_claude", fake_claude)
    monkeypatch.setattr(wf, "_AWP_OK", False)
    yaml_text = (
        "awp: '1.0.0'\nworkflow:\n  name: r1\n"
        "orchestration:\n  graph:\n"
        "    - id: a\n      type: agent\n      instructions: benign step one\n"
        "    - id: b\n      type: agent\n      instructions: benign step two\n      depends_on: [a]\n"
    )

    async def drive():
        out = []
        async for chunk in wf._stream_run("_default", "fp", "r1", "rid1", yaml_text, {}, False):
            out.append(chunk)
        return out

    asyncio.run(drive())
    assert len(prompts) == 2 and "REPRO-UPSTREAM-MARKER" in prompts[1]
    assert any("REPRO-UPSTREAM-MARKER" in t for t in seen), (
        "the spawn gate never saw the upstream output that was put into the second prompt"
    )


def test_wfb_r1_11_gate_hook_defect_is_not_fail_open(sandbox, monkeypatch):
    """WFB-R1-11, settled 2026-10-21 against the documented contract (ADR-0251, routes/workflows.py
    `workflow.workflow_gate`): a HOOK that raises or answers with a wrong type produces no decision and
    the run is denied (`ExtensionPointDenied`); a defect in the BUS itself is deliberately not a
    denial (an unrelated bug must not take workflows down platform-wide). Pins the first half."""
    from corvin_plugins import extension_points as ep
    wf = _load_console_workflows()

    def broken_invoke(*a, **k):
        raise ep.ExtensionPointDenied("workflow.workflow_gate", "hook raised")

    monkeypatch.setattr(ep, "invoke", broken_invoke)
    monkeypatch.setattr(wf, "_run_node_claude", lambda *a, **k: "should-not-run")
    monkeypatch.setattr(wf, "_AWP_OK", False)
    yaml_text = ("awp: '1.0.0'\nworkflow:\n  name: r1g\norchestration:\n  graph:\n"
                 "    - id: a\n      type: agent\n      instructions: step\n")

    async def drive():
        out = []
        async for chunk in wf._stream_run("_default", "fp", "r1g", "rid2", yaml_text, {}, False):
            out.append(chunk)
        return "".join(out)

    text = asyncio.run(drive())
    mods = sorted(k for k in sys.modules if k.endswith("extension_points"))
    assert "node_completed" not in text, f"a failed workflow_gate hook let the run spawn a node (modules {mods}): {text[:200]!r}"


# ── Erasure (ADR-2270) ───────────────────────────────────────────────────────

def test_wfb_r1_12_erasure_removes_awpkg_sidecar(sandbox):
    """WFB-R1-12: the .awpkg sidecar holds the full workflow YAML. The Art. 17 handler (and
    delete_workflow) remove .awp.yaml and .chat.jsonl but never the sidecar."""
    sys.path.insert(0, str(_SHARED))
    from erasure_handlers import WorkflowChatHandler  # noqa: PLC0415

    wd = _workflows_home(sandbox)
    subject = "subj-r1"
    (wd / "w1.meta.json").write_text(json.dumps({"id": "w1", "owner": subject}))
    (wd / "w1.awp.yaml").write_text("awp: '1.0'\n# personal text\n")
    (wd / "w1.chat.jsonl").write_text("{}\n")
    (wd / "w1.awpkg").write_bytes(b"PK-sidecar-with-yaml")
    WorkflowChatHandler(tenant_id="_default").purge(subject, "er-r1")
    assert not (wd / "w1.awpkg").exists(), "the .awpkg sidecar with the workflow YAML survived erasure"


# ── Scheduler ────────────────────────────────────────────────────────────────

def test_wfb_r1_13_schedule_timezone_is_honoured(sandbox):
    """WFB-R1-13: set_schedule stores `timezone` in meta only. The scheduler has no timezone input,
    so cron fires in host-local time whatever the operator chose."""
    import scheduler
    assert "timezone" in inspect.signature(scheduler.add_task).parameters, (
        "scheduler.add_task has no timezone parameter; the console's timezone is silently ignored"
    )


def test_wfb_r1_14_scheduled_workflow_uses_real_engine(sandbox, monkeypatch):
    """WFB-R1-14: scheduled workflow runs call `python -m corvin_workflows run <name>` without
    --engine, and the CLI default is the canned stub engine."""
    import scheduler
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="report", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    item = {"id": "t1", "channel": "console", "chat_id": "c", "workflow_name": "news_sentiment",
            "workflow_inputs": {}}
    scheduler._run_workflow_to_outbox(item, now=time.time(), bridges_root=sandbox / "bridges")
    # Since 5f58f33f0 the CLI default engine is `claude`; the stub is opt-in (`--engine stub`).
    assert "stub" not in " ".join(captured["cmd"]), f"scheduled run uses the stub engine: {captured['cmd']}"



def test_wfb_r1_15_scheduled_console_workflow_runs(sandbox):
    """WFB-R1-15: a schedule created in the console names the tenant workflow by its wid. The DAG
    CLI resolves names only against the bundled examples and literal paths, so it cannot find it."""
    import scheduler
    wd = _workflows_home(sandbox)
    (wd / "my_flow.awp.yaml").write_text(
        "awp: '1.0.0'\nworkflow:\n  name: my_flow\n  description: a console-authored flow\norchestration:\n  graph:\n"
        "    - id: a\n      type: deliver\n      depends_on: []\n      config: {channel: discord, chat_id: x}\n"
    )
    outbox = sandbox / "outbox"
    item = {"id": "t2", "channel": "console", "chat_id": "c", "workflow_name": "my_flow",
            "workflow_inputs": {}}
    import os as _os
    _os.environ["ADAPTER_OUTBOX"] = str(outbox)
    try:
        scheduler._run_workflow_to_outbox(item, now=time.time())
    finally:
        _os.environ.pop("ADAPTER_OUTBOX", None)
    reports = [json.loads(p.read_text()) for p in outbox.glob("sched_wf_t2_*.json")]
    assert reports and reports[0]["exit_code"] == 0, (
        f"the scheduled run of a console-authored workflow failed: {reports[0]['text'][:200] if reports else 'no report'}"
    )


def test_wfb_r1_16_schedule_crash_does_not_refire_task(sandbox, monkeypatch):
    """WFB-R1-16: the workflow fires before the schedule file is saved. A crash between the two
    makes the restarted adapter fire the same task again (at-least-once, not intended)."""
    import scheduler
    now = time.time()
    scheduler.save([{"id": "t3", "channel": "console", "chat_id": "c", "sender": "console",
                     "tenant_id": "_default", "text": "x", "next_run": now - 5, "cron": None,
                     "last_fire": 0.0, "kind": "workflow", "workflow_name": "wf",
                     "workflow_inputs": {}}])
    count = {"n": 0}

    def fake_fire(item, now, bridges_root=None):
        count["n"] += 1
        return True

    real_save = scheduler.save

    def crash_save(items):
        raise RuntimeError("simulated crash before persisting the fired state")

    monkeypatch.setattr(scheduler, "_run_workflow_to_outbox", fake_fire)
    monkeypatch.setattr(scheduler, "save", crash_save)
    with pytest.raises(RuntimeError):
        scheduler.materialize_due(sandbox / "inbox", now=now)
    monkeypatch.setattr(scheduler, "save", real_save)
    scheduler.materialize_due(sandbox / "inbox", now=now + 60)
    assert count["n"] == 1, f"the same scheduled workflow fired {count['n']} times across a restart"


def test_wfb_r1_17_rare_recurring_cron_is_not_dropped(sandbox):
    """WFB-R1-17: materialize_due drops a recurring task when _advance_cron finds no match within a
    year. `0 0 29 2 *` (Feb 29) legitimately recurs every four years, so the schedule vanishes."""
    import scheduler
    feb29 = time.mktime((2026, 3, 1, 0, 0, 0, 0, 0, -1))
    with pytest.raises(ValueError):
        scheduler._advance_cron("0 0 29 2 *", feb29)
    # Each call on an impossible expression also burns ~1.3 s of the adapter main loop.
    t = time.time()
    with pytest.raises(ValueError):
        scheduler._advance_cron("0 0 29 2 1", time.time())
    assert time.time() - t < 0.1, "_advance_cron scans a year of minutes on the adapter loop"


def test_wfb_r1_18_schedule_file_survives_concurrent_writers(sandbox):
    """WFB-R1-18: a corrupt schedule.json reads as [] and the next add_task overwrites it, losing the
    tasks it could not parse. Silent data loss, not an error."""
    import scheduler
    scheduler.SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    scheduler.SCHEDULE_FILE.write_text('[{"id": "keep-me", "next_run": 1')  # truncated write
    with pytest.raises(scheduler.ScheduleFileCorrupt):          # a writer refuses (2026-10-21)
        scheduler.add_task(channel="console", chat_id="c", sender="s", text="t", when="in 5m")
    assert "keep-me" in scheduler.SCHEDULE_FILE.read_text(), "the corrupt schedule file was overwritten"


def test_wfb_r1_19_invalid_cron_is_rejected_by_route(sandbox):
    """WFB-R1-19: PUT /schedule accepts any cron string. The scheduler rejects it inside a
    swallowed except, the route answers 200 and the schedule never fires."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from corvin_console import auth as _auth
    from corvin_console.app import router

    rec = _auth.create_session(tenant_id="_default", token_fingerprint="fp-r1s")
    csrf = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)
    app = FastAPI()
    app.include_router(router, prefix="/v1/console")
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("corvin_console_sid", rec.sid)
    client.headers.update({"X-CSRF-Token": csrf})
    wd = _workflows_home(sandbox)
    (wd / "sched_wf.meta.json").write_text(json.dumps({"id": "sched_wf", "title": "t"}))
    resp = client.put("/v1/console/workflows/sched_wf/schedule",
                      json={"cron": "every day at noon", "timezone": "Mars/Olympus", "overrun": "skip"})
    assert resp.status_code in (400, 422), (
        f"an unparseable cron was accepted with HTTP {resp.status_code}; the schedule will never fire"
    )


def test_wfb_r1_20_overrun_policy_is_enforced(sandbox):
    """WFB-R1-20: the UI stores `overrun` (skip|queue|parallel); nothing in the scheduler reads it."""
    import scheduler
    src = inspect.getsource(scheduler)
    assert "overrun" in src, "the overrun policy is stored by the console but never enforced"


# ── Charging / concurrency ───────────────────────────────────────────────────

def test_wfb_r1_21_resume_counts_toward_concurrency_limit(sandbox):
    """WFB-R1-21: resume_run gates on _count_running_workflows(), which only counts run-meta files
    with status=running. A DAG resume writes none, so N parallel resumes all pass a limit of 1."""
    wf = _load_console_workflows()
    src = inspect.getsource(wf.resume_run)
    # A console DAG resume writes its run meta as `running`; resume_run and _decide_approval gate on
    # _count_running_workflows() + 1 like start_run (d7f9cbb77 / bc9637a9d / review 2026-10-21).
    assert "_count_running_workflows" in src and "workflows_concurrent" in src
    assert "_count_running_workflows" in inspect.getsource(wf._decide_approval)



# ── Storage ──────────────────────────────────────────────────────────────────

def test_wfb_r1_22_attachment_inline_reads_only_the_capped_prefix(sandbox):
    """WFB-R1-22: inline_block reads the whole attachment (up to 50 MiB) before slicing to 24 KiB,
    and up to 10 attachments per message do this at once."""
    from corvin_console import routes  # noqa: F401
    from corvin_console.routes import workflow_composer as wc
    src = inspect.getsource(wc.inline_block)
    assert "read_bytes()" not in src, "inline_block loads the full attachment into memory before capping it"
