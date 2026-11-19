"""Review 2026-11-19 — delegate findings A2A-6 / A2A-7 / A2A-8 / A2A-12.

Every test drives the real ``run_delegate`` (fake engines only — no CLI, no
cost) and, where a record is the claim, reads the real tenant chain.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "core" / "delegate", _REPO / "corvin_operator" / "bridges" / "shared",
           _REPO / "corvin_operator" / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from agents import StreamEvent  # type: ignore  # noqa: E402

from corvin_delegate import delegation, sandbox  # noqa: E402
from corvin_delegate.delegation import DelegateError, run_delegate  # noqa: E402

REPORTED = "claude-sonnet-5"
USAGE = {"input_tokens": 5, "output_tokens": 9,
         "cache_read_input_tokens": 400, "cache_creation_input_tokens": 60}


class _Engine:
    name = "fake"

    def __init__(self, *, caps=None, error=None, usage=None, model=REPORTED, swallow=True):
        self.capabilities = dict(caps or {})
        self._error, self._usage, self._model = error, usage, model
        self.spawn_kwargs: "dict | None" = None

    def spawn(self, prompt, **kw):  # type: ignore[no-untyped-def]
        self.spawn_kwargs = dict(kw)
        yield StreamEvent(type="session_started",
                          raw={"type": "system", "subtype": "init", "model": self._model})
        yield StreamEvent(type="text_delta", text="partial")
        if self._error:
            yield StreamEvent(type="error", error=self._error)
        yield StreamEvent(type="turn_completed", usage=self._usage or {})

    def cancel(self):  # pragma: no cover
        pass


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "corvin"
    h.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(h))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.delenv("CORVIN_SESSION_DIR", raising=False)
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.delenv("CORVIN_DELEGATE_SANDBOX_FLOOR", raising=False)
    return h


def _records(home: Path, tenant: str = "_default") -> list[dict]:
    chain = home / "tenants" / tenant / "global" / "forge" / "audit.jsonl"
    if not chain.is_file():
        return []
    return [json.loads(l) for l in chain.read_text("utf-8").splitlines() if l.strip()]


def _events(home: Path, name: str, tenant: str = "_default") -> list[dict]:
    return [r["details"] for r in _records(home, tenant) if r.get("event_type") == name]


def _run(eng, **kw):
    kw.setdefault("engine", "codex_cli")
    kw.setdefault("prompt", "summarise the release notes")
    return run_delegate(engine_factory=lambda _e: eng, **kw)


# ── A2A-6: L34 is the shared gate, fail-closed, tenant explicit ──────────────


def test_l34_goes_through_the_shared_gate_with_the_callers_tenant(home, monkeypatch):
    import spawn_gates
    seen = {}

    def _fake(engine_id, tenant_id, **kw):
        seen.update(engine=engine_id, tenant=tenant_id, **kw)
        return None

    monkeypatch.setattr(spawn_gates, "check_l34", _fake)
    monkeypatch.setenv("CORVIN_TENANT_ID", "env-tenant")
    eng = _Engine()
    res = _run(eng, tenant_id="acme", audit=False)
    assert res.ok
    assert seen["engine"] == "codex_cli"
    assert seen["tenant"] == "acme"          # the caller's, not the env's
    assert seen["prompt"] == "summarise the release notes"


def test_l34_refusal_denies_before_any_spawn(home, monkeypatch):
    import spawn_gates
    monkeypatch.setattr(spawn_gates, "check_l34",
                        lambda *a, **k: "[data-flow] Spawn rejected: SECRET")
    eng = _Engine()
    res = _run(eng, audit=False)
    assert res.ok is False and (res.error or "").startswith("data-flow-denied:")
    assert eng.spawn_kwargs is None


def test_l34_gate_error_is_a_denial_not_a_pass(home, monkeypatch):
    import spawn_gates

    def _boom(*a, **k):
        raise RuntimeError("classifier down")

    monkeypatch.setattr(spawn_gates, "check_l34", _boom)
    eng = _Engine()
    res = _run(eng, audit=False)
    assert res.ok is False
    assert "data-flow-denied" in (res.error or "") and "fail-closed" in (res.error or "")
    assert eng.spawn_kwargs is None


def test_l34_unimportable_gate_is_a_denial(home, monkeypatch):
    monkeypatch.setitem(sys.modules, "spawn_gates", None)   # import raises ImportError
    eng = _Engine()
    res = _run(eng, audit=False)
    assert res.ok is False and "data-flow-denied" in (res.error or "")
    assert eng.spawn_kwargs is None


def test_l34_real_gate_refuses_a_secret_without_any_policy(home):
    # The real spawn_gates.check_l34 + real classifier: the T-0254 SECRET floor.
    eng = _Engine()
    res = _run(eng, audit=False,
               prompt="deploy with AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY "
                      "and sk-ant-api03-" + "A" * 80)
    assert res.ok is False and "data-flow-denied" in (res.error or ""), res
    assert eng.spawn_kwargs is None
    assert _events(home, "data_flow.blocked")
    # Positive control: a harmless prompt passes the same real gate.
    assert _run(_Engine(), audit=False).ok


# ── A2A-7: sandbox applied or refused, never claimed ────────────────────────


def test_enforcing_refuses_an_engine_without_prefix_support(home, monkeypatch):
    monkeypatch.setattr(sandbox, "is_bwrap_available", lambda: True)
    eng = _Engine(caps={})                       # swallows **kw, no capability
    res = _run(eng, sandbox_mode="enforcing")
    assert res.ok is False and "sandbox-denied" in (res.error or "")
    assert eng.spawn_kwargs is None              # never ran unsandboxed
    assert _events(home, "delegate.sandboxed") == []
    unavailable = _events(home, "delegate.sandbox_unavailable")
    assert [u["reason"] for u in unavailable] == ["engine-no-argv-prefix"]


def test_advisory_runs_native_and_says_so(home, monkeypatch):
    monkeypatch.setattr(sandbox, "is_bwrap_available", lambda: True)
    eng = _Engine(caps={})
    res = _run(eng, sandbox_mode="advisory")
    assert res.ok
    assert "argv_prefix" not in (eng.spawn_kwargs or {})
    assert _events(home, "delegate.sandboxed") == []     # no false claim
    assert [u["reason"] for u in _events(home, "delegate.sandbox_unavailable")] == [
        "engine-no-argv-prefix-fallback-native"]


def test_sandboxed_is_recorded_only_after_the_prefix_reached_the_engine(home, monkeypatch):
    monkeypatch.setattr(sandbox, "is_bwrap_available", lambda: True)
    eng = _Engine(caps={"argv_prefix": True})
    res = _run(eng, sandbox_mode="enforcing")
    assert res.ok
    prefix = (eng.spawn_kwargs or {}).get("argv_prefix")
    assert prefix and "--unshare-pid" in prefix
    assert len(_events(home, "delegate.sandboxed")) == 1
    names = [r["event_type"] for r in _records(home)]
    assert names.index("delegate.sandboxed") > names.index("delegate.invoked")


def test_enforcing_refuses_when_there_is_no_directory_to_bind(home, monkeypatch):
    monkeypatch.setattr(sandbox, "is_bwrap_available", lambda: True)
    eng = _Engine(caps={"argv_prefix": True})
    res = _run(eng, sandbox_mode="enforcing", hermetic=False)
    assert res.ok is False and "no-working-dir-to-bind" in (res.error or "")
    assert eng.spawn_kwargs is None
    assert _events(home, "delegate.sandboxed") == []


@pytest.mark.parametrize("engine_mod, cls", [
    ("agents.claude_code", "ClaudeCodeEngine"),
    ("agents.codex_cli", "CodexCliEngine"),
    ("agents.opencode_cli", "OpenCodeEngine"),
])
def test_real_cli_engines_declare_and_accept_argv_prefix(engine_mod, cls):
    import importlib
    import inspect
    engine_cls = getattr(importlib.import_module(engine_mod), cls)
    assert engine_cls.capabilities.get("argv_prefix") is True
    assert "argv_prefix" in inspect.signature(engine_cls.spawn).parameters


def test_claude_engine_really_prepends_the_prefix_to_the_spawned_argv(tmp_path, monkeypatch):
    """Through the real ClaudeCodeEngine and a real subprocess: the launcher
    (a recording stand-in for bwrap) is what gets executed, with the CLI argv after it."""
    from agents import collect
    from agents.claude_code import ClaudeCodeEngine
    if os.name != "posix":
        pytest.skip("POSIX launcher script")
    log = tmp_path / "launcher.log"
    launcher = tmp_path / "launcher"
    launcher.write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" > {log}\nexit 0\n', "utf-8")
    launcher.chmod(0o700)
    monkeypatch.delenv("ADAPTER_FAKE_CLAUDE", raising=False)
    eng = ClaudeCodeEngine(binary="/nonexistent/claude-binary")
    collect(eng.spawn("hello", timeout=10.0, working_dir=tmp_path,
                      argv_prefix=[str(launcher), "--marker"]))
    argv = log.read_text("utf-8").splitlines()
    assert argv[0] == "--marker"
    assert "/nonexistent/claude-binary" in argv[1]
    assert "-p" in argv


@pytest.mark.parametrize("bad", ["/", "//", "/..", "/tmp/.."])
def test_a_filesystem_root_is_refused_as_working_dir(home, bad):
    eng = _Engine()
    with pytest.raises(DelegateError, match="filesystem root"):
        _run(eng, working_dir=bad, audit=False)
    assert eng.spawn_kwargs is None


def test_a_symlink_to_root_is_refused_as_working_dir(home, tmp_path):
    link = tmp_path / "rootlink"
    link.symlink_to("/")
    with pytest.raises(DelegateError, match="filesystem root"):
        _run(_Engine(), working_dir=str(link), audit=False)


def test_an_ordinary_working_dir_still_works(home, tmp_path):
    eng = _Engine()
    ws = tmp_path / "ws"
    ws.mkdir()
    assert _run(eng, working_dir=str(ws), audit=False).ok
    assert str(eng.spawn_kwargs["working_dir"]) == str(ws.resolve())


# ── A2A-8 / A2A-12: one priceable carrier, also for failed runs ─────────────


def _worker_ends(home):
    return [d for d in _events(home, "engine.span.end") if d.get("role") == "worker"]


def test_a_failed_run_still_leaves_a_priceable_span(home):
    eng = _Engine(error="claude stream timeout", usage=USAGE)
    res = _run(eng, engine="claude_code")
    assert res.ok is False
    ends = _worker_ends(home)
    assert len(ends) == 1, ends
    end = ends[0]
    assert end["status"] == "error"
    assert end["model_id"] == REPORTED
    assert (end["input_tokens"], end["output_tokens"],
            end["cache_read_tokens"], end["cache_write_tokens"]) == (5, 9, 400, 60)


def test_a_failed_run_without_a_reported_model_is_unpriced_not_guessed(home):
    eng = _Engine(error="boom", usage={}, model="")
    _run(eng, engine="claude_code", model="claude-opus-5")
    (end,) = _worker_ends(home)
    assert end["model_id"] == "" and end["status"] == "error"


def test_model_id_is_the_attested_model_never_the_engine_name(home, tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_SESSION_DIR", str(tmp_path / "session"))
    (tmp_path / "session").mkdir()
    eng = _Engine(usage=USAGE, model="")          # the engine reports NO model
    assert _run(eng, engine="codex_cli", model=None).ok
    for name in ("acs.worker_spawned", "acs.engine_completed", "engine.span.start",
                 "engine.span.end"):
        (rec,) = _events(home, name)
        assert rec.get("model_id", "") == "", (name, rec)
        assert rec.get("model_id") != "codex_cli"


def test_the_token_split_has_exactly_one_carrier(home, tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_SESSION_DIR", str(tmp_path / "session"))
    (tmp_path / "session").mkdir()
    assert _run(_Engine(usage=USAGE), engine="claude_code").ok
    (completed,) = _events(home, "acs.engine_completed")
    for key in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                "cache_read_input_tokens", "cache_read_tokens", "cache_write_tokens"):
        assert key not in completed, key
    (end,) = _worker_ends(home)
    assert (end["input_tokens"], end["cache_read_tokens"]) == (5, 400)
    assert end["model_id"] == REPORTED


def test_the_span_lands_on_the_callers_tenant_chain(home, monkeypatch):
    monkeypatch.setenv("CORVIN_TENANT_ID", "env-tenant")
    import spawn_gates
    monkeypatch.setattr(spawn_gates, "check_l34", lambda *a, **k: None)
    assert _run(_Engine(usage=USAGE), engine="claude_code", tenant_id="acme", audit=False).ok
    assert len([d for d in _events(home, "engine.span.end", "acme")]) == 1
    assert _events(home, "engine.span.end", "env-tenant") == []


# ── R2-A2A-7: working_dir is a positive rule, the resolved path is used ──────


def _refused(path, match=None):
    eng = _Engine()
    with pytest.raises(DelegateError, match=match):
        _run(eng, working_dir=str(path), audit=False)
    assert eng.spawn_kwargs is None, "the engine must not have been spawned"


@pytest.mark.skipif(os.name != "posix", reason="POSIX system directories")
@pytest.mark.parametrize("path", ["/etc", "/usr", "/usr/local/lib", "/var/lib", "/home", "/opt"])
def test_system_directories_are_refused(home, path):
    _refused(path)


def test_the_users_home_and_its_credential_dirs_are_refused(home, tmp_path, monkeypatch):
    user = tmp_path / "userhome"
    for sub in (".ssh", ".claude", ".config/gh", ".aws", "projects/app"):
        (user / sub).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("USERPROFILE", str(user))
    _refused(user, "contains a protected directory")          # holds ~/.ssh
    _refused(user / ".ssh", "inside a protected directory")
    _refused(user / ".claude", "inside a protected directory")
    _refused(user / ".config" / "gh", "inside a protected directory")
    _refused(user / ".aws", "inside a protected directory")
    # Positive control: a project below the home is a workspace.
    eng = _Engine()
    assert _run(eng, working_dir=str(user / "projects" / "app"), audit=False).ok
    assert eng.spawn_kwargs["working_dir"] == (user / "projects" / "app").resolve()


def test_the_runtime_root_and_the_audit_chain_dir_are_refused(home, tmp_path):
    chain_dir = home / "tenants" / "_default" / "global" / "forge"
    chain_dir.mkdir(parents=True)
    _refused(home, "inside a protected directory")
    _refused(chain_dir, "inside a protected directory")
    _refused(home / "tenants" / "_default" / "global", "inside a protected directory")
    _refused(home / "tenants" / "_default" / "sessions")      # the root of all sessions
    _refused(tmp_path, "contains a protected directory")      # parent of the runtime root


def test_session_and_acs_run_directories_inside_the_runtime_root_are_workspaces(home):
    sess = home / "tenants" / "_default" / "sessions" / "web" / "chat1"
    run = home / "tenants" / "_default" / "global" / "acs" / "runs" / "r1" / "output"
    for d in (sess, run):
        d.mkdir(parents=True)
        eng = _Engine()
        assert _run(eng, working_dir=str(d), audit=False).ok, d
        assert eng.spawn_kwargs["working_dir"] == d.resolve()


def test_a_path_outside_every_workspace_root_is_refused(home, tmp_path, monkeypatch):
    # Neither home, temp, session nor operator root: make the temp root and
    # the home point elsewhere so tmp_path is "somewhere else on the disk".
    other = tmp_path / "elsewhere" / "proj"
    other.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "nohome"))
    monkeypatch.setattr(delegation.tempfile, "gettempdir", lambda: str(tmp_path / "notmp"))
    _refused(other, "not under a workspace root")
    # The operator declares it: accepted.
    monkeypatch.setenv(delegation.WORKSPACE_ROOTS_ENV, str(tmp_path / "elsewhere"))
    eng = _Engine()
    assert _run(eng, working_dir=str(other), audit=False).ok


def test_the_shared_temp_root_itself_is_not_a_workspace(home, tmp_path, monkeypatch):
    fake_tmp = tmp_path / "faketmp"
    (fake_tmp / "job").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "nohome"))
    monkeypatch.setattr(delegation.tempfile, "gettempdir", lambda: str(fake_tmp))
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin"))
    _refused(fake_tmp, "not under a workspace root")
    assert _run(_Engine(), working_dir=str(fake_tmp / "job"), audit=False).ok


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlinks")
def test_a_symlink_is_resolved_once_and_the_resolved_path_is_what_spawns(home, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    eng = _Engine()
    assert _run(eng, working_dir=str(link), audit=False).ok
    # The engine gets the TARGET: re-pointing the link afterwards changes nothing.
    assert eng.spawn_kwargs["working_dir"] == real.resolve()
    # A link into a protected directory is judged by its target.
    bad = tmp_path / "bad"
    bad.symlink_to(home)
    _refused(bad, "inside a protected directory")


# ── R2-A2A-10: delegate.sandboxed only for a sandbox that started ────────────


class _RaisingEngine(_Engine):
    def spawn(self, prompt, **kw):  # type: ignore[no-untyped-def]
        self.spawn_kwargs = dict(kw)
        raise OSError("exec format error")
        yield  # pragma: no cover


def test_a_spawn_that_raises_is_not_recorded_as_sandboxed(home, monkeypatch):
    monkeypatch.setattr(sandbox, "is_bwrap_available", lambda: True)
    eng = _RaisingEngine(caps={"argv_prefix": True})
    res = _run(eng, sandbox_mode="enforcing")
    assert res.ok is False and "engine-spawn-failed" in (res.error or "")
    assert (eng.spawn_kwargs or {}).get("argv_prefix"), "the prefix was built and handed over"
    assert _events(home, "delegate.sandboxed") == [], "no sandbox ran"
    assert [u["reason"] for u in _events(home, "delegate.sandbox_unavailable")] == [
        "engine-spawn-failed"]
    assert len(_events(home, "delegate.failed")) == 1


def test_positive_control_a_completed_sandboxed_run_is_recorded_once(home, monkeypatch):
    monkeypatch.setattr(sandbox, "is_bwrap_available", lambda: True)
    res = _run(_Engine(caps={"argv_prefix": True}), sandbox_mode="enforcing")
    assert res.ok
    assert len(_events(home, "delegate.sandboxed")) == 1
    assert _events(home, "delegate.sandbox_unavailable") == []


# ── R2-A2A-12: a planted config target refuses the delegation ────────────────


def _force_mcp_specs(monkeypatch):
    from corvin_delegate import mcp_config_builder as mcb
    spec = mcb.McpServerSpec(name="forge", command="python3", args=["-m", "x"], env={})
    monkeypatch.setattr(mcb, "build_mcp_specs", lambda **kw: [spec])
    monkeypatch.setattr(mcb, "env_floor_forge_enabled", lambda: None)
    return mcb


@pytest.mark.skipif(os.name != "posix", reason="POSIX hard links")
def test_an_unsafe_config_target_refuses_the_delegation_and_is_audited(home, tmp_path, monkeypatch):
    _force_mcp_specs(monkeypatch)
    victim = tmp_path / "victim.json"
    victim.write_text('{"mcp": {"evil": {}}}', "utf-8")
    wd = tmp_path / "wd"
    wd.mkdir()
    os.link(victim, wd / "opencode.json")       # planted: not a plain private file
    eng = _Engine()
    res = _run(eng, engine="opencode", working_dir=str(wd), forge_enabled=True)
    assert res.ok is False
    assert "unsafe-config-target" in (res.error or "")
    assert eng.spawn_kwargs is None, "the worker must not start beside a planted config"
    assert victim.read_text("utf-8") == '{"mcp": {"evil": {}}}'
    failed = _events(home, "delegate.failed")
    assert [f["reason"] for f in failed] == ["unsafe-config-target"]
    assert _events(home, "delegate.completed") == []


def test_positive_control_a_clean_dir_gets_its_config_and_runs(home, tmp_path, monkeypatch):
    _force_mcp_specs(monkeypatch)
    wd = tmp_path / "wd"
    wd.mkdir()
    eng = _Engine()
    res = _run(eng, engine="opencode", working_dir=str(wd), forge_enabled=True)
    assert res.ok, res.error
    assert "forge" in (wd / "opencode.json").read_text("utf-8")
    assert _events(home, "delegate.failed") == []


# ── R2-A2A-1: a console delegation is ONE priced worker run ──────────────────


def test_a_console_delegation_is_counted_and_priced_once(home, tmp_path, monkeypatch):
    sys.path.insert(0, str(_REPO)) if str(_REPO) not in sys.path else None
    from core.learning import model_selection_learner as msl
    sess = home / "tenants" / "_default" / "sessions" / "web" / "c1"
    sess.mkdir(parents=True)
    monkeypatch.setenv("CORVIN_SESSION_DIR", str(sess))   # -> acs.* run records too
    usage = {"input_tokens": 1000, "output_tokens": 500}
    res = _run(_Engine(usage=usage, model="claude-haiku-4-5"), engine="claude_code")
    assert res.ok, res.error
    # Positive control: BOTH records exist for the one run.
    assert len(_events(home, "acs.engine_completed")) == 1
    assert len(_worker_ends(home)) == 1
    chain = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    out = msl.compute_cost_efficiency("_default", chain, since=0.0)
    assert sum(p.total_turns for p in out.acs_daily) == 1
    assert sum(p.counted_turns for p in out.acs_daily) == 1
    one = msl._price_and_baseline("claude-haiku-4-5", 1000, 500, 0, 0, reference=None)[0]
    assert out.acs_total_actual_usd == pytest.approx(one, abs=1e-4)
