"""E2E for Tool Forge + Plugin Forge (ADR-2217).

Drives the REAL console router (``corvin_console.app.router`` mounted at
/v1/console, as the SPA calls it): POST /forge-creator/{kind}/generate, poll
/forge-creator/status, then read the result back through the surfaces an
operator uses — Forge → Tools (``GET /forge/tools``), Marketplace → Forged
(``GET /forge-creator/plugins``) and the Builder listing (``GET /plugins/scaffolded``).

The engine is scripted (no login, no minutes per run); the SANDBOX is real —
tool test cases execute through ``forge.runner.run_tool`` (bwrap when present).
"""
from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "corvin_operator", _REPO / "corvin_operator" / "forge",
           _REPO / "corvin_operator" / "skill-forge", _REPO / "core" / "plugins",
           _REPO / "core" / "console"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

GOOD_IMPL = ("import json, sys\n"
             "d = json.load(sys.stdin)\n"
             "print(json.dumps({'words': len(d['text'].split())}))\n")
BROKEN_IMPL = ("import json, sys\n"
               "d = json.load(sys.stdin)\n"
               "print(json.dumps({'words': 42}))\n")
NET_IMPL = "import socket, json, sys\nprint(json.dumps({'words': 0}))\n"
SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
CASES = [{"input": {"text": "a b c"}, "expect": {"words": 3}},
         {"input": {"text": ""}, "expect": {"words": 0}}]
PANEL_HTML = ("<!doctype html><html><head><title>Status</title><style>body{font:14px sans-serif}"
              "</style></head><body><h1>Status</h1><script>document.body.dataset.ok='1'</script>"
              "</body></html>")


def tool_plan(impl: str, name: str = "Word Count", cases=None) -> str:
    return json.dumps({"name": name, "description": "Counts the words in a text.",
                       "input_schema": SCHEMA, "impl": impl,
                       "test_cases": CASES if cases is None else cases})


PLUGIN_PLAN = json.dumps({
    "plugin_name": "Weather Digest", "problem": "Summarises the daily weather for the operator.",
    "target_audience": "operators", "existing_solutions": "", "time_scope": "MVP",
    "external_libraries": [], "requires_auth": False, "requires_network_egress": True,
    "egress_hosts": ["api.open-meteo.com"], "platform_constraints": "", "scope_notes": "no alerts",
})


class _Reply:
    def __init__(self, text: str):
        self.text = text


class ScriptedEngine:
    """Answers by prompt shape; ``script`` overrides a shape's reply."""

    engine_id = "scripted"
    model = "scripted-model"

    def __init__(self, **script):
        self.messages = self
        self.script = script
        self.prompts: list[str] = []

    def create(self, **kwargs):
        prompt = kwargs["messages"][0]["content"]
        self.prompts.append(prompt)
        if "reviewer focused on" in prompt:
            dim = prompt.split("reviewer focused on ", 1)[1].split(".", 1)[0].lower()
            reply = self.script.get(f"review_{dim}", "VERDICT: REFUTED")
            if isinstance(reply, list):  # a sequence of replies; the last one repeats
                reply = reply.pop(0) if len(reply) > 1 else reply[0]
            if reply == "RAISE":
                raise RuntimeError("reviewer crashed")
            return _Reply(reply)
        if "Design a small, single-purpose tool" in prompt:
            reply = self.script.get("tool_plan", tool_plan(GOOD_IMPL))
            if isinstance(reply, list):
                reply = reply.pop(0) if len(reply) > 1 else reply[0]
            return _Reply(reply)
        if "violates its contract" in prompt:
            return _Reply(self.script.get("tool_repair", tool_plan(GOOD_IMPL)))
        if "fails its own test cases" in prompt:
            return _Reply(self.script.get("tool_fix", tool_plan(GOOD_IMPL)))
        if "You are planning a CorvinOS plugin" in prompt:
            return _Reply(self.script.get("plugin_plan", PLUGIN_PLAN))
        if "Write the console panel" in prompt:
            return _Reply("```html\n" + self.script.get("panel", PANEL_HTML) + "\n```")
        raise AssertionError(f"unexpected prompt: {prompt[:80]!r}")


