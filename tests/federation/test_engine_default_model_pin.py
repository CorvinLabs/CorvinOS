"""The default federation agent's model is a LABEL, never a pin (measured on two fresh installs, 2026-10-21).

``LocalAgentRegistry.ensure_default_agent`` registers ``model="claude-code"``. That string reached
``claude --model claude-code`` on the receiver and on a conversation's local turn, the API answered 404,
and every federated task on a fresh install — delegate, ``/ask @peer``, ``/talk``, both sides of a
conversation — was refused ``engine_error`` while a plain (non-federated) send to the same peer worked.
``protocol.model_pin`` is the one place a label becomes a pin; these tests pin that contract.
"""
from __future__ import annotations

import inspect

from core.federation import conversation, local_agent, protocol


def test_the_default_agent_model_is_the_engine_default_label():
    assert local_agent.DEFAULT_AGENT_MODEL == protocol.ENGINE_DEFAULT_MODEL
    assert protocol.model_pin(local_agent.DEFAULT_AGENT_MODEL) is None


def test_model_pin_passes_a_real_model_id_and_drops_every_engine_default_spelling():
    assert protocol.model_pin("claude-opus-5") == "claude-opus-5"
    assert protocol.model_pin("  claude-haiku-4-5  ") == "claude-haiku-4-5"
    for label in ("claude-code", "Claude-Code", "claude_code", "default", "engine-default", "", "   ", None, 7):
        assert protocol.model_pin(label) is None, label


def test_every_spawn_site_goes_through_model_pin():
    """Reading the source is the only cheap proof that no spawn site hands the label to the engine."""
    local_turn = inspect.getsource(conversation._run_local_turn)
    assert "model=model_pin(model)" in local_turn
    import importlib
    fed = importlib.import_module("corvin_operator.bridges.shared.a2a_federation")
    assert "model=model_pin(agent.model)" in inspect.getsource(fed)
