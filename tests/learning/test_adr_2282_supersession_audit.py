"""Pins the T-0263 supersession audit of ADR-2282 (Corvin-Knowledge, dated amendment).

For each of the 24 decisions ADR-2282 supersedes, every `paths:` entry is either gone from
the tree or named in KEPT with the reason it stays. A superseded path that comes back, or a
kept path that disappears, fails here, so the table in ADR-2282 and the tree cannot drift
apart silently. Data measured on origin/main 17bac25a0; update the table in the same change.
"""

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

SUPERSEDED_PATHS = {
    "ADR-0580": (
        "core/learning/quality_gate.py",
        "tests/unit/test_quality_gate_k3.py",
        "tests/test_l5_k3_k5_integration_e2e.py",
        "scripts/validate_l5_k3_k5.py",
    ),
    "ADR-0581": (
        "core/learning/conflict_resolver.py",
        "tests/unit/test_conflict_resolver_k4.py",
        "tests/test_l5_k3_k5_integration_e2e.py",
        "scripts/validate_l5_k3_k5.py",
    ),
    "ADR-0582": (
        "core/learning/rollback_guard.py",
        "tests/unit/test_rollback_guard_k5.py",
        "tests/test_l5_k3_k5_integration_e2e.py",
        "scripts/validate_l5_k3_k5.py",
    ),
    "ADR-0583": (
    ),
    "ADR-0584": (
        "core/console/corvin_console/web-next/src/panels/ApprovalControlPanel.tsx",
        "core/console/corvin_console/web-next/src/panels/registry.tsx",
        "tests/test_l5_phase3_approval_control_panel.py",
    ),
    "ADR-0585": (
        "core/learning/cross_skill_optimizer.py",
        "tests/test_l5_phase4_advanced_features.py",
    ),
    "ADR-0586": (
        "core/learning/advanced_learning.py",
        "tests/test_l5_phase4_advanced_features.py",
    ),
    "ADR-0587": (
        "core/learning/production_tuning.py",
        "tests/test_l5_phase4_advanced_features.py",
    ),
    "ADR-0848": (
        "core/learning/ldd_learning_loop.py",
        "tests/e2e/test_ldd_optimization.py",
    ),
    "ADR-0886": (
        "core/learning/loss_signals/advanced_loss_signals.py",
        "tests/e2e/test_tier34_complete.py",
    ),
    "ADR-0623": (
        "core/learning/meta_optimizer.py",
        "core/learning/base.py",
        "tests/test_meta_optimizer.py",
    ),
    "ADR-0655": (
        "core/learning/completion_detectors/",
        "operator/bridges/shared/completion_notify.py",
        "core/notification/summary_generator.py",
        "core/notification/notification_router.py",
    ),
    "ADR-0683": (
        "core/skills/skill_learning_loop.py",
        "core/skills/skill_optimizer.py",
        "tests/skills/test_skill_learning_loop_k1.py",
    ),
    "ADR-0840": (
        "core/skills/confidence_calculator.py",
        "core/skills/skill_learning_loop.py",
        "core/skills/skill_optimizer.py",
        "core/skills/tests/test_confidence_calculator.py",
        "core/console/corvin_console/routes/skill_learning_routes.py",
        "core/console/corvin_console/web-next/src/pages/skill-learning-dashboard.tsx",
        "tests/e2e/test_skill_forge_phase7_learning_loop.py",
    ),
    "ADR-2165": (
        "core/skills/skill_learning_loop.py",
        "core/skills/skill_optimizer.py",
        "core/console/corvin_console/web-next/src/pages/skill-learning-dashboard.tsx",
        "tests/e2e/test_skill_forge_phase7_learning_loop.py",
    ),
    "ADR-0775": (
        "core/marketplace/plugin_tier_system.py",
        "core/marketplace/plugin_signing.py",
        "tests/integration/test_phase3_k4_marketplace_hardening.py",
    ),
    "ADR-0776": (
        "core/marketplace/plugin_distribution.py",
        "core/marketplace/plugin_learning_loop.py",
        "tests/integration/test_phase3_k5_marketplace_e2e_convergence.py",
    ),
    "ADR-0860": (
        "core/marketplace/community_submissions.py",
    ),
    "ADR-0347": (
        "core/orchestration/brain/",
        "core/orchestration/subsystems/",
    ),
    "ADR-0359": (
        "core/orchestration/subsystems/tool_forge_subsystem.py",
        "core/orchestration/async_wrappers/async_forge_registry.py",
        "core/orchestration/subsystems/forge_apis.py",
        "core/orchestration/subsystems/forge_api_impl.py",
    ),
    "ADR-0360": (
        "core/orchestration/subsystems/skill_forge_subsystem.py",
        "core/orchestration/async_wrappers/async_skill_registry.py",
        "core/orchestration/subsystems/forge_apis.py",
        "core/orchestration/subsystems/forge_api_impl.py",
    ),
    "ADR-0361": (
        "core/orchestration/subsystems/forge_apis.py",
        "core/orchestration/subsystems/forge_api_impl.py",
        "core/orchestration/hub.py",
    ),
    "ADR-0394": (
        "corvin_operator/context_engineering/selective_injection.py",
        "corvin_operator/context_engineering/memory_pruning.py",
        "corvin_operator/context_engineering/adr_reranking.py",
        "corvin_operator/context_engineering/stages/selective_injection_stage.py",
        "corvin_operator/context_engineering/stages/memory_pruning_stage.py",
        "corvin_operator/context_engineering/stages/adr_reranking_stage.py",
    ),
    "ADR-0601": (
        "core/skills/feedback_integration/",
        "core/skills/skill_grader.py",
    ),
}

