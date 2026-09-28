"""ADR-2094 E2E — the skill canary through the real bridge entry point.

Real inbox envelopes go through ``adapter.process_one``. ``ADAPTER_FAKE_CLAUDE``
replaces only the Claude CLI subprocess; ``ADAPTER_FAKE_ARGS_DUMP`` records
the arguments the adapter built for it — including the system prompt with the
injected skill block — so the test sees which body each chat was served.
The follow-up turn ("danke" / "falsch") is graded by the adapter's own
outcome-grading step and must land on the variant that chat was served.
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
for p in (SHARED, REPO / "corvin_operator" / "forge", REPO / "corvin_operator" / "skill-forge",
          REPO / "corvin_operator", REPO / "core" / "console"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

SKILL = "assistant.check_json_syntax"
SENDER = "u-canary"
LIVE = "LIVE-MARKER validate every JSON file strictly and report each error"
CANDIDATE = "CANDIDATE-MARKER validate JSON, report errors with line numbers and duplicate keys"


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    for name in ("inbox", "outbox", "processed", "bridges"):
        (tmp_path / name).mkdir()
    home = tmp_path / "home"
    env = {
        "CORVIN_HOME": str(home), "CORVIN_TENANT_ID": "_default",
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "CORVIN_AUDIT_ANCHOR_KEY": str(tmp_path / "anchor.key"),
        "ADAPTER_INBOX": str(tmp_path / "inbox"), "ADAPTER_OUTBOX": str(tmp_path / "outbox"),
        "ADAPTER_PROCESSED": str(tmp_path / "processed"),
        "ADAPTER_BRIDGES_DIR": str(tmp_path / "bridges"),
        "ADAPTER_FAKE_CLAUDE": "1", "ADAPTER_FAKE_DELAY": "0",
        "ADAPTER_FAKE_ARGS_DUMP": str(tmp_path / "args.jsonl"),
        "ADAPTER_ROUTING_MODE": "off", "ADAPTER_DISABLE_VOICE": "1",
        "VOICE_AUDIT_PATH": str(tmp_path / "audit.jsonl"),
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    from license import validator as host_validator
    orig = host_validator._ACTIVE_LICENSE
    host_validator._set_active_license({"tier": "member"})
    from skill_creator.registry_bridge import promote_to_registry
    from skill_creator import autonomous
    root = home / "tenants" / "_default" / "skill-forge"
    promote_to_registry(root, name=SKILL, body_md=LIVE, description="Validates JSON files.")
    canary = autonomous.canary_store(root)
    canary.start(name=SKILL, candidate_body=CANDIDATE, live_body=LIVE, traffic_percent=50,
                 source="operator", trigger={"reason": "operator_request"})

    sys.modules.pop("adapter", None)
    adapter = importlib.import_module("adapter")
    yield adapter, tmp_path, root, canary
    host_validator._set_active_license(orig)


def _send(adapter, base: Path, chat: str, text: str) -> None:
    msg_id = f"e2e-{time.time_ns()}"
    envelope = {"id": msg_id, "channel": "discord", "from": SENDER, "chat_id": chat,
                "text": text, "ts": time.time()}
    f = base / "inbox" / f"{msg_id}.json"
    f.write_text(json.dumps(envelope))
    adapter.process_one(f, settings={"whitelist": [SENDER], "voice_summary_mode": "never"})


def _served_in_last_turn(base: Path) -> str:
    lines = (base / "args.jsonl").read_text().splitlines()
    args = json.loads(lines[-1])["args"]
    # The system prompt travels as a file; read what the engine would read.
    sys_file = args[args.index("--append-system-prompt-file") + 1]
    system = Path(sys_file).read_text(encoding="utf-8")
    assert f'<auto_skill name="{SKILL}"' in system, "the skill block never reached the engine"
    if "CANDIDATE-MARKER" in system:
        assert "LIVE-MARKER" not in system
        return "candidate"
    assert "LIVE-MARKER" in system
    return "live"


def test_bridge_turns_split_and_follow_ups_grade_the_served_variant(bridge):
    adapter, base, root, canary = bridge
    chats: dict[str, str] = {}
    n = 0
    while len(set(chats.values())) < 2:
        chat = f"chat-{n}"
        _send(adapter, base, chat, f"please use {SKILL} on data.json")
        chats[chat] = _served_in_last_turn(base)
        n += 1
        assert n < 40, chats

    # Sticky: a second turn in the same chat is served the same body.
    for chat, variant in chats.items():
        _send(adapter, base, chat, f"and again with {SKILL} on other.json")
        assert _served_in_last_turn(base) == variant

    # Follow-up grading through the adapter's own outcome step.
    for chat, variant in chats.items():
        _send(adapter, base, chat, "danke, perfekt" if variant == "candidate" else "falsch, nochmal")

    state = canary.state(SKILL)
    outcome = [g for g in state["grades"] if g["kind"] == "outcome"]
    assert {g["variant"] for g in outcome if g["score"] == 0.9} == {"candidate"}
    assert {g["variant"] for g in outcome if g["score"] == 0.1} == {"live"}

    from skill_creator.registry_bridge import registry_for
    scores = [g["score"] for g in registry_for(root).get(SKILL).grades]
    assert 0.9 not in scores, "a candidate outcome grade leaked into the live registry grades"
    assert 0.1 in scores, "the live chat's rejection must still grade the live skill"

    from forge.security_events import verify_chain
    chain = root.parent / "global" / "forge" / "audit.jsonl"
    ok, problems = verify_chain(chain)
    assert ok, problems[:3]
    assert any(json.loads(l).get("event_type") == "skill.canary_graded"
               for l in chain.read_text().splitlines() if l.strip())
