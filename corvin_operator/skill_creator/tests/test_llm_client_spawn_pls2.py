"""Review 2026-11-19, PLS-2 (a-c): what a Skill-Creator generation really spawns.

A REAL child process is started for every test - a recording stand-in for the ``claude``
binary that writes its argv and its whole environment to a file and prints a CLI envelope.
Nothing in ``ClaudeCodeClient`` is mocked, so these tests see what the engine would see:

* (b) the child gets the deny-by-default worker environment, not the console's;
* (a) every call is an ``engine.span.start`` / ``engine.span.end`` pair on the caller's
  tenant chain, closed on the model the CLI REPORTED with the four-way token split;
* (c) the requested model follows the lineage (Console COMPLEX choice, else newest Opus)
  instead of a hardcoded version, and a pin reaches ``--model`` in the form the CLI accepts.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

_OPERATOR_DIR = Path(__file__).resolve().parents[2]
for _p in (str(_OPERATOR_DIR), str(_OPERATOR_DIR / "bridges" / "shared"), str(_OPERATOR_DIR / "forge")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from skill_creator import llm_client as lc  # noqa: E402

_SHIM = r'''#!{python}
import json, os, sys
with open(os.environ["FAKE_CLAUDE_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "env": dict(os.environ), "cwd": os.getcwd()}}) + "\n")
plan_path = os.environ["FAKE_CLAUDE_LOG"] + ".plan"
plan = json.load(open(plan_path, encoding="utf-8")) if os.path.exists(plan_path) else {{}}
if "stdout" in plan:
    sys.stdout.write(plan["stdout"])
else:
    model = sys.argv[sys.argv.index("--model") + 1]
    sys.stdout.write(json.dumps({{
        "type": "result", "subtype": "success", "is_error": False, "result": "PONG",
        "usage": {{"input_tokens": 11, "output_tokens": 7,
                  "cache_read_input_tokens": 100, "cache_creation_input_tokens": 20}},
        "modelUsage": plan.get("modelUsage", {{model + "-20260101": {{"outputTokens": 7}}}}),
    }}))
sys.stderr.write(plan.get("stderr", ""))
sys.exit(int(plan.get("exit_code", 0)))
'''

SECRETS = {
    "DISCORD_BOT_TOKEN": "discord-secret-value",
    "TELEGRAM_BOT_TOKEN": "telegram-secret-value",
    "MY_SERVICE_API_KEY": "api-key-secret-value",
    "DB_PASSWORD": "password-secret-value",
    "AWS_SECRET_ACCESS_KEY": "aws-secret-value",
    "CORVIN_CONSOLE_SESSION_SECRET": "console-secret-value",
}


class FakeClaude:
    def __init__(self, tmp_path: Path) -> None:
        self.log = tmp_path / "fake-claude.jsonl"
        self.binary = tmp_path / "bin" / "claude"
        self.binary.parent.mkdir()
        self.binary.write_text(_SHIM.format(python=sys.executable), encoding="utf-8")
        self.binary.chmod(self.binary.stat().st_mode | stat.S_IXUSR)

    def plan(self, **plan) -> None:
        Path(str(self.log) + ".plan").write_text(json.dumps(plan), encoding="utf-8")

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines() if x.strip()]


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A tenant home of its own, an operator environment full of secrets and a fake claude."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.delenv(lc.MODEL_ENV, raising=False)
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    # The log path is not a secret and must reach the child for the recording to work at all:
    # it is on the base allowlist only through the test's own keep list.
    fake = FakeClaude(tmp_path)
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(fake.log))
    import acs_runtime
    real_build = acs_runtime.build_worker_env
    monkeypatch.setattr(
        acs_runtime, "build_worker_env",
        lambda tenant_id, **kw: real_build(tenant_id, **{**kw, "extra": {
            **(kw.get("extra") or {}), "FAKE_CLAUDE_LOG": str(fake.log)}}))
    # The live model-usage signal is a network send; a test never makes one.
    import engine_span
    monkeypatch.setattr(engine_span, "send_live_model_usage", lambda details: None)
    return fake


def _client(fake: FakeClaude, **kw) -> lc.ClaudeCodeClient:
    return lc.ClaudeCodeClient(binary=str(fake.binary), **kw)


def _ask(client, **kw):
    return client.messages.create(messages=[{"role": "user", "content": "say PONG"}], **kw)


def _spans(tenant_id: str = "_default") -> list[dict]:
    import forge.paths as fp
    path = Path(fp.tenant_audit_chain(tenant_id))
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if str(rec.get("event_type", "")).startswith("engine.span."):
            out.append(rec)
    return out


# ── (b) the child environment ────────────────────────────────────────────────

def test_the_child_never_sees_the_operators_secrets(sandbox):
    resp = _ask(_client(sandbox))
    assert resp.text == "PONG"
    (call,) = sandbox.calls()
    env = call["env"]
    leaked = {k for k in SECRETS if k in env} | {k for k, v in env.items() if v in SECRETS.values()}
    assert not leaked, f"operator secrets reached the generation child: {sorted(leaked)}"
    assert env.get("PATH"), "the worker env keeps what a process needs to run"
    assert env["CORVIN_TENANT_ID"] == "_default"


def test_the_env_is_the_shared_worker_rule_not_a_private_copy(sandbox, monkeypatch):
    """Call-site proof: the spawn's env is what ``acs_runtime.build_worker_env`` returned."""
    import acs_runtime
    seen: list[str] = []
    marker = {"PATH": os.environ["PATH"], "ONLY_FROM_THE_BUILDER": "1",
              "FAKE_CLAUDE_LOG": str(sandbox.log)}
    monkeypatch.setattr(acs_runtime, "build_worker_env",
                        lambda tenant_id, **kw: seen.append(tenant_id) or dict(marker))
    _ask(_client(sandbox, tenant_id="_default"))
    assert seen == ["_default"]
    env = sandbox.calls()[0]["env"]
    assert env["ONLY_FROM_THE_BUILDER"] == "1"
    assert "HOME" not in env or env.get("HOME") == marker.get("HOME"), "nothing is merged in from os.environ"


