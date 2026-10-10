"""Fence: turns that carry a PEER's words never receive the operator's prompt skills (ADR-2175 G9).

Operator skills (bundle + SkillForge) hold the operator's own working knowledge. An
inbound A2A task or an agent-conversation turn is text from another instance; injecting
the skills there would hand that knowledge to a party the operator has not vetted.
Today no A2A path injects skills. This test makes that a decision instead of an accident:
it fails the moment one of these modules starts reaching the injector or the registry gate.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SHARED = REPO / "corvin_operator" / "bridges" / "shared"
if str(SHARED) not in sys.path:
    sys.path.insert(0, str(SHARED))

PEER_TURN_MODULES = [
    "corvin_operator/bridges/shared/a2a_worker.py",
    "corvin_operator/bridges/shared/a2a_feed.py",
    "corvin_operator/bridges/shared/a2a_task_state.py",
    "core/federation/conversation.py",
]
FORBIDDEN = {"skill_inject", "collect_active_skills", "gate_injection",
             "prompt_skill_adapter", "SkillCompiler", "skill_compiler"}


def _referenced_names(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.update(a.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            names.update((node.module or "").split("."))
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


@pytest.mark.parametrize("rel", PEER_TURN_MODULES)
def test_peer_turn_module_never_reaches_the_skill_injector(rel):
    path = REPO / rel
    assert path.is_file(), f"{rel} moved — update this fence, do not drop it"
    hit = _referenced_names(path) & FORBIDDEN
    assert not hit, (
        f"{rel} references {sorted(hit)}: a peer's turn would receive operator skills. "
        "ADR-2175 G9 keeps A2A turns skill-free; change that decision first.")


def test_the_inbound_a2a_system_prompt_carries_no_skill_block():
    import a2a_worker

    prompt = a2a_worker.build_system_prompt(
        persona="assistant", origin_id="peer-1", task_id="t-1")
    assert "<auto_skill" not in prompt and "Active session skills" not in prompt
