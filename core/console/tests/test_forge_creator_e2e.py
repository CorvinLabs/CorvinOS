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


def tool_plan(impl: str, name: str = "Word Count") -> str:
    return json.dumps({"name": name, "description": "Counts the words in a text.",
                       "input_schema": SCHEMA, "impl": impl, "test_cases": CASES})


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

    def __init__(self, **script: str):
        self.messages = self
        self.script = script
        self.prompts: list[str] = []

    def create(self, **kwargs):
        prompt = kwargs["messages"][0]["content"]
        self.prompts.append(prompt)
        if "reviewer focused on" in prompt:
            dim = prompt.split("reviewer focused on ", 1)[1].split(".", 1)[0].lower()
            return _Reply(self.script.get(f"review_{dim}", "VERDICT: REFUTED"))
        if "Design a small, single-purpose tool" in prompt:
            return _Reply(self.script.get("tool_plan", tool_plan(GOOD_IMPL)))
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
def console(tmp_path: Path, engine, *, tier: str | None = "member", tenant: str = "_default"):
    home = tmp_path / "corvin_home"
    for sub in ("auth", "forge", "console/sessions"):
        (home / "tenants" / tenant / "global" / sub).mkdir(parents=True, exist_ok=True)
    prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID", "CORVIN_PROJECT_ROOT")}
    os.environ.update({"CORVIN_HOME": str(home), "CORVIN_TENANT_ID": tenant,
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


def chain_actions(client) -> list[str]:
    chain = client.home / "tenants" / client.tenant / "global" / "forge" / "audit.jsonl"
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
        assert "assistant.word_count" in listed_tools(client)
        assert "tool.generated_created" in chain_actions(client)


def test_tool_that_fails_its_sandbox_tests_is_not_registered(tmp_path):
    engine = ScriptedEngine(tool_plan=tool_plan(BROKEN_IMPL), tool_fix=tool_plan(BROKEN_IMPL))
    with console(tmp_path, engine) as client:
        body = poll(client, start(client, "tool", "count the words in a text"))
        assert body["status"] == "failed"
        assert "sandbox test case" in body["message"]
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
        assert detail["panel"]["sandbox"] == ["allow-scripts"]
        surface = next(f for f in detail["files"] if f["path"] == "panel/surface.yaml")
        assert json.loads(surface["content"])["sandbox"] == ["allow-scripts"]

        # The Builder index behind Settings → Plugins (/plugins/scaffolded, flag-gated).
        from plugin_builder import index_store
        assert [r["plugin_id"] for r in index_store.list_scaffolds(client.tenant)] == [plugin["plugin_id"]]
        assert "plugin.forged_staged" in chain_actions(client)

        # Not installed: the tenant plugin registry never heard of it.
        registry = client.home / "tenants" / client.tenant / "plugins" / "registry.yaml"
        assert not registry.exists() or plugin["plugin_id"] not in registry.read_text()

        resp = client.delete(f"/v1/console/forge-creator/plugins/{plugin['dirname']}")
        assert resp.status_code == 200, resp.text
        assert client.get("/v1/console/forge-creator/plugins").json()["count"] == 0
        assert index_store.list_scaffolds(client.tenant) == []
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