def test_an_unavailable_env_builder_refuses_the_spawn(sandbox, monkeypatch):
    """Fail-closed: no fallback to the full environment."""
    monkeypatch.setitem(sys.modules, "acs_runtime", None)   # import acs_runtime -> ImportError
    with pytest.raises(lc.ClaudeCodeUnavailable, match="refusing to spawn"):
        _ask(_client(sandbox))
    assert sandbox.calls() == [], "nothing was spawned"
    assert _spans() == [], "a refused spawn opens no span"


def test_the_child_runs_outside_the_callers_directory(sandbox):
    _ask(_client(sandbox))
    assert "skill-creator-" in sandbox.calls()[0]["cwd"]


# ── (a) the span ─────────────────────────────────────────────────────────────

def test_one_call_is_one_priceable_span_on_the_tenant_chain(sandbox):
    client = _client(sandbox)
    resp = _ask(client)
    requested = sandbox.calls()[0]["argv"]
    requested = requested[requested.index("--model") + 1]

    start, end = _spans()
    assert (start["event_type"], end["event_type"]) == ("engine.span.start", "engine.span.end")
    s, e = start["details"], end["details"]
    assert s["span_id"] == e["span_id"] and s["span_id"].startswith("spn_")
    assert s["role"] == e["role"] == "worker"
    assert s["engine_id"] == e["engine_id"] == "claude_code"
    assert s["model_id"] == requested, "the start names what was asked for"
    assert e["model_id"] == requested + "-20260101", "the end names what the CLI REPORTED"
    assert e["status"] == "ok"
    assert (e["input_tokens"], e["output_tokens"], e["cache_read_tokens"], e["cache_write_tokens"]) \
        == (11, 7, 100, 20)
    assert resp.reported_model == requested + "-20260101"


def test_the_split_is_the_shared_normalisers(sandbox):
    import engine_span
    _ask(_client(sandbox))
    end = _spans()[-1]["details"]
    expected = engine_span.usage_split({"input_tokens": 11, "output_tokens": 7,
                                        "cache_read_input_tokens": 100,
                                        "cache_creation_input_tokens": 20})
    assert {k: end[k] for k in expected} == expected


def test_a_cli_that_reports_no_model_leaves_the_span_unpriced(sandbox):
    """Never the requested id: a guessed model prices the run at the wrong rate."""
    sandbox.plan(stdout=json.dumps({"type": "result", "subtype": "success", "is_error": False,
                                    "result": "PONG", "usage": {"input_tokens": 3, "output_tokens": 2}}))
    resp = _ask(_client(sandbox))
    end = _spans()[-1]["details"]
    assert end["model_id"] == ""
    assert end["status"] == "ok" and end["output_tokens"] == 2
    assert resp.reported_model == ""
    assert resp.model, "the SDK-shaped field keeps its fallback"


