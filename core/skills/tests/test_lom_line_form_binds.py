"""R3-B1: the ``file:function:L<line>`` LoM form must actually BIND (ADR-0537).

Before 2026-09-07 the three-part form returned ``sha256(<that one line>)``
*before* any ``ast`` lookup, so the function part was decorative:

* a fabricated function name still produced a 64-hex "bound" hash, and
  ``execute()``'s ADR-0537 gate accepted it;
* a blank line produced the constant ``sha256("")`` —
  ``e3b0c442...`` — the same value for every blank line in every file, i.e. a
  hash that binds to nothing at all.

The guard is exercised through the REAL registry dispatch (``execute()``), not
by calling the hash helper, because the gate is what a fabricated LoM has to get
past. ``_compute_lom_hash`` is additionally asserted directly for the cases that
have no distinct execute() outcome.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from core.skills.os_skills_phase1 import register_builtin_skills
from core.skills.skill_registry_phase1 import CoreAuditBackend, SkillsRegistry

_THIS = "core/skills/tests/test_lom_line_form_binds.py"
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _marker_function() -> str:
    """A function whose body supplies known-good and known-blank line numbers."""
    payload = "bound-line"

    return payload


# Line numbers derived from the file itself, so an edit above cannot rot them.
_SRC = Path(__file__).read_text().split("\n")
_BODY_LINE = next(i + 1 for i, l in enumerate(_SRC) if 'payload = "bound-line"' in l)
_BLANK_LINE = _BODY_LINE + 1  # the blank line inside _marker_function
_OUTSIDE_LINE = next(i + 1 for i, l in enumerate(_SRC) if l.startswith("_EMPTY_SHA256"))


def _chain() -> list[dict]:
    path = Path(os.environ["VOICE_AUDIT_PATH"])
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


@pytest.fixture
def registry() -> SkillsRegistry:
    reg = SkillsRegistry(CoreAuditBackend("_default"))
    register_builtin_skills(reg)
    return reg


def _run(registry: SkillsRegistry, lom):
    return registry.execute(
        "os.capabilities", {"tenant_id": "_default", "gated_flags": ["x"]}, lom=lom
    )


class TestLineFormResolvesTheFunction:
    def test_a_line_inside_the_named_function_binds_and_runs(self, registry):
        lom = f"{_THIS}:_marker_function:L{_BODY_LINE}"
        result = _run(registry, lom)
        assert result.status == "success", result.error_message
        assert result.lom_hash == hashlib.sha256(_SRC[_BODY_LINE - 1].encode()).hexdigest()
        # and the bound hash reached the hash chain
        rec = [c for c in _chain() if c["event_type"] == "skill.executed"][-1]["details"]
        assert rec["lom_hash"] == result.lom_hash

    def test_a_fabricated_function_name_is_refused(self, registry):
        lom = f"{_THIS}:no_such_function_anywhere:L{_BODY_LINE}"
        assert SkillsRegistry._compute_lom_hash(lom) is None
        result = _run(registry, lom)
        assert result.status == "error"
        assert "LoM unresolvable" in (result.error_message or "")
        assert result.output is None  # the Skill never ran

    def test_a_line_outside_the_named_function_is_refused(self, registry):
        lom = f"{_THIS}:_marker_function:L{_OUTSIDE_LINE}"
        assert SkillsRegistry._compute_lom_hash(lom) is None
        result = _run(registry, lom)
        assert result.status == "error"
        assert "LoM unresolvable" in (result.error_message or "")

    def test_a_blank_line_never_yields_the_empty_string_hash(self, registry):
        assert _SRC[_BLANK_LINE - 1].strip() == ""
        lom = f"{_THIS}:_marker_function:L{_BLANK_LINE}"
        assert SkillsRegistry._compute_lom_hash(lom) != _EMPTY_SHA256
        assert SkillsRegistry._compute_lom_hash(lom) is None
        assert _run(registry, lom).status == "error"

    def test_the_live_producer_lom_still_binds(self):
        """``os_skills_integration._lom`` derives ``file:function:L<line>`` from
        the live frame — the only production user of the three-part form. It
        must keep binding after the fix."""
        import ast
        import inspect

        from core.skills import os_skills_integration as mod

        src = Path(inspect.getsourcefile(mod)).read_text()
        tree = ast.parse(src)
        call_lines = [
            n.lineno
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_lom"
        ]
        assert call_lines, "no _lom() call sites found"
        for line in call_lines:
            func = next(
                n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.lineno <= line <= (n.end_lineno or n.lineno)
                and n.name != "_lom"
            )
            lom = f"core/skills/os_skills_integration.py:{func.name}:L{line}"
            assert SkillsRegistry._compute_lom_hash(lom) is not None, lom


class TestSourceAdmissibility:
    @pytest.mark.parametrize("lom", [
        "/etc/passwd:root:1",
        "../../etc/passwd:root",
        "core/../../etc/hostname:f:1",
        "core/console/.venv/lib/python3.11/site-packages/pip/__init__.py:main:L1",
        "CLAUDE.md:anything",              # not a .py file
        ".corvin/tenants/_default/x.py:f", # runtime-writable tree
    ])
    def test_non_source_targets_are_refused(self, lom):
        assert SkillsRegistry._compute_lom_hash(lom) is None


class TestHashIsMemoised:
    def test_repeated_resolution_does_not_reparse(self):
        lom = f"{_THIS}:_marker_function"
        first = SkillsRegistry._compute_lom_hash(lom)
        assert first is not None

        started = time.perf_counter()
        for _ in range(200):
            assert SkillsRegistry._compute_lom_hash(lom) == first
        per_call_ms = (time.perf_counter() - started) * 1000 / 200
        # A cold resolve of this file is milliseconds; execute() resolves twice
        # per call (gate + result). Memoised, it must be far below that.
        assert per_call_ms < 0.5, f"{per_call_ms:.3f} ms/call — memoisation lost"

    def test_editing_the_source_invalidates_the_cached_hash(self, tmp_path):
        # A file INSIDE the repo (the cache key includes mtime+size, so an edit
        # must produce a new hash rather than a stale one).
        target = Path("core/skills/tests") / "_lom_cache_probe.py"
        try:
            target.write_text("def probe():\n    return 1\n")
            lom = "core/skills/tests/_lom_cache_probe.py:probe"
            before = SkillsRegistry._compute_lom_hash(lom)
            assert before is not None
            time.sleep(0.01)
            target.write_text("def probe():\n    return 2\n")
            after = SkillsRegistry._compute_lom_hash(lom)
            assert after is not None and after != before
        finally:
            target.unlink(missing_ok=True)
