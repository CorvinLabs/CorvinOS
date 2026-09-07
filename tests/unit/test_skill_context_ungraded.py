"""F-K8: the ungraded-injection env var may NARROW, never WIDEN.

``CORVIN_DELEGATE_INJECT_SKILLS_UNGRADED=1`` used to resolve through the same
env-floor-wins rule as ``inject_skills`` and therefore overrode an explicit
``inject_ungraded=False`` tool-arg — injecting bodies that had passed no grade
gate. ``resolve_inject_ungraded`` is conjunctive.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "core" / "delegate" / "corvin_delegate" / "skill_context.py"
_spec = importlib.util.spec_from_file_location("_skill_context_under_test", _SRC)
skill_context = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = skill_context
_spec.loader.exec_module(skill_context)  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "env, arg, persona, expected",
    [
        (False, True, True, False),   # env=0 always narrows
        (True, False, True, False),   # env=1 does NOT override an explicit False (the F-K8 hole)
        (True, None, False, False),   # env=1 alone never widens
        (True, None, None, False),
        (True, True, None, True),     # explicit opt-in + env permits
        (None, True, None, True),
        (None, None, True, True),     # persona default is an explicit opt-in too
        (None, None, None, False),    # default-deny
    ],
)
def test_resolve_inject_ungraded_is_conjunctive(env, arg, persona, expected):
    assert skill_context.resolve_inject_ungraded(
        env_floor=env, tool_arg=arg, persona_default=persona,
    ) is expected


def test_graded_injection_keeps_the_env_floor_semantics():
    """The graded-skills switch is unchanged: env=1 may force injection ON."""
    assert skill_context.resolve_inject_skills(env_floor=True, tool_arg=False) is True
    assert skill_context.resolve_inject_skills(env_floor=False, tool_arg=True) is False


def test_block_builder_uses_the_conjunctive_rule(monkeypatch):
    """Through the real entry point: env=1 + tool-arg False → ungraded stays off."""
    calls = {}

    class _FakeInject:
        @staticmethod
        def collect_active_skills(**kwargs):
            calls.update(kwargs)
            return ""

    monkeypatch.setattr(skill_context, "_skill_inject", _FakeInject)
    monkeypatch.setenv(skill_context._ENV_INJECT_UNGRADED, "1")
    skill_context.build_skill_context_block(persona="coder", inject_skills=True, inject_ungraded=False)
    assert calls["profile"]["inject_ungraded"] is False
    calls.clear()
    skill_context.build_skill_context_block(persona="coder", inject_skills=True, inject_ungraded=True)
    assert calls["profile"]["inject_ungraded"] is True
