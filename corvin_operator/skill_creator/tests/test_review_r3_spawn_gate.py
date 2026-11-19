"""Review 2026-11-19 round 3: R3-BR-2 (Tool / Plugin orchestrators take a spawn gate) and
R3-BR-3 (the gate reads positional messages; no text is a refusal).

The CLI tests start a REAL child process - the recording stand-in for ``claude`` of
``test_llm_client_spawn_pls2`` - and count its starts: a refused call must start none.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_OPERATOR_DIR = Path(__file__).resolve().parents[2]
for _p in (str(_OPERATOR_DIR), str(_OPERATOR_DIR / "bridges" / "shared"),
           str(_OPERATOR_DIR / "forge"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from skill_creator import llm_client as lc  # noqa: E402
from skill_creator.plugin_creator import PluginCreatorOrchestrator  # noqa: E402
from skill_creator.tool_creator import ToolCreatorOrchestrator  # noqa: E402
from test_llm_client_spawn_pls2 import FakeClaude, sandbox  # noqa: E402,F401


class Refused(RuntimeError):
    pass


class _Reply:
    text = "ok"


class _Positional:
    """A duck-typed client whose ``create`` takes the messages positionally."""

    def __init__(self):
        self.messages = self
        self.calls: list = []

    def create(self, messages=None, *args, **kwargs):
        self.calls.append(messages)
        return _Reply()


def _recording_gate(refuse_on: str = ""):
    seen: list[str] = []

    def gate(text: str) -> None:
        seen.append(text)
        if refuse_on and refuse_on in text:
            raise Refused("refused: " + refuse_on)

    return gate, seen


# ── R3-BR-3 ────────────────────────────────────────────────────────────────

def test_positional_messages_reach_the_gate():
    gate, seen = _recording_gate(refuse_on="FORBIDDEN")
    inner = _Positional()
    client = lc.gated_client(inner, gate)
    client.messages.create([{"role": "user", "content": "harmless"}])
    assert seen == ["harmless"] and len(inner.calls) == 1
    with pytest.raises(Refused):
        client.messages.create([{"role": "user", "content": "a FORBIDDEN thing"}])
    assert len(inner.calls) == 1            # the refused call never reached the engine
    assert "FORBIDDEN" in seen[-1]


def test_positional_and_keyword_text_are_both_judged():
    gate, seen = _recording_gate()
    client = lc.gated_client(_Positional(), gate)
    client.messages.create([{"role": "user", "content": "first"}], system="SYSTEM-TEXT")
    assert "first" in seen[0] and "SYSTEM-TEXT" in seen[0]
    client.messages.create(messages=[{"role": "user", "content": [
        {"type": "text", "text": "block-text"}, {"type": "tool_result", "content": "TOOL-OUT"}]}])
    assert "block-text" in seen[1] and "TOOL-OUT" in seen[1]


@pytest.mark.parametrize("call", [
    lambda c: c.messages.create(),
    lambda c: c.messages.create([]),
    lambda c: c.messages.create(messages=[{"role": "user", "content": "   "}]),
    lambda c: c.messages.create(messages=None, system=""),
])
def test_a_call_without_text_is_refused_before_the_gate_and_the_engine(call):
    gate, seen = _recording_gate()
    inner = _Positional()
    client = lc.gated_client(inner, gate)
    with pytest.raises(lc.SpawnGateRefused):
        call(client)
    assert seen == [] and inner.calls == []


def test_the_latched_gate_treats_empty_text_as_a_refusal_and_latches_it():
    gate, seen = _recording_gate()
    latched = lc.LatchedSpawnGate(gate)
    with pytest.raises(lc.SpawnGateRefused):
        latched("  ")
    assert isinstance(latched.refusal, lc.SpawnGateRefused) and seen == []
    with pytest.raises(lc.SpawnGateRefused):
        latched("perfectly fine text")      # latched: the generation is over
    assert seen == []


def test_run_gated_surfaces_a_swallowed_and_a_wrapped_refusal():
    latched = lc.LatchedSpawnGate(_recording_gate(refuse_on="BAD")[0])

    def swallowing():
        try:
            latched("BAD text")
        except Refused:
            pass
        return "an artifact"

    with pytest.raises(Refused):
        lc.run_gated(latched, swallowing)

    latched2 = lc.LatchedSpawnGate(_recording_gate(refuse_on="BAD")[0])

    def wrapping():
        try:
            latched2("BAD text")
        except Refused as exc:
            raise ValueError("planning failed") from exc

    with pytest.raises(Refused):
        lc.run_gated(latched2, wrapping)
    clean = lc.LatchedSpawnGate(_recording_gate()[0])
    assert lc.run_gated(clean, lambda: (clean("fine"), "result")[1]) == "result"


# ── R3-BR-2: the orchestrators gate their client ───────────────────────────

@pytest.mark.parametrize("factory", [ToolCreatorOrchestrator, PluginCreatorOrchestrator])
def test_tool_and_plugin_orchestrators_hand_out_only_a_gated_client(factory):
    gate, seen = _recording_gate(refuse_on="FORBIDDEN")
    inner = _Positional()
    orch = factory(tenant_id="_default", client=inner, spawn_gate=gate)
    assert orch.spawn_gate is gate
    assert orch.client is not inner
    orch.client.messages.create(messages=[{"role": "user", "content": "plan this"}])
    assert seen == ["plan this"] and len(inner.calls) == 1
    with pytest.raises(Refused):
        orch.client.messages.create(messages=[{"role": "user", "content": "FORBIDDEN"}])
    assert len(inner.calls) == 1


@pytest.mark.parametrize("factory", [ToolCreatorOrchestrator, PluginCreatorOrchestrator])
def test_without_a_gate_the_client_is_unchanged(factory):
    inner = _Positional()
    assert factory(tenant_id="_default", client=inner).client is inner


def test_a_refused_call_starts_no_claude_process(sandbox):  # noqa: F811
    """Counting fake ``claude``: one allowed call = one child, a refused call = none."""
    gate, seen = _recording_gate(refuse_on="FORBIDDEN")
    latched = lc.LatchedSpawnGate(gate)
    client = lc.ClaudeCodeClient(binary=str(sandbox.binary))
    orch = ToolCreatorOrchestrator(tenant_id="_default", client=client, spawn_gate=latched)
    orch.client.messages.create(messages=[{"role": "user", "content": "say PONG"}])
    assert len(sandbox.calls()) == 1 and latched.calls == 1
    with pytest.raises(Refused):
        orch.client.messages.create(messages=[{"role": "user", "content": "a FORBIDDEN ask"}])
    with pytest.raises(Refused):
        orch.client.messages.create(messages=[{"role": "user", "content": "harmless again"}])
    assert len(sandbox.calls()) == 1        # neither the refused nor the latched call spawned
    assert latched.calls == 2 and len(seen) == 2
