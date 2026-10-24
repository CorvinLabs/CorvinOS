"""ACS workers receive graded SkillForge skills as TEXT (workflow opt-in).

Boundary under test: ``_dispatch_workers`` -> ``_call_worker_sync`` -> a real
subprocess (a stub ``claude`` that records the ``--append-system-prompt-file`` it
was given) with a REAL ``MultiSkillRegistry`` in a sandboxed ``CORVIN_HOME``. Only
``_write_audit`` is captured. Nothing about skill selection is mocked in the main
cases: the grade gate, relevance ranking and body sanitising are the production
``skill_inject.collect_active_skills``.

What this pins (ADR-0279 reach boundary kept intact):
  * a workflow that opts in (``dynamic_skills.enabled``) gets graded skill bodies in
    the worker's system prompt, as text;
  * an UNGRADED skill never reaches a worker;
  * a workflow that did not opt in is byte-for-byte unaffected;
  * the audit event carries skill NAMES and a count, never a body;
  * a failing lookup never fails the worker.
"""
from __future__ import annotations

import asyncio
import json
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
for _p in (_HERE, _REPO / "corvin_operator" / "skill-forge", _REPO / "corvin_operator" / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="stub claude binary is a POSIX shell script",
)

import acs_runtime as _rt  # noqa: E402

GRADED_MARKER = "ACSWORKERSKILLMARKER-GRADED"
UNGRADED_MARKER = "ACSWORKERSKILLMARKER-UNGRADED"

# Prose-heavy so the SkillForge linter's code-density check passes.
_BODY = (
    "This skill explains how to clean a messy csv export before analysis. "
    "It walks through checking the header row, normalising the decimal "
    "separator and removing duplicate rows, one careful step at a time. {marker} "
    "Second paragraph: more prose about verifying row counts before and after "
    "each cleaning step so nothing is lost silently.\n"
)

