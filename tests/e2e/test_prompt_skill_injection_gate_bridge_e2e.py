"""ADR-2175 T-0104 E2E — skill injection is decided by the Skills registry, per turn.

A real inbox envelope goes through ``adapter.process_one`` with the registry booted by
the bridge's own ``_boot_acp_skills``. The system prompt the CLI would receive is read
from the fake-claude argv dump (``ADAPTER_FAKE_ARGS_DUMP`` builds the real args), and
the sandboxed hash chain is verified.

Boundaries replaced: the claude subprocess (``ADAPTER_FAKE_CLAUDE``) and the voice
summary (``ADAPTER_DISABLE_VOICE``). Everything between the inbox and the argv is real.
"""
from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SHARED = REPO / "corvin_operator" / "bridges" / "shared"
for p in (str(SHARED), str(REPO / "corvin_operator" / "forge"),
          str(REPO / "corvin_operator" / "skill-forge"), str(REPO / "core" / "console")):
    if p not in sys.path:
        sys.path.insert(0, p)

TENANT = "_default"
SENDER = "u-e2e-skillgate"
CHAT = "chat-skillgate"
PROBE = "assistant.adr2175_gate_probe"


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    for name in ("inbox", "outbox", "processed", "bridges"):
        (tmp_path / name).mkdir()
    env = {
        "CORVIN_HOME": str(tmp_path / "home"),
        "FORGE_ROOT": str(tmp_path / "forge"),
        "CORVIN_TENANT_ID": TENANT,
        "ADAPTER_INBOX": str(tmp_path / "inbox"),
        "ADAPTER_OUTBOX": str(tmp_path / "outbox"),
        "ADAPTER_PROCESSED": str(tmp_path / "processed"),
        "ADAPTER_BRIDGES_DIR": str(tmp_path / "bridges"),
        "ADAPTER_FAKE_CLAUDE": "1",
        "ADAPTER_FAKE_DELAY": "0",
        "ADAPTER_FAKE_ARGS_DUMP": str(tmp_path / "args.jsonl"),
        "ADAPTER_ROUTING_MODE": "off",
        "ADAPTER_DISABLE_VOICE": "1",
        "VOICE_AUDIT_PATH": str(tmp_path / "audit.jsonl"),
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    sys.modules.pop("adapter", None)
    adapter = importlib.import_module("adapter")
    assert "os.delegation_router" in adapter._boot_acp_skills()
    yield adapter, tmp_path


def _send(adapter, base: Path, text: str) -> str:
    """Process one envelope; return the system prompt the CLI would have received."""
    msg_id = f"e2e-{time.time_ns()}"
    envelope = {"id": msg_id, "channel": "discord", "from": SENDER,
                "chat_id": CHAT, "text": text, "ts": time.time()}
    f = base / "inbox" / f"{msg_id}.json"
    f.write_text(json.dumps(envelope))
    dump = base / "args.jsonl"
    before = len(dump.read_text().splitlines()) if dump.exists() else 0
    adapter.process_one(f, settings={"whitelist": [SENDER], "voice_summary_mode": "never"})
    calls = [json.loads(line) for line in dump.read_text().splitlines()[before:] if line.strip()]
    assert calls, "the fake CLI was not reached"
    args = calls[-1]["args"]
    if "--append-system-prompt" in args:
        return args[args.index("--append-system-prompt") + 1]
    return Path(args[args.index("--append-system-prompt-file") + 1]).read_text(encoding="utf-8")


def _chain(base: Path) -> list[dict]:
    return [json.loads(line) for line in (base / "audit.jsonl").read_text().splitlines() if line.strip()]


def _events(base: Path, name: str) -> list[dict]:
    return [e["details"] for e in _chain(base) if e.get("event_type") == name]


def _verify(base: Path) -> None:
    from forge.security_events import verify_chain
    ok, problems = verify_chain(base / "audit.jsonl")
    assert ok, problems[:3]


def _make_probe_skill():
    from skill_forge.multi_registry import MultiSkillRegistry
    import os
    reg = MultiSkillRegistry(tenant_id=TENANT, channel_id=f"discord:{CHAT}", caller_persona="assistant")
    root = str(reg._root_for("session"))
    assert root.startswith(os.environ["CORVIN_HOME"]), root  # never the live install
    reg.create(scope="session", name=PROBE, type="domain",
               body_md="GATE-PROBE-BODY: say the probe word.",
               description="probe skill for the T-0104 gate", created_by="test")


def test_every_injected_skill_is_a_registry_decision(bridge):
    adapter, base = bridge
    prompt = _send(adapter, base, "hello there")
    assert '<auto_skill name="adr_gate"' in prompt  # core quality skill, as before

    from core.skills.prompt_skill_adapter import INJECTION_LOM, DEFAULT_BUDGET_MS
    gated = _events(base, "skill.injection.gated")
    assert gated, "no per-turn gate record"
    g = gated[-1]
    assert g["tenant_id"] == TENANT and g["candidates"] >= 3 and g["allowed"] >= 3
    assert g["budget_exceeded"] is False and g["elapsed_ms"] < DEFAULT_BUDGET_MS
    runs = [e for e in _events(base, "skill.executed") if e.get("skill_id") == "prompt.adr_gate"]
    assert runs and runs[-1]["lom"] == INJECTION_LOM and runs[-1]["lom_hash"]
    assert runs[-1]["skill_version"].startswith("0.0.0+")
    assert runs[-1]["decision"]["decision"] == "inject"
    _verify(base)


def test_a_disabled_skill_is_withheld_and_a_core_skill_is_not(bridge):
    adapter, base = bridge
    _make_probe_skill()
    ask = f"please use the skill {PROBE} now"
    assert "GATE-PROBE-BODY" in _send(adapter, base, ask)  # explicit request → injected

    from core.skills.skill_registry_phase1 import get_registry
    reg = get_registry()
    assert reg.get(f"prompt.{PROBE}") is not None  # registered per turn, not only at boot
    reg.disable_skill(f"prompt.{PROBE}", TENANT)
    reg.disable_skill("prompt.adr_gate", TENANT)

    prompt = _send(adapter, base, ask)
    assert "GATE-PROBE-BODY" not in prompt  # registry disable is honoured
    assert '<auto_skill name="adr_gate"' in prompt  # its off switch is quality_layers, not this
    assert _events(base, "skill.injection.gated")[-1]["withheld"] >= 1
    _verify(base)


def test_budget_exhaustion_passes_the_rest_undecided_and_says_so(bridge, monkeypatch):
    adapter, base = bridge
    import core.skills.prompt_skill_adapter as psa
    monkeypatch.setattr(psa, "DEFAULT_BUDGET_MS", 0.0)
    prompt = _send(adapter, base, "hello again")
    assert '<auto_skill name="adr_gate"' in prompt  # never dropped for lack of time
    g = _events(base, "skill.injection.gated")[-1]
    assert g["budget_exceeded"] is True and g["ungated"] == g["candidates"] and g["errors"] == 0
    _verify(base)
