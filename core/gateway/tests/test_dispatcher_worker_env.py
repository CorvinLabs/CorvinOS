"""Gateway run spawn: worker env, MCP isolation, routing audit, model, spans.

Adversarial review 2026-11-05 (delegation):

* DLG-3  — the gateway spawned ``claude -p`` with the host operator's FULL
  environment (every API key, bot token, the audit anchor key) on a
  multi-tenant API surface, and without ``--strict-mcp-config``. The spawn env
  is now built like the ACS worker's (``acs_runtime._strip_worker_secrets`` →
  ``_restore_platform_credentials`` → ``_apply_provider_redirect``); the engine
  merges its ``env`` over ``os.environ``, so every stripped name is passed
  as ``""``.
* DLG-5  — the routing decision was "audited" through a function that does not
  exist; it now lands on the tenant's chain with closed fields only.
* DLG-6  — the Haiku fallback read the PROCESS tenant's Console choice.
* DLG-18 — the raw ``/delegate …`` text reached the engine.
* DLG-19 — ``_model_is_available`` was always True.
* DLG-20 — spans closed on the configured model when the engine reported none.

The end-to-end test drives the REAL FastAPI route, the REAL dispatcher and the
REAL ``ClaudeCodeEngine`` against a recording stand-in binary (same harness as
``test_dispatcher_prompt_guard.py``) and asserts on what the child process
actually received.

Run:  .venv/bin/python -m pytest -q -p no:cacheprovider \
          core/gateway/tests/test_dispatcher_worker_env.py
"""
from __future__ import annotations

import json
import stat
import sys
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "gateway"))
sys.path.insert(0, str(_REPO / "corvin_operator" / "forge"))
sys.path.insert(0, str(_REPO / "corvin_operator" / "bridges" / "shared"))

from fastapi.testclient import TestClient  # noqa: E402

#: Operator secrets a tenant's run must never see. Values are unique markers so
#: a leak is unambiguous wherever it shows up.
_SECRETS = {
    "OPENAI_API_KEY": "sk-dlg3-openai-marker",
    "GITHUB_TOKEN": "ghp_dlg3githubmarker000000000000000000",
    "CORVIN_WDAT_KEY": "dlg3-wdat-marker",
    "SOME_BOT_TOKEN": "dlg3-bot-marker",
}

#: One file per invocation (the house-rules classifier runs through the same
#: binary). Records argv and the env names this test asserts on.
_RECORDER = r"""#!/usr/bin/env python3
import json, os, sys, uuid
out = os.environ["FAKE_CLAUDE_OUT"]
argv = sys.argv[1:]
watch = json.loads(os.environ.get("FAKE_CLAUDE_WATCH", "[]"))
env = {k: os.environ.get(k) for k in watch}
with open(os.path.join(out, "spawn-%s.json" % uuid.uuid4().hex), "w") as fh:
    json.dump({"argv": argv, "env": env}, fh)
joined = " ".join(argv)
if "violated_rule_id" in joined:
    sys.stdout.write(json.dumps({"result": json.dumps(
        {"violated_rule_id": "", "confidence": 0.98, "reason": "test fixture"})}))
elif "--version" in argv:
    sys.stdout.write("2.1.263 (Claude Code)\n")
else:
    # No init frame: the engine reports NO model for this run (DLG-20).
    sys.stdout.write(json.dumps(
        {"type": "result", "subtype": "success", "result": "ok"}) + "\n")
"""