_ENVELOPE = {
    "type": "result", "subtype": "success", "is_error": False,
    "result": json.dumps({"status": "success", "confidence": 0.9, "result": {"ok": True}}),
    "model": "claude-opus-5-5",
    "usage": {"input_tokens": 10, "output_tokens": 5,
              "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
}


def _recording_claude(tmp_path: Path) -> tuple[str, Path]:
    """A stand-in ``claude`` that copies its system-prompt file, then prints an envelope."""
    rec = tmp_path / "recorded-system.txt"
    script = tmp_path / "claude-stub"
    script.write_text(
        "#!/bin/sh\n"
        "while [ $# -gt 0 ]; do\n"
        f"  if [ \"$1\" = \"--append-system-prompt-file\" ]; then cp \"$2\" \"{rec}\"; fi\n"
        "  shift\n"
        "done\n"
        "cat >/dev/null\n"
        "cat <<'__ENVELOPE__'\n" + json.dumps(_ENVELOPE) + "\n__ENVELOPE__\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(script), rec


@pytest.fixture()
def skill_home(tmp_path, monkeypatch):
    """A sandboxed tenant home with one graded and one ungraded user-scope skill."""
    home = tmp_path / "corvin-home"
    home.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_FORCE_SCOPE", "user")
    monkeypatch.setenv("CORVIN_PLUGIN_SLOT_DIR", str(tmp_path / "slot"))
    monkeypatch.delenv("LDD_AUTO_OPTIN", raising=False)
    # The production lookup also covers PROJECT-scope skills of the repo it runs in
    # (intended). Pin the project root to an empty sandbox so the test sees only the
    # two skills it creates below.
    import skill_inject
    (tmp_path / "project").mkdir()
    monkeypatch.setattr(skill_inject, "_detect_project_root", lambda: tmp_path / "project")
    from skill_forge.multi_registry import MultiSkillRegistry
    reg = MultiSkillRegistry(tenant_id="_default")
    reg.create(name="acs.csv_cleaning", type="domain", description="clean a csv export",
               body_md=_BODY.format(marker=GRADED_MARKER))
    reg.grade("acs.csv_cleaning", run_id="setup", score=0.8, organic=True)
    reg.create(name="acs.csv_ungraded", type="domain", description="clean a csv export too",
               body_md=_BODY.format(marker=UNGRADED_MARKER))
    return home


def _dispatch(tmp_path: Path, spec_extra: dict) -> tuple[str, list[tuple[str, dict]]]:
    """Dispatch one worker against the recording stub. Returns (system prompt, audit writes)."""
    from test_acs_runtime import _minimal_spec  # type: ignore[import-not-found]

    binary, rec = _recording_claude(tmp_path)
    spec = _minimal_spec()
    spec.update(spec_extra)
    root_budget = _rt._budget_from_spec(spec)
    root_budget.max_total_workers = 100
    root_budget.max_workers_per_iteration = 5
    ctx = _rt.RunContext(
        run_id="run-skills", workflow_id="wf-skills", workflow_spec=spec,
        budget=root_budget.fraction(1.0), run_dir=tmp_path, root_budget=root_budget,
    )
    writes: list[tuple[str, dict]] = []

    def _capture(tenant_id, event_type, details):
        writes.append((event_type, dict(details)))

    subtasks = [{"id": "t0", "instructions": "clean the csv export and report the row counts"}]
    with (
        patch.object(_rt, "_resolve_worker_engine", return_value=("claude_code", "m")),
        patch.object(_rt, "_assert_engine_licensed", return_value=None),
        patch.object(_rt, "_claude_binary", return_value=binary),
        patch.object(_rt, "_apply_provider_redirect", return_value=None),
        patch.object(_rt.shutil, "which", return_value=binary),
        patch.object(_rt, "_write_audit", side_effect=_capture),
    ):
        results = asyncio.run(_rt._dispatch_workers(
            subtasks, ctx, depth=1, manager_model="m", worker_model="m",
        ))
    system = rec.read_text(encoding="utf-8") if rec.exists() else ""
    return system, writes, results


def test_opted_in_workflow_gives_the_worker_the_graded_skill_text(tmp_path, skill_home):
    system, writes, results = _dispatch(tmp_path, {"dynamic_skills": {"enabled": True}})
    assert results and results[0].status == "success", results
    assert GRADED_MARKER in system, "the graded skill body must reach the worker prompt"
    assert "acs.csv_cleaning" in system
    assert UNGRADED_MARKER not in system, "an ungraded skill must never reach a worker"
    events = [d for et, d in writes if et == "acs.worker_skills_injected"]
    assert len(events) == 1, writes
    assert events[0]["skills"] == "acs.csv_cleaning"
    assert events[0]["skill_count"] == 1
    assert GRADED_MARKER not in json.dumps(events), "the audit event must not carry a skill body"


def test_core_quality_skills_are_not_added_to_a_worker(tmp_path, skill_home):
    system, _writes, _results = _dispatch(tmp_path, {"dynamic_skills": {"enabled": True}})
    assert 'name="adr_gate"' not in system
    assert 'name="e2e-wiring-proof"' not in system


def test_workflow_without_the_opt_in_is_unaffected(tmp_path, skill_home):
    system, writes, results = _dispatch(tmp_path, {})
    assert results and results[0].status == "success"
    assert GRADED_MARKER not in system
    assert "SKILLS (curated" not in system
    assert not [1 for et, _ in writes if et == "acs.worker_skills_injected"]


def test_disabled_flag_is_off(tmp_path, skill_home):
    system, writes, _ = _dispatch(tmp_path, {"dynamic_skills": {"enabled": False}})
    assert GRADED_MARKER not in system
    assert not [1 for et, _ in writes if et == "acs.worker_skills_injected"]


def test_a_failing_skill_lookup_never_fails_the_worker(tmp_path, skill_home):
    import skill_inject
    with patch.object(skill_inject, "collect_active_skills", side_effect=RuntimeError("boom")):
        system, writes, results = _dispatch(tmp_path, {"dynamic_skills": {"enabled": True}})
    assert results and results[0].status == "success", results
    assert GRADED_MARKER not in system
    assert not [1 for et, _ in writes if et == "acs.worker_skills_injected"]


def test_the_workflow_validator_accepts_the_dynamic_skills_section():
    """The key must survive validation, or no real workflow could ever opt in."""
    import acs_validator
    from test_acs_runtime import _minimal_spec  # type: ignore[import-not-found]
    spec = _minimal_spec()
    spec["dynamic_skills"] = {"enabled": True, "max_skills": 3}
    assert acs_validator.validate_workflow_dict(spec).ok


@pytest.mark.parametrize("bad", ["yes", True, 1, ["enabled"], {"enabled": "true"}, {"enabled": 1}])
def test_only_a_real_true_opts_in(tmp_path, skill_home, bad):
    """A malformed section never opts a workflow in (fail toward the old behaviour)."""
    system, writes, _ = _dispatch(tmp_path, {"dynamic_skills": bad})
    assert GRADED_MARKER not in system
    assert not [1 for et, _ in writes if et == "acs.worker_skills_injected"]


@pytest.mark.parametrize("asked,expected", [(99, 5), (0, 1), ("x", 3), (2, 2)])
def test_max_skills_is_clamped(tmp_path, skill_home, asked, expected):
    import skill_inject
    seen = {}

    def _spy(**kw):
        seen.update(kw)
        return None

    with patch.object(skill_inject, "collect_active_skills", side_effect=_spy):
        _dispatch(tmp_path, {"dynamic_skills": {"enabled": True, "max_skills": asked}})
    assert seen["profile"]["max_injected_skills"] == expected
    assert seen["include_core"] is False
    assert seen["tenant_id"] == "_default"
