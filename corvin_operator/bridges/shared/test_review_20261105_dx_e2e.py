"""Adversarial review 2026-11-05 (area DX) — worker / delegation execution paths.

Every test drives a REAL entry point with a REAL subprocess: a recording ``claude``
shim (a Python script put in place of the CLI through ``CORVIN_CLAUDE_BIN`` /
``ClaudeCodeEngine(binary=...)``) that logs its argv, its stdin and the names of its
environment, and answers with a canned CLI JSON envelope. Nothing inside the product
is replaced except the L44 classifier verdict (pinned to "allow" so no model call is
made, the idiom of tests/security/test_t0254_secret_floor_no_policy.py) and the
licence check (the documented dual-env test bypass).

DX-1  ACS manager + worker L34 gates passed only the workflow's DECLARED class and never
      the prompt, so a credential in an ``acs_delegate`` task (or in a manager-written
      subtask) reached a tool-capable cloud worker — every other spawn surface refuses
      it (T-0254, review L34-3: a declared class may only RAISE).
DX-2  The inbound A2A (L38) worker inherited every operator secret that was not on a
      fixed 14-name list (GITHUB_TOKEN, CORVIN_WDAT_KEY, PGPASSWORD, ...), while the
      ACS worker strips by name pattern.
DX-3  The ACS manager span closed on the CONFIGURED model with zero tokens: the
      manager's own CLI envelope (model + four-way usage) was parsed for its text and
      the rest thrown away, so every manager turn was priced at $0.00.
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
for _p in (_HERE, _REPO / "corvin_operator" / "forge", _REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shim script")

# Built from parts: this file must not itself carry a credential-shaped literal.
TOKEN_FRAGMENT = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
SECRET_TEXT = "push the release with token ghp_" + TOKEN_FRAGMENT
REPORTED_MANAGER_MODEL = "claude-opus-5-5"


_SHIM = r'''#!__PY__
import json, os, sys
argv = sys.argv[1:]
stdin = sys.stdin.read()
log = os.environ["DX_SHIM_LOG"]
manager = "--disallowedTools" in argv
with open(log, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"argv": argv, "stdin": stdin, "kind": "manager" if manager else "worker",
                         "env_names": sorted(os.environ),
                         "env_probe": {k: os.environ.get(k, "") for k in
                                       os.environ.get("DX_SHIM_PROBE", "").split(",") if k}}) + "\n")
calls = sum(1 for _ in open(log, encoding="utf-8"))
usage = {"input_tokens": 11, "output_tokens": 7,
         "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 300}
mode = os.environ.get("DX_SHIM_MODE", "acs")
if mode == "stream":
    print(json.dumps({"type": "system", "subtype": "init", "model": "claude-sonnet-5",
                      "session_id": "s1"}))
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                      "result": json.dumps({"status": "ok", "result": {}}), "usage": usage}))
    sys.exit(0)
if manager:
    if calls == 1:
        decision = {"decision": "DELEGATE", "reasoning": "split",
                    "subtasks": [{"id": "w1", "instructions": os.environ.get("DX_SUBTASK", "say hi"),
                                  "expected_output": {}, "success_criteria": "done"}]}
    else:
        decision = {"decision": "COMPLETE", "reasoning": "done",
                    "complete_artifacts": {"summary": "ok", "output_paths": [], "quality_score": 1.0}}
    text = json.dumps(decision)
    model_usage = {"__MODEL__": {"inputTokens": 11, "outputTokens": 7}}
else:
    text = json.dumps({"status": "success", "confidence": 0.9, "result": {"ok": True}})
    model_usage = {"claude-sonnet-5": {"inputTokens": 11, "outputTokens": 7}}
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text,
                  "usage": usage, "modelUsage": model_usage}))
'''


def _make_shim(tmp_path: Path) -> Path:
    shim = tmp_path / "claude"
    shim.write_text(_SHIM.replace("__PY__", sys.executable)
                    .replace("__MODEL__", REPORTED_MANAGER_MODEL), encoding="utf-8")
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    return shim


def _calls(tmp_path: Path) -> list[dict]:
    p = tmp_path / "shim.jsonl"
    if not p.is_file():
        return []
    return [json.loads(x) for x in p.read_text("utf-8").splitlines() if x.strip()]


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    home = tmp_path / "corvin"
    home.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    monkeypatch.setenv("DX_SHIM_LOG", str(tmp_path / "shim.jsonl"))
    monkeypatch.setenv("CORVIN_CLAUDE_BIN", str(_make_shim(tmp_path)))
    # Documented dual-env licence bypass of _assert_engine_licensed (test-only).
    monkeypatch.setenv("CORVIN_AGENTS_SKIP_LIVE", "1")
    monkeypatch.setenv("CORVIN_INTEGRATION_TEST", "1")
    import spawn_gates
    spawn_gates._l34_cache.clear()
    import house_rules
    monkeypatch.setattr(house_rules, "_house_rules_classifier",
                        lambda task, rules, auth, **kw: ("", 0.0, "test clear"))
    return home


def _chain(home: Path) -> list[dict]:
    p = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    if not p.is_file():
        return []
    return [json.loads(x) for x in p.read_text("utf-8").splitlines() if x.strip()]


def _spec(task: str) -> dict:
    return {
        "awp": "1.0.0",
        "workflow": {"name": "dx-review", "description": "delegated chat task", "version": "1.0.0"},
        "orchestration": {"engine": "delegation_loop", "delegation_loop": {
            "budget": {"max_loops": 3, "max_depth": 2, "max_total_workers": 4,
                       "max_wall_time": 120}}},
        "state": {"initial": {"task": task}},
    }


def _run_acs(spec: dict):
    import acs_runtime
    rt = acs_runtime.ACSRuntime(tenant_id="_default", manager_model="claude-sonnet-5",
                                worker_model="claude-sonnet-5", enable_gate_chain=False)
    return asyncio.run(rt.run(spec))


# ── DX-1: ACS L34 must classify the CONTENT handed to the manager / worker ─────────


def test_dx1_acs_manager_never_receives_a_credential_from_the_task(sandbox, tmp_path):
    """Positive control first: an ordinary task reaches the manager (the shim is called)."""
    ok = _run_acs(_spec("summarise the release notes"))
    assert _calls(tmp_path), f"positive control: the manager never ran ({ok.status}: {ok.error})"
    (tmp_path / "shim.jsonl").unlink()

    res = _run_acs(_spec(SECRET_TEXT))
    assert res.status == "failed", res
    assert _calls(tmp_path) == [], "a credential-bearing task was handed to the manager engine"
    blocked = [r for r in _chain(sandbox) if r.get("event_type") == "data_flow.blocked"]
    assert blocked, "the refusal left no data_flow.blocked record"
    raw = (sandbox / "tenants" / "_default" / "global" / "forge" / "audit.jsonl").read_text("utf-8")
    assert TOKEN_FRAGMENT not in raw


def test_dx1_acs_worker_never_receives_a_credential_written_by_the_manager(sandbox, tmp_path,
                                                                          monkeypatch):
    monkeypatch.setenv("DX_SUBTASK", "use this: " + SECRET_TEXT)
    _run_acs(_spec("prepare the release"))
    kinds = [c["kind"] for c in _calls(tmp_path)]
    assert "manager" in kinds, f"positive control: the manager never ran ({kinds})"
    workers = [c for c in _calls(tmp_path) if c["kind"] == "worker"]
    assert all(TOKEN_FRAGMENT not in c["stdin"] for c in workers), (
        "a manager-written subtask carrying a credential was spawned as a worker")
    assert any(r.get("event_type") == "acs.worker_l34_blocked" for r in _chain(sandbox))


# ── DX-3: the ACS manager span is priceable ─────────────────────────────────────────


def test_dx3_acs_manager_span_closes_on_reported_model_and_usage(sandbox, tmp_path):
    res = _run_acs(_spec("summarise the release notes"))
    mgr_calls = [c for c in _calls(tmp_path) if c["kind"] == "manager"]
    assert len(mgr_calls) >= 2, f"positive control: expected DELEGATE+COMPLETE ({res.status})"
    ends = [r["details"] for r in _chain(sandbox)
            if r.get("event_type") == "engine.span.end"
            and (r.get("details") or {}).get("role") == "manager"]
    assert len(ends) == 1, ends
    end = ends[0]
    assert end["model_id"] == REPORTED_MANAGER_MODEL, end
    n = len(mgr_calls)
    assert end["input_tokens"] == 11 * n and end["output_tokens"] == 7 * n, end
    assert end["cache_read_tokens"] == 1000 * n and end["cache_write_tokens"] == 300 * n, end


# ── DX-2: the inbound A2A worker gets no operator secrets ──────────────────────────


_OPERATOR_SECRETS = {
    "GITHUB_TOKEN": "gh-operator-secret-value",
    "CORVIN_WDAT_KEY": "wdat-operator-secret-value",
    "PGPASSWORD": "pg-operator-secret-value",
    "TELEGRAM_BOT_TOKEN": "tg-operator-secret-value",
    "SOME_SERVICE_API_KEY": "svc-operator-secret-value",
}


def test_dx2_a2a_worker_env_carries_no_operator_secret(sandbox, tmp_path, monkeypatch):
    import a2a_worker
    from agents.claude_code import ClaudeCodeEngine

    for k, v in _OPERATOR_SECRETS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("DX_SHIM_MODE", "stream")
    monkeypatch.setenv("DX_SHIM_PROBE", ",".join(_OPERATOR_SECRETS))
    shim = os.environ["CORVIN_CLAUDE_BIN"]
    home = Path(getattr(a2a_worker, "_CORVIN_HOME_SNAPSHOT_A2A", "/nonexistent"))
    for name in ("compute_quota.json", ".compute_quota.json.lock"):
        try:
            (home / "global" / "license" / name).unlink()
        except OSError:
            pass
    res = a2a_worker.spawn_a2a_worker(
        instruction="summarise the attached notes", origin_id="peer-dx2", task_id="t-dx2",
        persona="assistant", ttl_s=30,
        engine_factory=lambda: ClaudeCodeEngine(binary=shim))
    calls = _calls(tmp_path)
    assert calls, f"positive control: the engine never ran ({res.status}: {res.error})"
    leaked = {k: v for k, v in calls[-1]["env_probe"].items() if v}
    assert leaked == {}, f"operator secrets reached the remote-peer worker: {sorted(leaked)}"


# ── Refutation 2026-11-05: DX-1 must also hold for a tenant that HAS a policy ───────


def _write_tenant_policy(home: Path) -> None:
    """A tenant.corvin.yaml as the console writes one (no data_classification block):
    load_guard_for_tenant then builds the DEFAULT matrix guard, so the tenant is judged
    by a policy instead of the no-policy SECRET floor."""
    cfg = home / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("spec:\n  web_chat:\n    worker_engine: claude_code\n", encoding="utf-8")
    cfg.chmod(0o600)


def test_refute_dx1_manager_gate_classifies_content_with_a_policy(sandbox, tmp_path):
    _write_tenant_policy(sandbox)
    ok = _run_acs(_spec("summarise the release notes"))
    assert _calls(tmp_path), f"positive control: the manager never ran ({ok.status}: {ok.error})"
    (tmp_path / "shim.jsonl").unlink()

    res = _run_acs(_spec(SECRET_TEXT))
    assert res.status == "failed", res
    assert _calls(tmp_path) == [], "policy tenant: a credential-bearing task reached the manager"
    assert any(r.get("event_type") == "acs.manager_l34_blocked" for r in _chain(sandbox))


def test_refute_dx1_worker_gate_classifies_content_with_a_policy(sandbox, tmp_path, monkeypatch):
    _write_tenant_policy(sandbox)
    monkeypatch.setenv("DX_SUBTASK", "use this: " + SECRET_TEXT)
    _run_acs(_spec("prepare the release"))
    kinds = [c["kind"] for c in _calls(tmp_path)]
    assert "manager" in kinds, f"positive control: the manager never ran ({kinds})"
    workers = [c for c in _calls(tmp_path) if c["kind"] == "worker"]
    assert all(TOKEN_FRAGMENT not in c["stdin"] for c in workers), (
        "policy tenant: a manager-written credential was spawned as a worker")
    assert any(r.get("event_type") == "acs.worker_l34_blocked" for r in _chain(sandbox))


_HOOK_SHIM = r'''#!__PY__
import json, os, sys
sys.stdin.read()
# What a hook of the worker (path_gate, voice plugin) does: append to the tenant chain
# with the worker's OWN environment.
sys.path.insert(0, "__FORGE__")
from forge import paths, security_events
security_events.write_event(paths.tenant_audit_chain("_default"), "a2a.task_received",
                            details={"origin_id": "peer-anchor"})
with open(os.environ["DX_SHIM_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"anchor": os.environ.get("CORVIN_AUDIT_ANCHOR_KEY", "<unset>")}) + "\n")
print(json.dumps({"type": "system", "subtype": "init", "model": "claude-sonnet-5", "session_id": "s1"}))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "result": json.dumps({"status": "ok", "result": {}}),
                  "usage": {"input_tokens": 1, "output_tokens": 1}}))
'''


def test_refute_dx2_a2a_worker_keeps_the_audit_anchor_key_pointer(sandbox, tmp_path, monkeypatch):
    """CORVIN_AUDIT_ANCHOR_KEY is a PATH (security_events._anchor_key_path), not the key.
    Blanking it makes every audit write inside the worker (its hooks) MAC the tenant chain
    with a different key (the ~/.config default), so the host's next verify_chain fails."""
    import a2a_worker
    from agents.claude_code import ClaudeCodeEngine
    from forge import paths, security_events

    monkeypatch.setenv("HOME", str(tmp_path / "home"))   # the worker's default key path stays in tmp
    (tmp_path / "home").mkdir()
    # The host process caches its key; an earlier test's sandbox key must not be reused.
    monkeypatch.setattr(security_events, "_ANCHOR_KEY", None)
    monkeypatch.setattr(security_events, "_ANCHOR_KEY_LOADED", False)
    chain = paths.tenant_audit_chain("_default")
    security_events.write_event(chain, "a2a.task_received", details={"origin_id": "host"})
    ok0, probs0 = security_events.verify_chain(chain)
    assert ok0, f"positive control: host chain must verify before the worker ({probs0})"

    shim = tmp_path / "claude-hook"
    shim.write_text(_HOOK_SHIM.replace("__PY__", sys.executable)
                    .replace("__FORGE__", str(_REPO / "corvin_operator" / "forge")), encoding="utf-8")
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    home = Path(getattr(a2a_worker, "_CORVIN_HOME_SNAPSHOT_A2A", "/nonexistent"))
    for name in ("compute_quota.json", ".compute_quota.json.lock"):
        try:
            (home / "global" / "license" / name).unlink()
        except OSError:
            pass
    res = a2a_worker.spawn_a2a_worker(
        instruction="summarise the attached notes", origin_id="peer-anchor", task_id="t-anchor",
        persona="assistant", ttl_s=30, engine_factory=lambda: ClaudeCodeEngine(binary=str(shim)))
    calls = _calls(tmp_path)
    assert calls, f"positive control: the engine never ran ({res.status}: {res.error})"
    ok, probs = security_events.verify_chain(chain)
    assert ok, f"a worker-side audit write broke the host chain: {probs}"
    assert calls[-1]["anchor"] == os.environ["CORVIN_AUDIT_ANCHOR_KEY"], calls[-1]


@pytest.mark.parametrize("policy", [False, True])
def test_refute_dx1_legit_task_mentioning_password_still_runs(sandbox, tmp_path, policy):
    """Regression guard: naming a password / api key is not a credential."""
    if policy:
        _write_tenant_policy(sandbox)
    res = _run_acs(_spec("write docs on how users reset their password and rotate an api key"))
    kinds = [c["kind"] for c in _calls(tmp_path)]
    assert "manager" in kinds and "worker" in kinds, (res.status, res.error, kinds)
