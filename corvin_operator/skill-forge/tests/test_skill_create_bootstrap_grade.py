"""Registration-chain gap fix (2026-09-28): ``skill_create`` (the MCP tool a
persona calls mid-chat to forge a new skill) used to leave every freshly
minted skill at ``n_grades == 0`` forever. ``skill_inject.py``'s injection
gate skips any skill with ``n_grades < 1 or mean_score <= 0`` — and there is
no organic path to a first grade for a skill nobody has used yet, since
"used" is what the grade is supposed to measure. A skill forged this way sat
registered in the canonical registry, listed by ``skill_list``, but was
NEVER a candidate for live injection — invisible to the very system it was
forged for.

This mirrors the fix already proven for the console Skill-Creator path
(``registry_bridge.py::promote_to_registry``, which already seeds a
bootstrap grade) and for console manual authoring
(``skills_manual.py::create_manual_skill``,
see ``core/console/tests/test_skills_manual_registry.py``).

Verified RED against the pre-fix code before being confirmed GREEN here
(temporarily reverted the ``self.multi.grade(...)`` call in
``mcp_server.py::_call_skill_create`` and re-ran this file — it failed with
``n_grades == 0``, as expected).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_FORGE = REPO_ROOT / "corvin_operator" / "skill-forge"
FORGE = REPO_ROOT / "corvin_operator" / "forge"

for _p in (str(SKILL_FORGE), str(FORGE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """Isolated CORVIN_HOME + a fresh MultiSkillRegistry-backed MCP server —
    never touches the live install's registry or audit chain."""
    home = tmp_path / "corvin_home"
    home.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_PLUGIN_SLOT_DIR", str(tmp_path / "slot"))
    monkeypatch.delenv("CORVIN_CALLER_PERSONA", raising=False)
    # "task" scope (forge/scope.py::scope_root) ignores CORVIN_HOME entirely —
    # it resolves to <platform-tmpdir>/.corvin/tasks/<CORVIN_TASK_ID>/forge, a
    # location SHARED across every test process on the machine unless pinned.
    # Without this, two test functions (or two separate runs) both defaulting
    # to task_id="default" collide on the same on-disk skill — which is
    # exactly what happened here (`FileExistsError: 'another.checklist'`)
    # before this line was added.
    monkeypatch.setenv("CORVIN_TASK_ID", f"pytest-{tmp_path.name}")

    # Deliberately NOT reloading skill_forge.* here (no `del sys.modules[...]`):
    # every path resolver involved (scope_root/_tenant_and_home/MultiSkillRegistry
    # construction) re-reads os.environ on every call rather than caching it at
    # import time, so a plain import already picks up the monkeypatched env
    # above. Reloading would be actively harmful — it replaces the module
    # object in sys.modules for the rest of the pytest SESSION (not just this
    # test), and any other test file that imported an exception class from
    # this package at collection time (e.g. `from skill_forge.registry import
    # SkillQuotaExceeded` at module top-level elsewhere) would then hold a
    # class object that `isinstance`/`except` no longer matches against what
    # this reloaded module raises — the exact "two classes, same qualified
    # name" hazard documented in skills_manual.py's own
    # `_linter_error`/`_is_namespace_denied` comments. Confirmed by hitting it
    # here first: an earlier version of this fixture DID reload, and running
    # this file before corvin_operator/skill-forge/tests/test_licence_gate.py
    # in the same pytest process made ITS `except SkillQuotaExceeded` stop
    # catching the exception its own registry.create() raised.
    from skill_forge.mcp_server import SkillForgeMCPServer
    from forge.scope import _resolve_tmp_tasks_root

    srv = SkillForgeMCPServer()
    try:
        yield srv
    finally:
        # task-scope's root lives outside tmp_path (platform tempdir, not
        # pytest's per-test tmp_path) — clean it up explicitly so repeated
        # local runs don't accumulate scratch dirs under a real filesystem
        # path (CORVIN_TASK_ID above already makes it unique, this just
        # keeps /tmp tidy).
        import shutil
        shutil.rmtree(_resolve_tmp_tasks_root() / f"pytest-{tmp_path.name}", ignore_errors=True)


BODY = (
    "# review.checklist\n\nFive-step review pass: behaviour test first, "
    "structural smell, naming consistency, doc-as-DOD reminder, and a "
    "final read-through for any left-over scaffolding.\n"
)


def test_skill_create_seeds_a_bootstrap_grade(sandbox):
    srv = sandbox
    srv._call_skill_create(1, {
        "name": "review.checklist",
        "type": "learned-experience",
        "description": "A five-step review checklist",
        "body_md": BODY,
        "scope": "task",  # ungated — no persona/force gymnastics needed
    })

    spec = srv.multi.get_in_scope("review.checklist", "task")
    assert spec is not None, "skill_create must register the skill"
    # The exact gate skill_inject.py applies (n_grades < 1 or mean_score <= 0
    # -> skip). Both halves must be satisfied for the skill to ever be
    # injected into a live conversation.
    assert spec.n_grades >= 1, "a freshly forged skill must clear the injection gate's grade-count half"
    assert spec.mean_score > 0.0, "a freshly forged skill must clear the injection gate's score half"
    # A self-awarded bootstrap seed must never look like earned usage — it is
    # capped at _AUTO_GRADE_CAP_MAX (0.3), same as every other bootstrap path.
    assert spec.mean_score <= 0.3


def test_response_reports_whether_the_skill_is_injectable(sandbox):
    """The tool response must not silently claim success while the skill
    stays invisible — bootstrap_graded/injectable must be True on the happy
    path so a calling persona (or a human reading the tool output) can tell."""
    srv = sandbox
    captured: list[dict] = []
    srv._respond = lambda msgid, result: captured.append(result)  # type: ignore[method-assign]

    srv._call_skill_create(1, {
        "name": "another.checklist",
        "type": "learned-experience",
        "description": "Another checklist",
        "body_md": BODY.replace("review.checklist", "another.checklist"),
        "scope": "task",
    })

    assert len(captured) == 1
    data = captured[0]["structuredContent"]["data"]
    assert data["bootstrap_graded"] is True
    assert data["injectable"] is True
