"""scripts/zero_caller_sweep.py — positive and negative controls (ADR-2102).

A sweep that reports nothing proves nothing unless it is shown to (a) reach
modules that are known to be live and (b) flag ones known to be dead. Both are
pinned here against the real tree, plus a synthetic tree for the mechanics.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import zero_caller_sweep as z  # noqa: E402


def test_known_live_modules_are_reachable():
    idx = z.Index(z.production_files())
    live = z.reachable(idx)
    for rel in ("corvin_operator/bridges/shared/session_ledger.py",
                "corvin_operator/context_engineering/stages/l10_adapter.py",
                "core/skills/os_skills/delegation_router.py",
                "corvin_operator/bridges/shared/erasure_handlers.py"):
        assert REPO / rel in live, f"positive control unreachable: {rel}"


def test_known_dead_context_bridges_are_flagged():
    dead = {(d["file"], d["class"]) for d in z.sweep()["dead"]}
    assert ("core/orchestration/subsystems/context_bridge.py", "ContextBridge") in dead
    # phase1's ContextBridge is imported (package __init__ re-export, pulled in
    # by a mounted console route) but never instantiated — reachable by import,
    # so NOT flagged; "imported" is not "used" (review R2-C16).
    assert ("core/skills/os_skills/phase1/context_bridge.py", "ContextBridge") not in dead
    # review R1-C10: the skill_registry_phase1 `Skill` base and unscheduled stages
    assert ("core/skills/workflow_optimizer.py", "WorkflowOptimizerSkill") in dead
    assert any(d["file"].endswith("adr_reranking_stage.py") for d in z.sweep()["dead"])


def test_plugin_json_entry_points_are_not_roots():
    """Review R2-C15: the plugin bootstrap loads plugin.yaml → provider.py /
    plugin.py; a plugin.json entry_point is never loaded, so those plugins
    (which also import a nonexistent ``corvin_plugins.BasePlugin``) are dead."""
    dead = {d["file"] for d in z.sweep()["dead"]}
    assert "core/plugins/buildin/ai/vibe_engineering/src/vibe_routing.py" in dead


def test_the_gate_passes_on_the_committed_baseline():
    assert z.main(["--check"]) == 0


def test_mechanics_on_a_synthetic_tree(tmp_path, monkeypatch):
    (tmp_path / "core" / "pkg").mkdir(parents=True)
    (tmp_path / "core" / "pkg" / "__init__.py").write_text("")
    (tmp_path / "core" / "pkg" / "base.py").write_text("class Subsystem: pass\n")
    (tmp_path / "core" / "pkg" / "wired.py").write_text(
        "from .base import Subsystem\nclass Wired(Subsystem): pass\n")
    (tmp_path / "core" / "pkg" / "by_string.py").write_text(
        "from .base import Subsystem\nclass ByString(Subsystem): pass\n")
    (tmp_path / "core" / "pkg" / "dead.py").write_text(
        "from .base import Subsystem\nclass Dead(Subsystem): pass\n")
    (tmp_path / "core" / "main.py").write_text(
        "from core.pkg.wired import Wired\nREG = {'x': 'core.pkg.by_string:ByString'}\n")
    (tmp_path / "pyproject.toml").write_text('[project.scripts]\nx = "core.main:run"\n')
    monkeypatch.setattr(z, "REPO", tmp_path)
    monkeypatch.setattr(z, "EXTRA_ROOTS", ())
    monkeypatch.setattr(z, "SCAN_DIRS", ("core",))
    dead = {d["class"] for d in z.sweep()["dead"]}
    assert dead == {"Dead"}
