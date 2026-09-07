"""The audit WRITER's ``lom_hash_for`` obeys the same contract as the registry's.

R4 finding (2026-09-07): ``security_events.lom_hash_for`` claimed in its
docstring to have the same contract as
``core/skills/skill_registry_phase1.py::_compute_lom_hash`` and did not. Round 2
removed the ``sha256(label)`` fallback for an unresolvable ``file:function`` from
the registry and round 3 required a ``:L<n>`` to fall inside the named function;
neither fix reached the writer — which is what stamps ``lom_hash`` into the
hash-chained log for every NON-skill emitter. So a fabricated LoM
(``totally/made/up.py:fabricated``, ``/etc/passwd:root``) produced a hash
indistinguishable from a real source binding, in the file CLAUDE.md cites as the
anti-spoofing binding, with no verifier anywhere in the tree.

These tests pin the two implementations together and drive the REAL writer.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "operator" / "forge"))

from forge import security_events as se  # noqa: E402

#: (lom, must_bind). The exact table the R4 reproduction printed.
CASES = [
    ("core/skills/boot.py:boot_skills",       True),
    ("core/skills/boot.py:no_such_function",  False),
    ("totally/made/up.py:fabricated",         False),
    ("/etc/passwd:root",                      False),
    ("core/skills/boot.py:boot_skills:L1",    False),   # line 1 is outside the function
    ("consistency_checker.validate_consistency", False),  # no file part at all
    ("context_selector.py:execute",           False),   # path does not exist at repo root
    ("no_colon_at_all",                       False),
]


@pytest.mark.parametrize("lom,must_bind", CASES)
def test_unresolvable_lom_returns_none_not_a_label_hash(lom, must_bind):
    import hashlib
    h = se.lom_hash_for(lom)
    if must_bind:
        assert h is not None and len(h) == 64, lom
    else:
        assert h is None, f"{lom} produced {h!r} — an unbindable LoM must not get a hash"
    # and never, in either case, the sha256 of the label itself
    assert h != hashlib.sha256(lom.encode()).hexdigest()


@pytest.mark.parametrize("lom,_must_bind", CASES)
def test_writer_and_registry_agree(lom, _must_bind):
    """Both implementations, same input, same verdict AND same digest."""
    from core.skills.skill_registry_phase1 import SkillsRegistry
    assert se.lom_hash_for(lom) == SkillsRegistry._compute_lom_hash(lom), lom


def test_verify_lom_binding_round_trips():
    lom = "core/skills/boot.py:boot_skills"
    assert se.verify_lom_binding(lom, se.lom_hash_for(lom))
    assert not se.verify_lom_binding(lom, "0" * 64)
    assert not se.verify_lom_binding("totally/made/up.py:fabricated", "0" * 64)
    assert not se.verify_lom_binding(lom, None)


def test_written_record_distinguishes_bound_from_unbound(tmp_path, monkeypatch):
    """Through the REAL writer: a record must SAY whether its LoM bound.

    Before R4 nothing in a record distinguished a source-bound hash from a label
    hash, so an auditor could not tell attribution from decoration.
    """
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "k"))
    chain = tmp_path / "audit.jsonl"
    se.write_event(chain, "tool.created", tool="a",
                   details={"lom": "core/skills/boot.py:boot_skills"})
    se.write_event(chain, "tool.created", tool="b",
                   details={"lom": "totally/made/up.py:fabricated"})
    recs = [json.loads(l)["details"] for l in chain.read_text().splitlines()]

    assert recs[0]["lom_bound"] is True
    assert recs[0]["lom_hash"] == se.lom_hash_for("core/skills/boot.py:boot_skills")
    assert se.verify_lom_binding(recs[0]["lom"], recs[0]["lom_hash"])

    assert recs[1]["lom_bound"] is False
    assert "lom_hash" not in recs[1], "a fabricated LoM must not carry a bound-looking hash"


def test_lom_is_not_a_hash_oracle_over_arbitrary_files(tmp_path):
    """A LoM outside the repo, or a non-.py file, is refused BEFORE it is read."""
    outside = tmp_path / "secret.py"
    outside.write_text("def root(): return 1\n")
    assert se.lom_hash_for(f"{outside}:root") is None
    assert se.lom_hash_for("README.md:anything") is None
    assert se.lom_hash_for("../etc/passwd:root") is None


def test_the_two_exclusion_lists_agree():
    """``_LOM_EXCLUDED_PARTS`` exists twice — the drift is the bug.

    ``.claude`` was added to the registry's copy because ``.claude/worktrees/``
    holds .py-bearing copies of the repo inside the repo root, rewritten by
    running agents; the writer's copy — which stamps ``lom_hash`` for every
    NON-skill emitter — did not get it. Two copies is how that fix failed to take
    effect everywhere. They cannot be collapsed (forge is importable from a
    bridge daemon with no ``core/`` on sys.path), so this pins them together.
    """
    from core.skills.skill_registry_phase1 import _LOM_EXCLUDED_PARTS as registry_parts
    assert set(se._LOM_EXCLUDED_PARTS) == set(registry_parts), (
        "writer and registry LoM exclusion lists have drifted: "
        f"writer-only={sorted(set(se._LOM_EXCLUDED_PARTS) - set(registry_parts))} "
        f"registry-only={sorted(set(registry_parts) - set(se._LOM_EXCLUDED_PARTS))}"
    )


def test_a_lom_cannot_bind_into_a_claude_worktree():
    """A worktree copy under ``.claude/`` is refused even though it is a real
    ``.py`` file inside the repo root."""
    import ast
    wt_root = REPO / ".claude" / "worktrees"
    if not wt_root.is_dir():
        pytest.skip("no .claude worktree present in this checkout")
    target = None
    for py in wt_root.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
        except (SyntaxError, OSError):
            continue
        fn = next((n.name for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
        if fn:
            target = (py.relative_to(REPO), fn)
            break
    if target is None:
        pytest.skip("no parsable .py with a def inside .claude/worktrees")
    rel, fn = target
    assert se.lom_hash_for(f"{rel}:{fn}") is None, (
        f"{rel}:{fn} bound — a LoM must never name a file under .claude/worktrees")