@contextmanager
def console(tmp_path: Path, engine, *, tier: str | None = "member", tenant: str = "_default",
            process_tenant: str | None = None):
    home = tmp_path / "corvin_home"
    for tid in {tenant, process_tenant or tenant}:
        for sub in ("auth", "forge", "console/sessions"):
            (home / "tenants" / tid / "global" / sub).mkdir(parents=True, exist_ok=True)
    prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID", "CORVIN_PROJECT_ROOT")}
    os.environ.update({"CORVIN_HOME": str(home), "CORVIN_TENANT_ID": process_tenant or tenant,
                       "CORVIN_PROJECT_ROOT": str(home)})
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from corvin_console import auth as _auth
    from corvin_console import forge_runs
    from corvin_console.app import router
    from license import validator as _validator
    from skill_creator import skill_creator as sc

    orig_resolve = sc.resolve_llm_client
    sc.resolve_llm_client = lambda explicit=None: engine
    orig_license = _validator._ACTIVE_LICENSE
    _validator._set_active_license({"tier": tier} if tier else None)
    app = FastAPI()
    app.include_router(router, prefix="/v1/console")
    rec = _auth.create_session(tenant_id=tenant, token_fingerprint="test-fp")
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("corvin_console_sid", rec.sid)
    client.headers.update({"X-CSRF-Token": _auth.derive_csrf_token(rec.csrf_secret, rec.sid)})
    client.home = home  # type: ignore[attr-defined]
    client.tenant = tenant  # type: ignore[attr-defined]
    try:
        yield client
    finally:
        sc.resolve_llm_client = orig_resolve
        _validator._set_active_license(orig_license)
        with forge_runs.runs_lock:
            forge_runs.runs.clear()
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def poll(client, run_id: str, timeout_s: float = 120.0) -> dict:
    deadline = time.time() + timeout_s
    body: dict = {}
    while time.time() < deadline:
        resp = client.get(f"/v1/console/forge-creator/status/{run_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["status"] in ("success", "failed"):
            return body
        time.sleep(0.1)
    pytest.fail(f"run {run_id} did not finish: {body}")


def start(client, kind: str, request: str, **extra) -> str:
    resp = client.post(f"/v1/console/forge-creator/{kind}/generate",
                       json={"user_request": request, **extra})
    assert resp.status_code == 202, resp.text
    return resp.json()["run_id"]


def chain_actions(client, tenant: str | None = None) -> list[str]:
    chain = client.home / "tenants" / (tenant or client.tenant) / "global" / "forge" / "audit.jsonl"
    out = []
    if chain.exists():
        for line in chain.read_text().splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            action = (rec.get("details") or {}).get("action")
            out.append(action or rec.get("event_type") or rec.get("event") or "")
    return out


def listed_tools(client) -> list[str]:
    resp = client.get("/v1/console/forge/tools")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    tools = body.get("tools", body) if isinstance(body, dict) else body
    return [t.get("name") or t.get("id") for t in tools]


# ── Tool Forge ──────────────────────────────────────────────────────────────

def test_tool_run_registers_a_sandbox_tested_tool(tmp_path):
    engine = ScriptedEngine()
    with console(tmp_path, engine) as client:
        body = poll(client, start(client, "tool", "count the words in a text"))
        assert body["status"] == "success", body
        tool = body["tool"]
        assert tool["name"] == "assistant.word_count"
        assert tool["tests"]["passed"] == tool["tests"]["total"] == 2
        assert tool["sandbox"] == ["bwrap"]
        assert "assistant.word_count" in listed_tools(client)
        actions = chain_actions(client)
        assert "tool.generated_created" in actions
        # Each execution of generated code is on the TENANT chain.
        assert actions.count("forge.tool_executed") == 2


def test_tool_that_fails_its_sandbox_tests_is_not_registered(tmp_path):
    engine = ScriptedEngine(tool_plan=tool_plan(BROKEN_IMPL), tool_fix=tool_plan(BROKEN_IMPL))
    with console(tmp_path, engine) as client:
        body = poll(client, start(client, "tool", "count the words in a text"))
        assert body["status"] == "failed"
        assert "sandbox test case" in body["message"]
        # The operator sees which cases failed, not only that it failed.
        assert body["tool"]["tests"]["passed"] < body["tool"]["tests"]["total"]
        assert "assistant.word_count" not in listed_tools(client)
        assert "tool.generated_creation_failed" in chain_actions(client)


def test_tool_with_a_forbidden_import_never_runs_or_registers(tmp_path):
    engine = ScriptedEngine(tool_plan=tool_plan(NET_IMPL), tool_repair=tool_plan(NET_IMPL))
    with console(tmp_path, engine) as client:
        body = poll(client, start(client, "tool", "count the words in a text"))
        assert body["status"] == "failed"
        assert "socket" in body["message"]
        assert not any("fails its own test cases" in p for p in engine.prompts)
        assert "assistant.word_count" not in listed_tools(client)


def test_confirmed_security_finding_blocks_registration(tmp_path):
    engine = ScriptedEngine(review_security="FINDING: reads environment secrets\nVERDICT: CONFIRMED")
    with console(tmp_path, engine) as client:
        body = poll(client, start(client, "tool", "count the words in a text"))
        assert body["status"] == "failed"
        assert "security reviewer" in body["message"]
        assert "assistant.word_count" not in listed_tools(client)


def test_no_bwrap_means_no_execution_and_no_registration(tmp_path, monkeypatch):
    engine = ScriptedEngine()
    with console(tmp_path, engine) as client:
        from forge import sandbox
        monkeypatch.setattr(sandbox, "have_bwrap", lambda: False)
        body = poll(client, start(client, "tool", "count the words in a text"))
        assert body["status"] == "failed"
        assert "bubblewrap" in body["message"]
        assert "forge.tool_executed" not in chain_actions(client)
        assert "assistant.word_count" not in listed_tools(client)


@pytest.mark.parametrize("reply", ["RAISE", "I refuse to answer in that format."])
def test_security_review_that_did_not_complete_blocks_registration(tmp_path, reply):
    engine = ScriptedEngine(review_security=reply)
    with console(tmp_path, engine) as client:
        body = poll(client, start(client, "tool", "count the words in a text"))
        assert body["status"] == "failed"
        assert "security review did not complete" in body["message"]
        assert "assistant.word_count" not in listed_tools(client)


def test_test_cases_without_expectations_are_refused(tmp_path):
    empty = [{"input": {"text": "a b"}, "expect": {}}, {"input": {"text": ""}, "expect": {}}]
    engine = ScriptedEngine(tool_plan=tool_plan(BROKEN_IMPL, cases=empty),
                            tool_repair=tool_plan(BROKEN_IMPL, cases=empty))
    with console(tmp_path, engine) as client:
        body = poll(client, start(client, "tool", "count the words in a text"))
        assert body["status"] == "failed"
        assert "expect" in body["message"]
        assert "assistant.word_count" not in listed_tools(client)


def test_a_fix_cannot_rewrite_the_cases_that_judge_it(tmp_path):
    rigged = [{"input": {"text": "a b c"}, "expect": {"words": 42}},
              {"input": {"text": ""}, "expect": {"words": 42}}]
    engine = ScriptedEngine(tool_plan=tool_plan(BROKEN_IMPL),
                            tool_fix=tool_plan(BROKEN_IMPL, cases=rigged))
    with console(tmp_path, engine) as client:
        body = poll(client, start(client, "tool", "count the words in a text"))
        assert body["status"] == "failed"
        assert "assistant.word_count" not in listed_tools(client)


def test_a_second_tool_with_the_same_name_is_refused_before_running(tmp_path):
    engine = ScriptedEngine()
    with console(tmp_path, engine) as client:
        assert poll(client, start(client, "tool", "count the words in a text"))["status"] == "success"
        body = poll(client, start(client, "tool", "count the words in a text again"))
        assert body["status"] == "failed"
        assert "already exists" in body["message"]
        assert chain_actions(client).count("forge.tool_executed") == 2  # only the first run executed


def test_the_registry_licence_gate_asks_for_the_session_tenant(tmp_path, monkeypatch):
    """ADR-0703: the G1 gate inside Registry.create is decided for the SESSION's
    tenant, not the process's. (The audit chokepoint itself binds a console process
    to one tenant context — F-A6 — so the records are not asserted here.)"""
    engine = ScriptedEngine()
    with console(tmp_path, engine, tenant="acme", process_tenant="_default") as client:
        from corvin_operator.license import capability_api
        asked = []
        real = capability_api.require_capability

        def spy(capability, **kw):
            if capability == "forge.create" and str(kw.get("entry_point", "")).startswith("forge:registry"):
                asked.append(kw.get("tenant_id"))
            return real(capability, **kw)

        monkeypatch.setattr(capability_api, "require_capability", spy)
        body = poll(client, start(client, "tool", "count the words in a text"))
        assert body["status"] == "success", body
        assert asked and set(asked) == {"acme"}
        assert (client.home / "tenants" / "acme" / "forge" / "registry.json").exists()


# ── Plugin Forge ────────────────────────────────────────────────────────────

def test_plugin_run_stages_with_panel_and_shows_in_marketplace(tmp_path):
    engine = ScriptedEngine()
    with console(tmp_path, engine) as client:
        body = poll(client, start(client, "plugin", "a plugin that summarises the daily weather",
                                  panel_request="show today's summary"))
        assert body["status"] == "success", body
        plugin = body["plugin"]
        assert plugin["plugin_id"].startswith("community.")
        assert "panel/index.html" in plugin["files"] and "panel/surface.yaml" in plugin["files"]

        listing = client.get("/v1/console/forge-creator/plugins").json()
        assert [p["dirname"] for p in listing["plugins"]] == [plugin["dirname"]]
        entry = listing["plugins"][0]
        assert entry["installed"] is False and entry["origin_on_install"] == "community"

        detail = client.get(f"/v1/console/forge-creator/plugins/{plugin['dirname']}").json()
        assert detail["panel_html"] == PANEL_HTML
        surface = next(f for f in detail["files"] if f["path"] == "panel/surface.yaml")
        # The plugin names its entry; sandbox tokens are never self-declared (ADR-2189 D2).
        assert json.loads(surface["content"]) == {
            "kind": "web_surface", "id": plugin["plugin_id"].replace(".", "-"),
            "title": "Weather Digest", "entry": "index.html"}
        prov = json.loads(next(f for f in detail["files"] if f["path"] == "FORGE_PROVENANCE.json")["content"])
        assert prov["generator"] == "plugin_builder" and prov["surface"] == "plugin_forge"
        assert "request" not in prov and prov["request_chars"] == len("a plugin that summarises the daily weather")
        assert detail["request_chars"] == prov["request_chars"]

        # One reader per concept: forged plugins are listed by Forged only, not the Builder index.
        from plugin_builder import index_store
        assert index_store.list_scaffolds(client.tenant) == []
        assert "plugin.forged_staged" in chain_actions(client)

        # Not installed: the tenant plugin registry never heard of it.
        registry = client.home / "tenants" / client.tenant / "plugins" / "registry.yaml"
        assert not registry.exists() or plugin["plugin_id"] not in registry.read_text()

        resp = client.delete(f"/v1/console/forge-creator/plugins/{plugin['dirname']}")
        assert resp.status_code == 200, resp.text
        assert client.get("/v1/console/forge-creator/plugins").json()["count"] == 0
        assert "plugin.forged_deleted" in chain_actions(client)


def test_panel_with_an_external_script_fails_and_leaves_nothing_staged(tmp_path):
    bad = "<html><body><script src='https://cdn.example.com/x.js'></script></body></html>"
    engine = ScriptedEngine(panel=bad)
    with console(tmp_path, engine) as client:
        body = poll(client, start(client, "plugin", "a plugin that summarises the daily weather",
                                  panel_request="show today's summary"))
        assert body["status"] == "failed"
        assert "external script" in body["message"]
        assert client.get("/v1/console/forge-creator/plugins").json()["count"] == 0
        forged_root = client.home / "tenants" / client.tenant / "plugin-builder"
        assert not forged_root.exists() or not any(forged_root.iterdir())


# ── Gates and boundaries ────────────────────────────────────────────────────

def test_spawn_gate_refusal_blocks_tool_plugin_and_skill(tmp_path, monkeypatch):
    engine = ScriptedEngine()
    with console(tmp_path, engine) as client:
        from corvin_console import _spawn_gates
        monkeypatch.setattr(_spawn_gates, "check_console_spawn_or_refusal",
                            lambda *a, **k: "Refused by house rules.")
        for kind in ("tool", "plugin"):
            resp = client.post(f"/v1/console/forge-creator/{kind}/generate",
                               json={"user_request": "do something forbidden here"})
            assert resp.status_code == 403 and "house rules" in resp.text
        resp = client.post("/v1/console/skill-creator/generate",
                           json={"user_request": "do something forbidden here"})
        assert resp.status_code == 403 and "house rules" in resp.text
        assert engine.prompts == []
        assert chain_actions(client).count("forge.generation_refused") == 3


def test_free_tier_is_refused_with_402(tmp_path):
    with console(tmp_path, ScriptedEngine(), tier=None) as client:
        resp = client.post("/v1/console/forge-creator/tool/generate",
                           json={"user_request": "count the words in a text"})
        assert resp.status_code == 402, resp.text


def test_missing_csrf_is_refused(tmp_path):
    with console(tmp_path, ScriptedEngine()) as client:
        del client.headers["X-CSRF-Token"]
        resp = client.post("/v1/console/forge-creator/tool/generate",
                           json={"user_request": "count the words in a text"})
        assert resp.status_code == 403, resp.text


def test_runs_are_tenant_and_kind_bound(tmp_path):
    engine = ScriptedEngine()
    with console(tmp_path, engine) as client:
        run_id = start(client, "tool", "count the words in a text")
        poll(client, run_id)
        # A tool run is not a skill run.
        assert client.get(f"/v1/console/skill-creator/status/{run_id}").status_code == 404
        from corvin_console import auth as _auth
        other = _auth.create_session(tenant_id="other", token_fingerprint="other-fp")
        client.cookies.set("corvin_console_sid", other.sid)
        assert client.get(f"/v1/console/forge-creator/status/{run_id}").status_code == 404


def test_unknown_kind_and_bad_dirname_are_rejected(tmp_path):
    with console(tmp_path, ScriptedEngine()) as client:
        resp = client.post("/v1/console/forge-creator/panel/generate",
                           json={"user_request": "count the words in a text"})
        assert resp.status_code == 422
        assert client.get("/v1/console/forge-creator/plugins/..%2F..%2Fetc").status_code in (400, 404)
        assert client.get("/v1/console/forge-creator/plugins/not_forged").status_code == 404
        resp = client.post("/v1/console/forge-creator/tool/generate",
                           json={"user_request": "count the words in a text", "panel_request": "x"})
        assert resp.status_code == 400


def test_a_lapsed_member_can_still_delete_a_forged_plugin(tmp_path):
    with console(tmp_path, ScriptedEngine()) as client:
        plugin = poll(client, start(client, "plugin", "a plugin that summarises the daily weather"))["plugin"]
        from license import validator as _validator
        _validator._set_active_license(None)  # free tier now
        assert client.post("/v1/console/forge-creator/plugin/generate",
                           json={"user_request": "another plugin for the weather"}).status_code == 402
        resp = client.delete(f"/v1/console/forge-creator/plugins/{plugin['dirname']}")
        assert resp.status_code == 200, resp.text


def test_an_existing_directory_blocks_the_name_and_is_left_alone(tmp_path):
    with console(tmp_path, ScriptedEngine()) as client:
        squatter = client.home / "tenants" / client.tenant / "plugin-builder" / "community_weather_digest"
        squatter.mkdir(parents=True)
        (squatter / "keep.txt").write_text("chat builder scaffold")
        body = poll(client, start(client, "plugin", "a plugin that summarises the daily weather"))
        assert body["status"] == "failed" and "already exists" in body["message"]
        assert (squatter / "keep.txt").read_text() == "chat builder scaffold"


def test_compile_check_reports_a_syntax_error(tmp_path):
    from skill_creator.plugin_creator import PluginCreatorOrchestrator
    (tmp_path / "plugin.py").write_text("def broken(:\n    pass\n")
    (tmp_path / "ok.py").write_text("x = 1\n")
    problems = PluginCreatorOrchestrator.compile_check(tmp_path)
    assert len(problems) == 1 and problems[0].startswith("plugin.py")
    assert not list(tmp_path.glob("*.forgecheck"))


def test_a_refine_gates_the_base_skill_body_too(tmp_path, monkeypatch):
    with console(tmp_path, ScriptedEngine()) as client:
        body = "# assistant.notes_helper\n\nSECRET-BASE-BODY-MARKER keeps meeting notes tidy and short.\n"
        resp = client.post("/v1/console/skills/manual", json={"name": "assistant.notes_helper", "body": body})
        assert resp.status_code in (200, 201), resp.text
        seen = []
        from corvin_console import _spawn_gates
        monkeypatch.setattr(_spawn_gates, "check_console_spawn_or_refusal",
                            lambda prompt, **k: seen.append(prompt) or "refused for the test")
        resp = client.post("/v1/console/skill-creator/generate",
                           json={"user_request": "make it shorter please", "base_skill": "assistant.notes_helper"})
        assert resp.status_code == 403
        assert "SECRET-BASE-BODY-MARKER" in seen[0]


def test_too_many_runs_in_flight_is_429(tmp_path, monkeypatch):
    with console(tmp_path, ScriptedEngine()) as client:
        from corvin_console import forge_runs
        monkeypatch.setattr(forge_runs, "MAX_RUNNING_PER_TENANT", 0)
        resp = client.post("/v1/console/forge-creator/tool/generate",
                           json={"user_request": "count the words in a text"})
        assert resp.status_code == 429, resp.text


def test_task_board_labels_forge_runs_by_kind(tmp_path):
    with console(tmp_path, ScriptedEngine()) as client:
        poll(client, start(client, "plugin", "a plugin that summarises the daily weather"))
        from corvin_console import task_sources
        import time as _t
        rows = list(task_sources._skill_creator(client.home / "tenants" / client.tenant, _t.time()))
        assert [r["subtype"] for r in rows] == ["plugin"]


# ── Console restart: runs survive (persisted) and are resumed ───────────────

def _run_file(client, run_id: str) -> Path:
    return client.home / "tenants" / client.tenant / "global" / "forge_runs" / f"{run_id}.json"


def _simulate_restart(client, run_id: str, **patch) -> None:
    """What a restart leaves behind: the record on disk, a dead process's boot id,
    and an empty in-memory store."""
    from corvin_console import forge_runs
    path = _run_file(client, run_id)
    rec = json.loads(path.read_text())
    rec.update({"boot_id": "previous-process", **patch})
    path.write_text(json.dumps(rec))
    with forge_runs.runs_lock:
        forge_runs.runs.clear()


def _orphaned_tool_run(client, **patch) -> str:
    """A tool run the previous process was still working on when it died."""
    from corvin_console import forge_runs
    from corvin_console.routes.forge_creator import _phases
    run_id = forge_runs.new_run(
        tenant_id=client.tenant, kind="tool", phases=_phases("tool"), sid_fingerprint=None,
        resume={"request": "count the words in a text", "panel_request": ""})
    _simulate_restart(client, run_id, **patch)
    return run_id


def test_finished_run_is_still_pollable_after_a_console_restart(tmp_path):
    with console(tmp_path, ScriptedEngine()) as client:
        run_id = start(client, "tool", "count the words in a text")
        assert poll(client, run_id)["status"] == "success"
        _simulate_restart(client, run_id)
        body = poll(client, run_id)
        assert body["status"] == "success" and body["tool"]["name"] == "assistant.word_count"


def test_interrupted_run_is_resumed_after_a_console_restart(tmp_path):
    with console(tmp_path, ScriptedEngine()) as client:
        run_id = _orphaned_tool_run(client)
        body = poll(client, run_id)  # never a 404: the run is re-spawned from its request
        assert body["status"] in ("running", "success")
        assert poll(client, run_id)["status"] == "success"
        assert json.loads(_run_file(client, run_id).read_text())["resumes"] == 1


def test_run_that_cannot_be_resumed_reports_interrupted_not_404(tmp_path):
    with console(tmp_path, ScriptedEngine()) as client:
        run_id = _orphaned_tool_run(client, resumes=2)
        body = client.get(f"/v1/console/forge-creator/status/{run_id}").json()
        assert body["status"] == "failed" and "restart" in body["message"]


def test_run_record_is_not_readable_across_tenants_or_by_guessed_path(tmp_path):
    with console(tmp_path, ScriptedEngine()) as client:
        run_id = start(client, "tool", "count the words in a text")
        poll(client, run_id)
        assert client.get("/v1/console/forge-creator/status/..%2F..%2Fx").status_code == 404
        assert oct(_run_file(client, run_id).stat().st_mode & 0o777) == "0o600"


# ── Tool Forge robustness: every tool must be able to finish (review gate) ──

SMA_IMPL = ("import json, sys\n"
            "d = json.load(sys.stdin)\n"
            "c = [float(x['Close']) for x in d['bars']]\n"
            "n = int(d.get('fast', 2))\n"
            "sig = [sum(c[i-n+1:i+1]) / n for i in range(n-1, len(c))]\n"
            "print(json.dumps({'ok': True, 'signals': len(sig), 'last_close': c[-1]}))\n")
SMA_SCHEMA = {"type": "object", "required": ["bars"],
              "properties": {"bars": {"type": "array"}, "fast": {"type": "integer"}}}
SMA_CASES = [{"input": {"bars": [{"Close": 1}, {"Close": 2}, {"Close": 3}], "fast": 2},
              "expect": {"ok": True, "signals": 2, "last_close": 3.0}},
             {"input": {"bars": [{"Close": 5}, {"Close": 7}], "fast": 2},
              "expect": {"ok": True, "signals": 1, "last_close": 7.0}}]
UPPER_IMPL = ("import json, sys\n"
              "d = json.load(sys.stdin)\n"
              "print(json.dumps({'upper': d['text'].upper()}))\n")
UPPER_CASES = [{"input": {"text": "ab"}, "expect": {"upper": "AB"}},
               {"input": {"text": ""}, "expect": {"upper": ""}}]
SUM_IMPL = ("import json, sys\n"
            "d = json.load(sys.stdin)\n"
            "print(json.dumps({'total': sum(d['numbers'])}))\n")
SUM_SCHEMA = {"type": "object", "required": ["numbers"], "properties": {"numbers": {"type": "array"}}}
SUM_CASES = [{"input": {"numbers": [1, 2, 3]}, "expect": {"total": 6}},
             {"input": {"numbers": []}, "expect": {"total": 0}}]


def _plan(name, impl, cases, schema=SCHEMA):
    return json.dumps({"name": name, "description": "A small deterministic helper tool.",
                       "input_schema": schema, "impl": impl, "test_cases": cases})


@pytest.mark.parametrize("name,plan", [
    ("assistant.sma_signals", _plan("assistant.sma_signals", SMA_IMPL, SMA_CASES, SMA_SCHEMA)),
    ("assistant.shout", _plan("assistant.shout", UPPER_IMPL, UPPER_CASES)),
    ("assistant.sum_numbers", _plan("assistant.sum_numbers", SUM_IMPL, SUM_CASES, SUM_SCHEMA)),
])
def test_every_kind_of_tool_runs_through_all_five_phases(tmp_path, name, plan):
    engine = ScriptedEngine(tool_plan=plan)
    with console(tmp_path, engine) as client:
        seen: set[str] = set()
        run_id = start(client, "tool", "a small deterministic helper tool please")
        while True:
            body = client.get(f"/v1/console/forge-creator/status/{run_id}").json()
            seen.add(body["phase"])
            if body["status"] in ("success", "failed"):
                break
            time.sleep(0.02)
        assert body["status"] == "success", body
        assert body["tool"]["name"] == name
        assert body["tool"]["tests"]["passed"] == body["tool"]["tests"]["total"]
        assert name in listed_tools(client)
        assert body["phase"] == "promotion"


def test_a_reviewer_format_slip_is_retried_instead_of_failing_the_run(tmp_path):
    # Measured live: ~1 in 4 security replies was prose without a VERDICT line.
    engine = ScriptedEngine(review_security=["Der Code ist unbedenklich, keine Befunde.",
                                             "VERDICT: REFUTED"])
    with console(tmp_path, engine) as client:
        body = poll(client, start(client, "tool", "count the words in a text"))
        assert body["status"] == "success", body
        assert body["tool"]["review_attempts"] == 2
        assert "assistant.word_count" in listed_tools(client)


def test_robustness_findings_are_advisory_and_do_not_block(tmp_path):
    engine = ScriptedEngine(review_security=(
        "FINDING: [ROBUSTNESS] an empty text list is not validated\nVERDICT: CONFIRMED"))
    with console(tmp_path, engine) as client:
        body = poll(client, start(client, "tool", "count the words in a text"))
        assert body["status"] == "success", body
        adv = [f for f in body["tool"]["findings"] if f["dimension"] == "security"]
        assert adv and adv[0]["verdict"] == "confirmed" and adv[0]["advisory"] is True


@pytest.mark.parametrize("finding", [
    "FINDING: [SHELL] spawns a shell from the input\nVERDICT: CONFIRMED",
    "FINDING: reads environment secrets\nVERDICT: CONFIRMED",  # untagged stays blocking
    "FINDING: [ROBUSTNESS] but it calls os.system on the text\nVERDICT: CONFIRMED",  # tag cannot launder a boundary
])
def test_boundary_findings_still_block_even_with_the_robustness_escape_hatch(tmp_path, finding):
    engine = ScriptedEngine(review_security=finding)
    with console(tmp_path, engine) as client:
        body = poll(client, start(client, "tool", "count the words in a text"))
        assert body["status"] == "failed"
        assert body["failure_code"] == "security_confirmed"
        assert "assistant.word_count" not in listed_tools(client)


def test_unavailable_reviewer_keeps_the_draft_and_a_retry_never_regenerates(tmp_path):
    engine = ScriptedEngine(review_security="Ich pruefe das gleich, hier mein Bericht ohne Urteil.")
    with console(tmp_path, engine) as client:
        failed = poll(client, start(client, "tool", "count the words in a text"))
        assert failed["status"] == "failed"
        assert failed["failure_code"] == "reviewer_unavailable"
        assert failed["tool"]["review_attempts"] == 1 + 2  # first try + REVIEW_RETRIES
        assert failed["draft_saved"] is True
        assert "forge.tool_rejected" in chain_actions(client)
        plans_before = sum("Design a small" in p for p in engine.prompts)

        engine.script["review_security"] = "VERDICT: REFUTED"  # the reviewer recovers
        resp = client.post(f"/v1/console/forge-creator/tool/retry/{failed['run_id']}")
        assert resp.status_code == 202, resp.text
        body = poll(client, resp.json()["run_id"])
        assert body["status"] == "success", body
        assert sum("Design a small" in p for p in engine.prompts) == plans_before == 1
        assert "assistant.word_count" in listed_tools(client)


def test_retry_is_refused_for_unknown_and_for_successful_runs(tmp_path):
    engine = ScriptedEngine()
    with console(tmp_path, engine) as client:
        ok = poll(client, start(client, "tool", "count the words in a text"))
        assert client.post(f"/v1/console/forge-creator/tool/retry/{ok['run_id']}").status_code == 409
        assert client.post("/v1/console/forge-creator/tool/retry/nope").status_code == 404


def test_a_rejection_is_audited_with_its_reason_code(tmp_path):
    engine = ScriptedEngine(tool_plan=tool_plan(BROKEN_IMPL), tool_fix=tool_plan(BROKEN_IMPL))
    with console(tmp_path, engine) as client:
        body = poll(client, start(client, "tool", "count the words in a text"))
        assert body["failure_code"] == "tests_failed"
        chain = client.home / "tenants" / client.tenant / "global" / "forge" / "audit.jsonl"
        recs = [json.loads(line) for line in chain.read_text().splitlines() if line.strip()]
        rej = [r for r in recs if r.get("event_type") == "forge.tool_rejected"]
        assert rej and rej[-1]["details"]["code"] == "tests_failed"
        assert rej[-1]["details"]["tests_total"] == 2


# ── Tool Forge robustness: an unusable plan reply is asked again, never "repaired" ──

def test_plan_reply_without_the_tool_keys_is_re_planned_not_repaired(tmp_path):
    # A stray example object ahead of the real plan, then a reply with no plan at all.
    stray = 'Beispiel: {"ok": true}\n' + tool_plan(GOOD_IMPL)
    engine = ScriptedEngine(tool_plan=['{"error": "no plan"}', stray])
    with console(tmp_path, engine) as client:
        body = poll(client, start(client, "tool", "count the words in a text"))
        assert body["status"] == "success", body
        assert sum("Design a small" in p for p in engine.prompts) == 2
        assert not any("violates its contract" in p for p in engine.prompts)  # no repair of an empty shell


def test_a_plan_that_never_arrives_fails_with_a_clear_code(tmp_path):
    engine = ScriptedEngine(tool_plan='{"error": "no plan"}')
    with console(tmp_path, engine) as client:
        body = poll(client, start(client, "tool", "count the words in a text"))
        assert body["status"] == "failed" and body["failure_code"] == "plan_unusable"
        assert sum("Design a small" in p for p in engine.prompts) == 3
