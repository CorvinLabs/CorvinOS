"""R3-NEW-6 / R3-NEW-27: speech-to-text and the L35 egress policy.

The STT chain defaults to ``openai -> local`` and uploaded every voice message
to OpenAI without asking the tenant's egress gate - TTS and every engine spawn
do. And ``CORVIN_STT_CHAIN`` with a typo (``lokal``) filtered to an empty chain,
which fell back to the cloud-first default: the operator who tried to keep
audio on the box sent it out.

The providers are stubbed (no network, no model); the gate, the tenant config
and the audit chain are the real ones.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SHARED = REPO / "corvin_operator" / "bridges" / "shared"
FORGE = REPO / "corvin_operator" / "forge"
VOICE_SCRIPTS = REPO / "corvin_operator" / "voice" / "scripts"

for _p in (SHARED, FORGE, VOICE_SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from stt import resolver  # noqa: E402
from stt.base import STTProviderUnavailable, TranscriptResult  # noqa: E402


class _Stub:
    name = "stub"
    calls: list = []

    def is_available(self) -> bool:
        return True

    def transcribe(self, audio_path, *, lang=None, timeout_s=None):
        type(self).calls.append(str(audio_path))
        return TranscriptResult(text=self.name, provider=self.name)


def _make(name: str):
    return type(f"Stub_{name}", (_Stub,), {"name": name, "calls": []})


@pytest.fixture
def stubs(monkeypatch):
    openai, local = _make("openai"), _make("local")
    monkeypatch.setattr(resolver, "_PROVIDERS", {"openai": openai, "local": local})
    for k in ("CORVIN_STT_PROVIDER", "CORVIN_STT_CHAIN", "CORVIN_TENANT_ID"):
        monkeypatch.delenv(k, raising=False)
    return openai, local


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    monkeypatch.setenv("CORVIN_HOME", str(h))
    return h


def _policy(home: Path, tenant: str, egress: dict) -> Path:
    g = home / "tenants" / tenant / "global"
    g.mkdir(parents=True, exist_ok=True)
    import yaml
    (g / "tenant.corvin.yaml").write_text(yaml.safe_dump({"spec": {"egress": egress}}))
    return g / "forge" / "audit.jsonl"


def _events(chain: Path) -> list[dict]:
    if not chain.exists():
        return []
    return [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]


def test_transcript_result_shape_is_what_the_stub_assumes():
    assert {"text", "provider"} <= set(TranscriptResult.__dataclass_fields__)


def test_stt_host_is_registered_for_the_gate():
    import egress_gate

    assert egress_gate.DEFAULT_ENGINE_HOSTS["stt_openai"] == "api.openai.com"


def test_no_policy_keeps_the_default_chain(stubs, home, tmp_path):
    openai, local = stubs
    res = resolver.transcribe(tmp_path / "a.ogg")
    assert res.provider == "openai" and local.calls == []


def test_denied_host_falls_through_to_local_and_is_audited(stubs, home, tmp_path):
    openai, local = stubs
    chain = _policy(home, "_default", {"enabled": True, "default_action": "deny",
                                       "allowed_hosts": ["api.anthropic.com"]})
    res = resolver.transcribe(tmp_path / "a.ogg")
    assert res.provider == "local"
    assert openai.calls == [], "audio was sent to the denied cloud provider"
    blocked = [e for e in _events(chain) if e["event_type"] == "egress.blocked"]
    assert blocked and blocked[-1]["details"].get("engine_id") == "stt_openai"
    assert type(resolver.resolve()).name == "local"


def test_forbidden_host_is_denied_under_default_allow(stubs, home, tmp_path):
    openai, local = stubs
    _policy(home, "_default", {"enabled": True, "default_action": "allow",
                               "forbidden_hosts": ["api.openai.com"]})
    assert resolver.transcribe(tmp_path / "a.ogg").provider == "local"
    assert openai.calls == []


def test_allowed_host_is_used(stubs, home, tmp_path):
    openai, local = stubs
    _policy(home, "_default", {"enabled": True, "default_action": "deny",
                               "allowed_hosts": ["api.openai.com"]})
    assert resolver.transcribe(tmp_path / "a.ogg").provider == "openai"


def test_the_turn_tenant_decides_not_the_process_tenant(stubs, home, tmp_path):
    openai, local = stubs
    _policy(home, "acme", {"enabled": True, "default_action": "deny", "allowed_hosts": []})
    assert resolver.transcribe(tmp_path / "a.ogg").provider == "openai"       # _default: no policy
    assert resolver.transcribe(tmp_path / "b.ogg", tenant_id="acme").provider == "local"
    assert openai.calls == [str(tmp_path / "a.ogg")]


def test_pinned_cloud_provider_is_refused_not_silently_replaced(stubs, home, tmp_path, monkeypatch):
    openai, local = stubs
    _policy(home, "_default", {"enabled": True, "default_action": "deny", "allowed_hosts": []})
    with pytest.raises(STTProviderUnavailable, match="not permitted"):
        resolver.transcribe(tmp_path / "a.ogg", provider="openai")
    monkeypatch.setenv("CORVIN_STT_PROVIDER", "openai")
    with pytest.raises(STTProviderUnavailable, match="not permitted"):
        resolver.resolve()
    assert openai.calls == [] and local.calls == []


def test_broken_gate_refuses_the_cloud_provider(stubs, home, tmp_path, monkeypatch):
    openai, local = stubs
    import egress_gate

    def boom(*a, **k):
        raise RuntimeError("gate exploded")

    monkeypatch.setattr(egress_gate, "load_egress_gate_for_tenant", boom)
    assert resolver.transcribe(tmp_path / "a.ogg").provider == "local"
    assert openai.calls == []


@pytest.mark.parametrize("raw", ["lokal", "lokal,opnai", "LOCAL"])
def test_chain_that_is_set_but_names_nothing_known_never_becomes_cloud_first(
        stubs, home, tmp_path, monkeypatch, raw):
    openai, local = stubs
    monkeypatch.setenv("CORVIN_STT_CHAIN", raw)
    with pytest.raises(STTProviderUnavailable, match="CORVIN_STT_CHAIN"):
        resolver.transcribe(tmp_path / "a.ogg")
    with pytest.raises(STTProviderUnavailable, match="CORVIN_STT_CHAIN"):
        resolver.resolve()
    assert openai.calls == []


def test_partly_valid_chain_keeps_only_what_was_asked_for(stubs, home, tmp_path, monkeypatch):
    openai, local = stubs
    monkeypatch.setenv("CORVIN_STT_CHAIN", "lokal,local")
    assert resolver._chain_from_env() == ("local",)
    assert resolver.transcribe(tmp_path / "a.ogg").provider == "local"
    assert openai.calls == []