def test_the_model_that_answered_wins_over_a_helper_model(sandbox):
    sandbox.plan(modelUsage={"claude-haiku-4-5-20251001": {"outputTokens": 1},
                             "claude-opus-5-5-20260301": {"outputTokens": 900}})
    _ask(_client(sandbox))
    assert _spans()[-1]["details"]["model_id"] == "claude-opus-5-5-20260301"


@pytest.mark.parametrize("plan,match", [
    ({"exit_code": 3, "stderr": "boom"}, "exited 3"),
    ({"stdout": json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True,
                            "result": "hit the limit", "usage": {"input_tokens": 5, "output_tokens": 1},
                            "modelUsage": {"claude-opus-5-5": {"outputTokens": 1}}})}, "error_max_turns"),
    ({"stdout": ""}, "empty output"),
])
def test_a_failed_call_still_closes_its_span_as_an_error(sandbox, plan, match):
    sandbox.plan(**plan)
    with pytest.raises(lc.ClaudeCodeUnavailable, match=match):
        _ask(_client(sandbox))
    start, end = _spans()
    assert start["details"]["span_id"] == end["details"]["span_id"]
    assert end["details"]["status"] == "error"
    if "error_max_turns" in match:
        # billed although it failed: the span carries what the envelope reports
        assert end["details"]["model_id"] == "claude-opus-5-5"
        assert end["details"]["input_tokens"] == 5


def test_the_span_lands_on_the_callers_tenant_not_the_process_tenant(sandbox):
    _ask(_client(sandbox, tenant_id="acme"))
    assert _spans("_default") == []
    assert [s["event_type"] for s in _spans("acme")] == ["engine.span.start", "engine.span.end"]
    assert sandbox.calls()[0]["env"]["CORVIN_TENANT_ID"] == "acme"


def test_a_span_that_cannot_be_written_does_not_break_the_generation(sandbox, monkeypatch):
    monkeypatch.setattr(lc, "_span_writer", lambda tenant_id: (_ for _ in ()).throw(OSError("disk")))
    assert _ask(_client(sandbox)).text == "PONG"


# ── (c) the model ────────────────────────────────────────────────────────────

def _requested(fake: FakeClaude) -> str:
    argv = fake.calls()[-1]["argv"]
    return argv[argv.index("--model") + 1]


def test_no_pin_follows_the_lineage_not_the_literal(sandbox, monkeypatch):
    import model_selector as ms
    assert _client(sandbox).model == ms.top_model("_default")
    monkeypatch.setattr(ms, "top_model", lambda tenant_id=None: "claude-opus-9-9")
    client = _client(sandbox)
    _ask(client)                                   # no model argument
    assert _requested(sandbox) == "claude-opus-9-9"
    _ask(client, model=lc.DEFAULT_MODEL)           # the phase code's legacy literal
    assert _requested(sandbox) == "claude-opus-9-9", "DEFAULT_MODEL means 'top tier', not this version"


def test_the_default_is_the_newest_opus_of_the_selector(sandbox):
    import model_selector as ms
    assert lc.default_model("_default") == (
        ms.normalise_pin(ms.top_model("_default"), "claude_code") or ms.top_model("_default"))
    assert "opus" in lc.default_model("_default")


def test_an_env_pin_wins_and_loses_its_provider_prefix(sandbox, monkeypatch):
    monkeypatch.setenv(lc.MODEL_ENV, "anthropic/claude-sonnet-5")
    client = _client(sandbox)
    _ask(client, model=lc.DEFAULT_MODEL)
    assert _requested(sandbox) == "claude-sonnet-5"
    assert "/" not in _requested(sandbox)


def test_an_explicit_other_model_is_the_callers_choice(sandbox):
    _ask(_client(sandbox), model="anthropic/claude-haiku-4-5")
    assert not _requested(sandbox).startswith("anthropic/")
    assert "haiku" in _requested(sandbox)


def test_a_missing_selector_falls_back_to_the_floor(sandbox, monkeypatch):
    monkeypatch.setitem(sys.modules, "model_selector", None)
    assert lc.default_model("_default") == lc.DEFAULT_MODEL