@pytest.fixture()
def gateway(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "tenants" / "acme" / "global" / "forge").mkdir(parents=True)
    (home / "tenants" / "acme" / "global" / "gateway" / "runs").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))

    out = tmp_path / "rec-out"
    out.mkdir()
    binary = tmp_path / "claude-recorder"
    binary.write_text(_RECORDER, encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("FAKE_CLAUDE_OUT", str(out))
    monkeypatch.setenv("CORVIN_CLAUDE_BIN", str(binary))
    monkeypatch.delenv("ADAPTER_FAKE_CLAUDE", raising=False)
    for name, value in _SECRETS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(
        "FAKE_CLAUDE_WATCH",
        json.dumps([*_SECRETS, "CORVIN_TENANT_ID", "CORVIN_CHANNEL_ID"]),
    )

    from corvin_gateway.app import app
    return app, out, home


def _run_spawn(out: Path, timeout: float = 30.0) -> dict:
    """The run's own engine spawn (``--dangerously-skip-permissions``, which the
    dispatcher's kwargs select), not the house-rules classifier."""
    deadline = time.time() + timeout
    seen: list = []
    while time.time() < deadline:
        seen = []
        for path in sorted(out.glob("spawn-*.json")):
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            seen.append(rec["argv"])
            if "--dangerously-skip-permissions" in rec["argv"]:
                return rec
        time.sleep(0.05)
    raise AssertionError(f"the gateway never spawned the run engine; spawns: {seen!r}")


def _chain(home: Path, tenant: str) -> list[dict]:
    path = home / "tenants" / tenant / "global" / "forge" / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _wait_for(home: Path, tenant: str, event: str, timeout: float = 15.0) -> list[dict]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        hits = [r for r in _chain(home, tenant) if r.get("event_type") == event]
        if hits:
            return hits
        time.sleep(0.05)
    return []


def test_gateway_run_spawn_env_argv_audit_and_span(gateway):
    app, out, home = gateway
    task = "say hello to the team"
    with TestClient(app) as client:
        r = client.post(
            "/v1/tenants/acme/runs",
            json={
                "apiVersion": "corvin/v1",
                "kind": "Run",
                "spec": {"persona": "customer-support", "input": f"/delegate {task}"},
            },
        )
        assert r.status_code == 202, r.text
        rec = _run_spawn(out)
        ends = _wait_for(home, "acme", "engine.span.end")

    argv, env = rec["argv"], rec["env"]

    # DLG-3: every operator secret is blank in the child, the tenant is the run's.
    for name, value in _SECRETS.items():
        assert env.get(name) in ("", None), f"{name} reached the tenant's run: {env.get(name)!r}"
        assert value not in json.dumps(rec)
    assert env["CORVIN_TENANT_ID"] == "acme"
    assert env["CORVIN_CHANNEL_ID"].startswith("gateway:")
    # DLG-3: only the MCP servers the spawn names — none — load.
    assert "--strict-mcp-config" in argv
    assert "--mcp-config" not in argv

    # DLG-18: the routing prefix never reaches the engine.
    prompt = argv[-1]
    assert "/delegate" not in prompt
    assert task in prompt

    # DLG-5: the routing decision is on the run tenant's chain, closed fields only.
    routed = _wait_for(home, "acme", "intelligent_routing_decision")
    assert routed, "the routing decision never reached the tenant's audit chain"
    details = routed[-1]["details"]
    assert "reasoning" not in details
    assert not details.get("_dropped_fields"), details
    assert details["engine"] == "claude_code" and "tenant_id" not in details
    assert details["run_id"] == r.json()["run_id"]
    assert details["model"] and details["tier"]
    assert routed[-1]["severity"] == "INFO"

    # The model that was routed is the one the CLI was told to use.
    assert argv[argv.index("--model") + 1] == details["model"]

    # DLG-20: the start record keeps the REQUESTED model, the end record the
    # REPORTED one — and this engine reported none.
    starts = [r for r in _chain(home, "acme") if r.get("event_type") == "engine.span.start"]
    assert starts and starts[-1]["details"]["model_id"] == details["model"]
    assert ends, "no engine.span.end on the tenant chain"
    assert ends[-1]["details"]["model_id"] == "", ends[-1]["details"]


# ── unit-level pins for the helpers the E2E goes through ────────────────────


def test_newest_haiku_reads_the_runs_tenant_not_the_process_tenant(tmp_path, monkeypatch):
    """DLG-6: tenant B's fallback is tenant B's saved SIMPLE choice."""
    from corvin_gateway import dispatcher

    home = tmp_path / "home"
    for tid, model in (("_default", "claude-haiku-4-5-20251001"), ("beta", "claude-haiku-5-5")):
        cfg = home / "tenants" / tid / "global" / "model_selection_config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"models": {"SIMPLE": {
            "selected_model": model, "provider": "anthropic"}}}), encoding="utf-8")
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")

    assert dispatcher._newest_haiku("beta") == "claude-haiku-5-5"
    assert dispatcher._newest_haiku("_default") == "claude-haiku-4-5-20251001"


def test_model_is_available_is_a_real_registry_check():
    """DLG-19: a definite miss on a known engine is unavailable; an engine the
    registry does not describe cannot be judged and passes through."""
    from corvin_gateway import dispatcher
    import model_lineage

    current = model_lineage.latest("haiku")
    assert dispatcher._model_is_available(current, "claude_code") is True
    assert dispatcher._model_is_available("claude-nonexistent-9-9", "claude_code") is False
    assert dispatcher._model_is_available("claude-nonexistent-9-9", "native") is True


def test_router_pick_that_is_not_registered_falls_back_to_the_tenants_haiku(tmp_path, monkeypatch):
    """DLG-19 + DLG-6 together: an unregistered router pick is replaced by the
    RUN tenant's cheap tier."""
    from types import SimpleNamespace

    from corvin_gateway.dispatcher import RunDispatcher
    from core.skills.os_skills import intelligent_router_integration as iri

    home = tmp_path / "home"
    cfg = home / "tenants" / "beta" / "global" / "model_selection_config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"models": {"SIMPLE": {
        "selected_model": "claude-haiku-5-5", "provider": "anthropic"}}}), encoding="utf-8")
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.setattr(
        iri.IntelligentRouterBridge, "route_with_intelligent_selection",
        staticmethod(lambda *a, **k: SimpleNamespace(
            model="claude-nonexistent-9-9", tier="simple", confidence=0.9, reasoning="x")),
    )
    assert RunDispatcher._resolve_worker_model("beta", "claude_code", prompt="hi") == "claude-haiku-5-5"


def test_routing_event_is_registered_with_a_closed_field_set():
    from forge import security_events as se

    for name in ("intelligent_routing_decision", "gateway.engine_denied", "gateway.zone_denied"):
        assert name in se.EVENT_SEVERITY, name
        assert name in se._EVENT_ALLOWLIST, name
    assert "reasoning" not in se._EVENT_ALLOWLIST["intelligent_routing_decision"]
    assert {"model", "tier", "confidence", "engine", "run_id"} == set(
        se._EVENT_ALLOWLIST["intelligent_routing_decision"])