KEPT = {
    "core/console/corvin_console/web-next/src/panels/registry.tsx": "ADR-0353 (live console panel registry)",
    "tests/e2e/test_tier34_complete.py": "live core/testing/adversarial_sweep tests; loss-signal part removed in 74b2300f3",
    "core/orchestration/subsystems/": "notification_daemon.py, imported by the unreachable core/skill_forge/skill_forge_v2.py",
}


def _present(path):
    """A superseded path is present when it holds SOURCE: a directory left with only a
    stale, untracked ``__pycache__`` (core/learning/completion_detectors/ after its
    modules were deleted) imports nothing - Python never loads bytecode whose source is
    gone - so it is not a superseded module coming back."""
    p = REPO / path
    if p.is_dir():
        return any(f.is_file() and "__pycache__" not in f.relative_to(p).parts
                   for f in p.rglob("*"))
    return p.exists()


def _uncovered(superseded, kept, present):
    return sorted(
        (adr, p) for adr, paths in superseded.items() for p in paths if present(p) and p not in kept
    )


def test_audit_covers_all_24_superseded_decisions():
    assert len(SUPERSEDED_PATHS) == 24
    assert sum(len(p) for p in SUPERSEDED_PATHS.values()) == 74


def test_every_present_superseded_path_is_named_as_kept():
    assert _uncovered(SUPERSEDED_PATHS, KEPT, _present) == []


@pytest.mark.parametrize("path", sorted(KEPT))
def test_kept_path_still_exists(path):
    assert _present(path), f"{path} is gone; drop it from KEPT and from the ADR-2282 table"


def test_uncovered_detection_positive_control():
    fake = {"ADR-0000": ("core/learning/quality_gate.py", "README.md")}
    assert _uncovered(fake, {}, lambda p: p == "core/learning/quality_gate.py") == [
        ("ADR-0000", "core/learning/quality_gate.py")
    ]
    real = {"ADR-0000": ("core/learning/__never_existed__.py", "README.md")}
    assert _uncovered(real, {}, _present) == [("ADR-0000", "README.md")]


def test_a_directory_with_only_stale_bytecode_is_not_present(tmp_path, monkeypatch):
    import sys

    mod = sys.modules[__name__]
    (tmp_path / "gone" / "__pycache__").mkdir(parents=True)
    (tmp_path / "gone" / "__pycache__" / "x.cpython-311.pyc").write_bytes(b"\0")
    (tmp_path / "back").mkdir()
    (tmp_path / "back" / "x.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(mod, "REPO", tmp_path)
    assert not _present("gone/")
    assert _present("back/")