# ── the factory binds the tenant ─────────────────────────────────────────────

def test_resolve_llm_client_binds_the_tenant(sandbox, monkeypatch):
    monkeypatch.setattr(lc, "resolve_claude_bin", lambda explicit=None: str(sandbox.binary))
    monkeypatch.delenv(lc.ENGINE_ENV, raising=False)
    client = lc.resolve_llm_client(tenant_id="acme")
    assert isinstance(client, lc.ClaudeCodeClient) and client.tenant_id == "acme"
    assert lc.resolve_llm_client().tenant_id == "_default"


def test_the_orchestrators_pass_their_tenant(sandbox, monkeypatch, tmp_path):
    """Call-site proof for the three creators: each resolves its client FOR its tenant."""
    monkeypatch.setattr(lc, "resolve_claude_bin", lambda explicit=None: str(sandbox.binary))
    monkeypatch.delenv(lc.ENGINE_ENV, raising=False)
    from skill_creator.skill_creator import SkillCreatorOrchestrator
    root = tmp_path / "home" / "tenants" / "acme" / "skill-forge"
    root.mkdir(parents=True)
    assert SkillCreatorOrchestrator(registry_root=str(root)).client.tenant_id == "acme"
    from skill_creator.tool_creator import ToolCreatorOrchestrator
    from skill_creator.plugin_creator import PluginCreatorOrchestrator
    assert ToolCreatorOrchestrator(tenant_id="acme").client.tenant_id == "acme"
    assert PluginCreatorOrchestrator(tenant_id="acme").client.tenant_id == "acme"


# ── R2-BR-9: the per-spawn gate ──────────────────────────────────────────────

def test_the_gate_runs_on_each_calls_text_before_the_child_exists(sandbox):
    seen: list[tuple[int, str]] = []
    client = _client(sandbox, spawn_gate=lambda text: seen.append((len(sandbox.calls()), text)))
    for word in ("one", "two", "three"):
        client.messages.create(messages=[{"role": "user", "content": f"say {word}"}],
                               system=f"system for {word}")
    assert len(sandbox.calls()) == 3
    assert [n for n, _ in seen] == [0, 1, 2], "each gate pass came before its own spawn"
    assert all(f"say {w}" in t and f"system for {w}" in t
               for (_, t), w in zip(seen, ("one", "two", "three")))


def test_a_refusing_gate_spawns_nothing_and_writes_no_span(sandbox):
    class Refused(RuntimeError):
        pass

    def _gate(_text):
        raise Refused("not permitted")

    client = _client(sandbox, spawn_gate=_gate)
    with pytest.raises(Refused):
        _ask(client)
    assert sandbox.calls() == [], "the child was started although the gate refused"
    assert _spans() == [], "a refused call has no span"


def test_a_latched_gate_refuses_every_later_call_without_asking_again(sandbox):
    asked: list[str] = []

    def _gate(text):
        asked.append(text)
        if len(asked) == 2:
            raise PermissionError("refused at the second call")

    gate = lc.LatchedSpawnGate(_gate)
    client = _client(sandbox, spawn_gate=gate)
    _ask(client)
    for _ in range(3):
        with pytest.raises(PermissionError):
            _ask(client)
    assert len(sandbox.calls()) == 1, "nothing is spawned after the refusal"
    assert len(asked) == 2 and gate.calls == 2, "the gate is not run again once it refused"
    assert isinstance(gate.refusal, PermissionError)


def test_an_orchestrator_with_a_gate_hands_no_phase_an_ungated_client(sandbox):
    """The real orchestrator: every phase object holds the one gated client."""
    from skill_creator import skill_creator as sc
    gate = lc.LatchedSpawnGate(lambda text: None)
    orch = sc.SkillCreatorOrchestrator(claude_client=_client(sandbox), spawn_gate=gate,
                                       registry_root=str(Path(os.environ["CORVIN_HOME"])
                                                         / "tenants" / "_default" / "skill-forge"))
    holders = [orch, orch.planner, orch.tester, orch.reviewer]
    assert all(h.client is orch.client for h in holders)
    assert orch.client.spawn_gate is gate
    for h in holders:
        h.client.messages.create(messages=[{"role": "user", "content": "x"}])
    assert gate.calls == len(sandbox.calls()) == 4


