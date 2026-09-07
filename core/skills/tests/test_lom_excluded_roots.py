"""F8: a LoM must never bind to a file the RUNNING SYSTEM can rewrite (ADR-0537).

``_LOM_EXCLUDED_PARTS`` promises: "Anything under these path segments is refused
before the file is read, so a LoM can never bind to a file an attacker (or the
running system itself) can rewrite." Until the round-4 review it omitted
``.claude`` — which the agent runtime writes and which holds full, ``.py``-bearing
worktree COPIES of the repo inside ``_REPO_ROOT``. A LoM naming one bound and
produced a normal-looking source hash, so an audited decision could be attributed
to source that is not the shipped source.

Driven through the REAL registry dispatch (``execute()``), because the gate is
what an inadmissible LoM has to get past — not just the hash helper.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from core.skills.os_skills_phase1 import register_builtin_skills
from core.skills.skill_registry_phase1 import (
    _LOM_EXCLUDED_PARTS,
    _REPO_ROOT,
    CoreAuditBackend,
    SkillsRegistry,
)

_SHADOWABLE_ROOTS = (".claude", ".corvin", ".venv", "node_modules", ".git")


@pytest.fixture
def registry() -> SkillsRegistry:
    reg = SkillsRegistry(CoreAuditBackend("_default"))
    register_builtin_skills(reg)
    return reg


@pytest.fixture
def runtime_writable_source() -> Path:
    """A real, syntactically valid .py under ``.claude/`` inside _REPO_ROOT.

    Written and removed by the test; ``.claude/`` is runtime-writable by design,
    which is the whole point of the exclusion.
    """
    d = _REPO_ROOT / ".claude" / "worktrees" / f"_lom_guard_{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True)
    f = d / "shadow_source.py"
    f.write_text("def flock(fd):\n    return True\n", encoding="utf-8")
    try:
        yield f
    finally:
        f.unlink(missing_ok=True)
        d.rmdir()


def test_dot_claude_is_excluded():
    assert ".claude" in _LOM_EXCLUDED_PARTS, (
        ".claude/ is written by the running system and holds .py-bearing worktree "
        "copies of the repo inside _REPO_ROOT (round-4 review, F8)"
    )


@pytest.mark.parametrize("part", _SHADOWABLE_ROOTS)
def test_every_runtime_writable_root_stays_excluded(part):
    """Regression fence: none of these may be dropped from the set."""
    assert part in _LOM_EXCLUDED_PARTS


def test_a_lom_naming_a_dot_claude_copy_does_not_bind(runtime_writable_source):
    rel = runtime_writable_source.relative_to(_REPO_ROOT).as_posix()
    assert SkillsRegistry._compute_lom_hash(f"{rel}:flock") is None, (
        "a LoM bound to a runtime-writable copy of the repo — an audited decision "
        "would be attributed to source that is not the shipped source"
    )


def test_execute_refuses_a_dot_claude_lom(registry, runtime_writable_source):
    rel = runtime_writable_source.relative_to(_REPO_ROOT).as_posix()
    result = registry.execute(
        "os.capabilities",
        {"tenant_id": "_default", "gated_flags": ["x"]},
        lom=f"{rel}:flock",
    )
    assert result.status == "error"
    assert "LoM unresolvable" in (result.error_message or ""), result.error_message
    assert result.output is None, "the Skill must not run on an inadmissible LoM"
