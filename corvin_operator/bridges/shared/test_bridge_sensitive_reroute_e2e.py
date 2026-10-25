"""E2E (T-0219 / T-0221, ADR-2268): the bridge OS turn reroutes a sensitive prompt.

Driven through the real entry ``adapter.call_claude_streaming`` against a REAL subprocess
(``CORVIN_CLAUDE_BIN``: a launcher that records the env and argv it received, then replays a
captured claude stream). A CONFIDENTIAL prompt on a tenant with an enabled, declared
``sensitive_route`` is spawned on the declared provider with the declared model verbatim; a
public prompt spawns unchanged; an undeclared or egress-denied route refuses without a spawn.
Only the provider registry is stubbed; tenant file, gates, subprocess and audit chain are real.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE / "tests", HERE.parents[1] / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bgscope_testkit as kit  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"),
                                reason="POSIX launcher in the harness")

HOST = "llm.acme.example"
MAIL = "Please reply to jane.doe@example.com"


def _policy(*, declared=True, allowed=HOST, model="vendor/Model-1", allow_default=False) -> str:
    dcfg = {
        "sensitive_route": {"enabled": True, "provider": "acme_llm", "model_id": model},
        "provider_compliance": ([{"provider": "acme_llm", "locality": "eu_cloud",
                                  "network_egress": "external", "endpoint_host": HOST}]
                                if declared else []),
    }
    return yaml.safe_dump({"spec": {
        "data_classification": dcfg,
        "egress": {"enabled": True, "default_action": "deny",
                   "allowed_hosts": [allowed] + (["api.anthropic.com"] if allow_default else []),
                   "forbidden_hosts": [] if allow_default else ["api.anthropic.com"]},
    }})


def _fresh_adapter():
    os.environ.pop("ADAPTER_FAKE_CLAUDE", None)
    for mod in list(sys.modules):
        if mod in ("adapter", "agents") or mod.startswith("agents."):
            del sys.modules[mod]
    import adapter  # type: ignore

    adapter._house_rules_classifier = lambda task, rules, auth, **_kw: ("", 1.0, "test-benign")
    return adapter


class Env:
    def __init__(self, tmp: Path, monkeypatch):
        self.tmp, self.mp = tmp, monkeypatch
        self.cfg = tmp / "corvin" / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
        self.cfg.parent.mkdir(parents=True)
        self.dump, self.argdump = tmp / "spawn-env.txt", tmp / "spawn-args.txt"

    def write(self, text: str) -> None:
        import spawn_gates  # type: ignore
        spawn_gates._l34_cache.clear()
        if hasattr(spawn_gates, "_l35_cache"):
            spawn_gates._l35_cache.clear()
        self.cfg.write_text(text, encoding="utf-8")
        self.cfg.chmod(0o600)

    def turn(self, prompt: str, chat: str = "rt") -> str:
        adapter = _fresh_adapter()
        return adapter.call_claude_streaming(prompt, channel="discord", chat_key=chat,
                                             mode="unrestricted", profile=None)

    def spawned_env(self) -> dict:
        assert self.dump.exists(), "the claude subprocess was never started"
        return dict(l.split("=", 1) for l in self.dump.read_text().splitlines() if "=" in l)

    def args(self) -> list[str]:
        return self.argdump.read_text().splitlines()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for k, v in {
        "CORVIN_HOME": tmp_path / "corvin", "XDG_CONFIG_HOME": tmp_path / "xdg",
        "ADAPTER_INBOX": tmp_path / "inbox", "ADAPTER_OUTBOX": tmp_path / "outbox",
        "VOICE_AUDIT_PATH": tmp_path / "audit.jsonl",
    }.items():
        monkeypatch.setenv(k, str(v))
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    monkeypatch.setenv("ADAPTER_HEARTBEAT_INTERVAL", "0")
    monkeypatch.setenv("ADAPTER_STREAM_IDLE_TIMEOUT", "30")
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.setenv("ACME_LLM_KEY", "k-acme-test")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://stale.example")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k-anthropic-original")
    e = Env(tmp_path, monkeypatch)

    fake = kit.fake_env(tmp_path, "plain_no_children", speedup=50)
    real = Path(fake["CORVIN_CLAUDE_BIN"])
    wrapper = tmp_path / "fake-bin" / "claude-dump"
    wrapper.write_text(
        "#!/bin/sh\n"
        f'env > "{e.dump}"\n'
        f'printf "%s\\n" "$@" > "{e.argdump}"\n'
        f'exec "{real}" "$@"\n', encoding="utf-8")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    fake["CORVIN_CLAUDE_BIN"] = str(wrapper)
    for k, v in fake.items():
        monkeypatch.setenv(k, v)

    import engine_models as em  # type: ignore
    providers = {"acme_llm": em.ProviderSpec(
        id="acme_llm", label="Acme LLM", base_url=f"https://{HOST}/v1",
        model_source="static", credential_env="ACME_LLM_KEY", kind="cloud")}
    monkeypatch.setattr(em, "load_providers", lambda force_reload=False: providers)
    return e


def test_a_confidential_turn_spawns_on_the_declared_provider_with_the_declared_model(env):
    env.write(_policy())
    answer = env.turn(MAIL)
    assert "[security]" not in answer and "[compliance]" not in answer, answer
    spawned = env.spawned_env()
    assert spawned.get("ANTHROPIC_BASE_URL") == f"https://{HOST}/v1"
    assert spawned.get("ANTHROPIC_API_KEY") == "k-acme-test"
    blob = env.dump.read_text()
    assert "stale.example" not in blob and "k-anthropic-original" not in blob
    args = env.args()
    assert "--model" in args and args[args.index("--model") + 1] == "vendor/Model-1"


def test_a_public_turn_on_the_same_tenant_does_not_reroute(env):
    env.write(_policy(allow_default=True))
    env.turn("write a haiku about spring")
    assert env.spawned_env().get("ANTHROPIC_BASE_URL") != f"https://{HOST}/v1"
    assert "vendor/Model-1" not in env.args()


def test_an_undeclared_target_refuses_and_never_spawns(env):
    env.write(_policy(declared=False))
    answer = env.turn(MAIL)
    assert answer.startswith("["), answer
    assert not env.dump.exists(), "a refused sensitive turn must not spawn"


def test_a_provider_host_the_egress_policy_denies_refuses_and_never_spawns(env):
    env.write(_policy(allowed="other.example"))
    answer = env.turn(MAIL)
    assert "egress" in answer.lower(), answer
    assert not env.dump.exists()


def test_the_reroute_is_audited_without_the_prompt(env):
    env.write(_policy())
    env.turn(MAIL)
    import data_classification as dc  # type: ignore
    text = Path(dc._guard_audit_chain("_default", env.tmp / "corvin")).read_text(encoding="utf-8")
    assert "data_flow.rerouted" in text
    assert "jane.doe" not in text