def test_a_client_of_unknown_type_is_wrapped_not_trusted():
    calls: list[str] = []

    class Other:
        engine_id = "other"

        class messages:  # noqa: N801
            @staticmethod
            def create(**kw):
                calls.append("create")
                return "R"

    asked: list[str] = []
    gated = lc.gated_client(Other(), asked.append)
    assert isinstance(gated, lc.GatedClient) and lc.engine_id_of(gated) == "other"
    assert gated.messages.create(messages=[{"role": "user", "content": "hello"}], system="S") == "R"
    assert asked == ["hello\n\nS"] and calls == ["create"]
    assert lc.gated_client(None, asked.append) is None


# ── R2-BR-10: the SDK path is a priceable span on the lineage's model too ────

class _SdkUsage:
    input_tokens, output_tokens = 11, 7
    cache_read_input_tokens, cache_creation_input_tokens = 100, 20


class _FakeSdk:
    def __init__(self, fail: bool = False) -> None:
        self.requests: list[dict] = []
        self.fail = fail
        self.messages = self

    def create(self, **kw):
        self.requests.append(kw)
        if self.fail:
            raise RuntimeError("upstream 529")

        class _Resp:
            model = "claude-opus-9-20270101"
            usage = _SdkUsage()
            content = [type("B", (), {"text": "PONG"})()]
        return _Resp()


def test_the_api_path_writes_a_span_on_the_reported_model(sandbox, monkeypatch):
    import model_selector as ms
    monkeypatch.setattr(ms, "top_model", lambda tenant_id=None: "claude-opus-9")
    sdk = _FakeSdk()
    client = lc.ApiSpanClient(sdk, tenant_id="_default")
    resp = client.messages.create(model=lc.DEFAULT_MODEL, max_tokens=10,
                                  messages=[{"role": "user", "content": "say PONG"}])
    assert resp.content[0].text == "PONG"
    # The phase code's literal means "the top tier": the lineage decides, not the literal.
    assert sdk.requests[0]["model"] == "claude-opus-9"
    assert sdk.requests[0]["max_tokens"] == 10
    start, end = _spans()
    assert start["event_type"] == "engine.span.start" and end["event_type"] == "engine.span.end"
    assert start["details"]["engine_id"] == end["details"]["engine_id"] == "api"
    assert start["details"]["model_id"] == "claude-opus-9"
    assert end["details"]["model_id"] == "claude-opus-9-20270101"      # as REPORTED
    assert end["details"]["status"] == "ok"
    assert (end["details"]["input_tokens"], end["details"]["output_tokens"],
            end["details"]["cache_read_tokens"], end["details"]["cache_write_tokens"]) == (11, 7, 100, 20)
    assert lc.engine_id_of(client) == "api"


def test_a_failed_api_call_closes_its_span_as_an_error_and_a_pin_wins(sandbox, monkeypatch):
    monkeypatch.setenv(lc.MODEL_ENV, "anthropic/claude-sonnet-5")
    sdk = _FakeSdk(fail=True)
    client = lc.ApiSpanClient(sdk)
    with pytest.raises(RuntimeError):
        client.messages.create(model=lc.DEFAULT_MODEL, messages=[{"role": "user", "content": "x"}])
    assert sdk.requests[0]["model"] == "claude-sonnet-5", "a provider prefix never reaches the API"
    start, end = _spans()
    assert end["details"]["status"] == "error" and end["details"]["model_id"] == ""


def test_the_api_path_is_gated_like_the_cli_path(sandbox):
    sdk = _FakeSdk()

    def _gate(_text):
        raise PermissionError("no")

    client = lc.ApiSpanClient(sdk, spawn_gate=_gate)
    with pytest.raises(PermissionError):
        client.messages.create(messages=[{"role": "user", "content": "x"}])
    assert sdk.requests == [] and _spans() == []


def test_the_resolver_never_hands_out_a_bare_sdk_client(sandbox, monkeypatch):
    import types
    fake_mod = types.ModuleType("anthropic")
    fake_mod.Anthropic = lambda api_key=None: _FakeSdk()
    monkeypatch.setitem(sys.modules, "anthropic", fake_mod)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k-test")
    monkeypatch.setenv(lc.ENGINE_ENV, "api")
    client = lc.resolve_llm_client(tenant_id="_default")
    assert isinstance(client, lc.ApiSpanClient) and client.tenant_id == "_default"
