"""Review 2026-11-19 round 3, R3-BR-2: the per-spawn gate of Skill, Tool and Plugin Forge.

Until this fix only the autonomous fork gated every engine call. A console generation ran
ONE ``check_spawn_gates`` on the request and then made its plan / repair / reviewer calls -
text the model wrote in between - with no gate, and the attachment block was appended to
the engine text AFTER that one check.

Real boundaries: the console routes over HTTP, the real orchestrators and run store, the
real ``forge_runs.check_spawn_gates``. Only the gate verdict and the engine are given.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from test_forge_creator_e2e import (  # noqa: E402
    ScriptedEngine, chain_actions, console, listed_tools, poll, start,
)

REFUSAL = "Refused by house rules (test verdict)."


class CountingGate:
    """Stand-in for the four console pre-spawn gates: records every text, refuses on a marker."""

    def __init__(self, refuse_when=None):
        self.texts: list[str] = []
        self.refuse_when = refuse_when

    def __call__(self, text, **_kw):
        self.texts.append(str(text))
        if self.refuse_when is not None and self.refuse_when(str(text), len(self.texts)):
            return REFUSAL
        return None


@pytest.fixture()
def gate(monkeypatch):
    from corvin_console import _spawn_gates
    g = CountingGate()
    monkeypatch.setattr(_spawn_gates, "check_console_spawn_or_refusal", g)
    return g


def test_every_engine_call_of_a_tool_run_passes_the_gate(tmp_path, gate):
    engine = ScriptedEngine()
    with console(tmp_path, engine) as client:
        body = poll(client, start(client, "tool", "count the words in a text"))
        assert body["status"] == "success", body
    assert len(engine.prompts) >= 4          # plan + three reviewers
    # one request-time check + one check per engine call, each on that call's own text
    assert len(gate.texts) == 1 + len(engine.prompts)
    for prompt in engine.prompts:
        assert any(prompt in seen for seen in gate.texts[1:]), prompt[:60]


def test_a_refusal_on_a_later_tool_call_ends_the_run_and_registers_nothing(tmp_path, gate):
    gate.refuse_when = lambda text, n: "reviewer focused on" in text
    engine = ScriptedEngine()
    with console(tmp_path, engine) as client:
        body = poll(client, start(client, "tool", "count the words in a text"))
        assert body["status"] == "failed", body
        assert "Refused by house rules" in str(body.get("error")) + str(body.get("message"))
        assert "assistant.word_count" not in listed_tools(client)
        assert "forge.generation_refused" in chain_actions(client)
        assert "tool.generated_created" not in chain_actions(client)
    assert not any("reviewer focused on" in p for p in engine.prompts)   # nothing was spawned
    # Latched: the three reviewers start concurrently, so up to three calls reach the gate
    # before the first refusal lands - but none of the review RETRIES runs it again.
    assert 1 <= sum("reviewer focused on" in t for t in gate.texts) <= 3


def test_a_refused_plugin_plan_stages_nothing(tmp_path, gate):
    gate.refuse_when = lambda text, n: "You are planning a CorvinOS plugin" in text
    engine = ScriptedEngine()
    with console(tmp_path, engine) as client:
        body = poll(client, start(client, "plugin", "a plugin that shows the weather"))
        assert body["status"] == "failed", body
        assert "Refused by house rules" in str(body.get("error")) + str(body.get("message"))
        forged_root = client.home / "tenants" / client.tenant / "plugin-builder"
        assert not forged_root.exists() or not any(forged_root.iterdir())
    assert engine.prompts == []


def test_a_plugin_run_gates_its_plan_panel_and_review_calls(tmp_path, gate):
    engine = ScriptedEngine()
    with console(tmp_path, engine) as client:
        body = poll(client, start(client, "plugin", "a plugin that shows the weather",
                                  panel_request="show the current status as a small table"))
        assert body["status"] == "success", body
    assert len(gate.texts) == 1 + len(engine.prompts)
    assert any("Write the console panel" in t for t in gate.texts)


def test_the_attachment_block_is_judged_by_the_request_gate(tmp_path, gate, monkeypatch):
    from corvin_console.routes import forge_intake_api
    monkeypatch.setattr(forge_intake_api, "resolve_pack",
                        lambda ids, **kw: {"files": [{"name": "a.csv", "note": "ATTACH-MARKER"}]})
    gate.refuse_when = lambda text, n: "ATTACH-MARKER" in text
    engine = ScriptedEngine()
    with console(tmp_path, engine) as client:
        for kind in ("tool", "plugin"):
            resp = client.post(f"/v1/console/forge-creator/{kind}/generate",
                               json={"user_request": "summarise the attached table",
                                     "attachment_ids": ["att-1"]})
            assert resp.status_code == 403 and "house rules" in resp.text, (kind, resp.text)
        resp = client.post("/v1/console/skill-creator/generate",
                           json={"user_request": "summarise the attached table",
                                 "attachment_ids": ["att-1"], "async": True})
        assert resp.status_code == 403 and "house rules" in resp.text, resp.text
    assert engine.prompts == []
    assert len(gate.texts) == 3 and all("ATTACH-MARKER" in t for t in gate.texts)


def test_positive_control_the_same_attachment_passes_an_allowing_gate(tmp_path, gate, monkeypatch):
    from corvin_console.routes import forge_intake_api
    monkeypatch.setattr(forge_intake_api, "resolve_pack",
                        lambda ids, **kw: {"files": [{"name": "a.csv", "note": "ATTACH-MARKER"}]})
    engine = ScriptedEngine()
    with console(tmp_path, engine) as client:
        body = poll(client, start(client, "tool", "count the words in a text",
                                  attachment_ids=["att-1"]))
        assert body["status"] == "success", body
    assert "ATTACH-MARKER" in gate.texts[0]
    assert any("ATTACH-MARKER" in p for p in engine.prompts)
