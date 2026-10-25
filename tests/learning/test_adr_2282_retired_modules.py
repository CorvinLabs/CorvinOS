"""Guard for the ADR-2282 retirement (PLAN-0964 Welle 2, T-0264..T-0272, T-0266).

The listed modules were deleted because nothing in production reached them. This test keeps
them gone and keeps anything from importing them again:

* every path is absent from the tree (a revived file fails the test, the entry must be
  removed with the ADR that revives it);
* no tracked production file imports one of them. Imports are resolved from the AST,
  absolute and relative, so `from core.learning import base` counts as `core.learning.base`.

Boundary: static imports only. A module loaded by a string (`importlib.import_module`) or a
`sys.path` trick is not seen. The positive control below proves the scanner finds a real
import, so a green result is not an empty scan.
"""

import ast
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

RETIRED = (
    "core/console/corvin_console/routes/learning_loops",
    "core/console/corvin_console/routes/learning_optimizer_routes_stream2",
    "core/console/corvin_console/routes/skill_learning_routes",
    "core/console/corvin_console/routes/vibe_metrics_api",
    "core/console/routes/engine_api",
    "core/console/routes/learning_dashboard",
    "core/console/routes/media_routes",
    "core/console/routes/plugin_manager_api",
    "core/console/routes/subsystems",
    "core/console/routes/video_producer_metrics",
    "core/engine/meta_skills",
    "core/engine/skill_learning_loop",
    "core/learning/ab_test_framework_stream2",
    "core/learning/adaptive_strategy",
    "core/learning/alert_dispatcher_stream2",
    "core/learning/auto_grading",
    "core/learning/base",
    "core/learning/completion_detectors/agent_detector",
    "core/learning/completion_detectors/completion_event",
    "core/learning/completion_detectors/detector_base",
    "core/learning/completion_detectors/workflow_detector",
    "core/learning/confidence_optimizer",
    "core/learning/config_tuner_stream2",
    "core/learning/conflict_resolver",
    "core/learning/convergence_detector_stream2",
    "core/learning/dashboard_metrics_stream2",
    "core/learning/feedback_processor_stream2",
    "core/learning/hotfix_flow_stream2",
    "core/learning/ldd_learning_loop",
    "core/learning/learning_loop_audit_integration",
    "core/learning/learning_loop_manifest",
    "core/learning/live_experiment_collector",
    "core/learning/loss_signals/advanced_loss_signals",
    "core/learning/operator_fingerprint",
    "core/learning/outcome_sink_a2",
    "core/learning/quality_gate",
    "core/learning/rollback_guard",
    "core/learning/rollback_strategy_stream2",
    "core/learning/skill_optimizer",
    "core/learning/token_accounting",
    "core/learning/token_metrics_aggregator",
    "core/learning/tool_ranking",
    "core/learning/tool_ranking_cache",
    "core/learning/utils",
    "core/marketplace/community_submissions",
    "core/marketplace/distribution",
    "core/marketplace/ecosystem_monitoring",
    "core/marketplace/manifest_validator",
    "core/marketplace/plugin_distribution",
    "core/marketplace/plugin_learning_loop",
    "core/marketplace/plugin_registry",
    "core/marketplace/plugin_signing",
    "core/marketplace/plugin_tier_system",
    "core/marketplace/registry_v3_api",
    "core/marketplace/skill_ecosystem_launcher",
    "core/notification/notification_router",
    "core/notification/pii_scrubber",
    "core/notification/summary_generator",
    "core/orchestration/brain",
    "core/orchestration/brain_startup",
    "core/orchestration/cli",
    "core/orchestration/config",
    "core/orchestration/hub",
    "core/orchestration/subsystems/base",
    "core/orchestration/subsystems/btw_advisor",
    "core/orchestration/subsystems/context_bridge",
    "core/orchestration/subsystems/cost_controller",
    "core/orchestration/subsystems/forge_api_impl",
    "core/orchestration/subsystems/forge_apis",
    "core/orchestration/subsystems/health_monitor",
    "core/orchestration/subsystems/learning_engine",
    "core/orchestration/subsystems/loop_engineer",
    "core/orchestration/subsystems/memory_manager",
    "core/orchestration/subsystems/orchestrator",
    "core/orchestration/subsystems/safety_validator",
    "core/orchestration/subsystems/session_lifecycle",
    "core/orchestration/subsystems/session_manager",
    "core/orchestration/subsystems/skill_forge_subsystem",
    "core/orchestration/subsystems/strategy_advisor",
    "core/orchestration/subsystems/task_manager",
    "core/orchestration/subsystems/token_budget",
    "core/orchestration/subsystems/tool_forge_subsystem",
    "core/orchestration/subsystems/voice_coordinator",
    "core/skills/os_skills/definition_of_done_verifier/hardening",
    "core/skills/os_skills/definition_of_done_verifier/learning_integration",
    "core/skills/os_skills/flow_guard/data_classifier_learned",
    "core/skills/os_skills/flow_guard/exception_system",
    "core/skills/os_skills/flow_guard/feedback_handler",
    "core/skills/os_skills/flow_guard/learning_loop_phase3",
    "core/skills/os_skills/flow_guard/policy_confidence_scorer",
    "core/skills/os_skills/flow_guard/threshold_tuner",
    "core/skills/os_skills/workflow_optimizer_skill/confidence_calculator",
    "core/skills/skill_learning_loop",
    "core/skills/skill_optimizer",
    "core/vibe/_chain_audit",
    "core/vibe/learning_loop_tracker",
    "core/vibe/loss_signal_emitter",
    "core/vibe/marketplace_unified",
    "core/vibe/vibe_hub_core",
    "corvin_operator/context_engineering/adr_reranking",
    "corvin_operator/context_engineering/memory_pruning",
    "corvin_operator/context_engineering/selective_injection",
    "corvin_operator/context_engineering/stages/adr_reranking_stage",
    "corvin_operator/context_engineering/stages/memory_pruning_stage",
    "corvin_operator/context_engineering/stages/selective_injection_stage",
)

RETIRED_MODULES = frozenset(p.replace("/", ".") for p in RETIRED)


def _tracked_production_files():
    out = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    for rel in out:
        parts = rel.split("/")
        name = parts[-1]
        if "tests" in parts or "test" in parts or name.startswith("test_") or name == "conftest.py":
            continue
        if (REPO / rel).is_file():
            yield rel


def _imports_of(rel, source):
    """Dotted module names imported by one file, relative imports resolved."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    here = rel[:-3].split("/")
    package = here[:-1] if here[-1] != "__init__" else here[:-1]
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)] if node.level > 1 else package
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            found.add(module)
            found.update(f"{module}.{alias.name}" for alias in node.names)
    return found


def _offenders(files):
    hits = []
    for rel, source in files:
        for name in _imports_of(rel, source):
            if name in RETIRED_MODULES:
                hits.append((rel, name))
    return hits


@pytest.mark.parametrize("path", RETIRED)
def test_retired_module_stays_deleted(path):
    assert not (REPO / f"{path}.py").exists(), (
        f"{path}.py is back; ADR-2282 retired it. Remove the entry in the same change "
        "that revives it, with the ADR that says why."
    )


def test_no_production_file_imports_a_retired_module():
    files = [(rel, (REPO / rel).read_text(encoding="utf-8", errors="replace"))
             for rel in _tracked_production_files()]
    assert len(files) > 500, "the scan saw almost no files; git ls-files returned too little"
    assert _offenders(files) == []


def test_scanner_finds_absolute_and_relative_imports_positive_control():
    sample = "from core.learning import base\nimport core.learning.quality_gate\n"
    hits = _offenders([("core/other/x.py", sample)])
    assert ("core/other/x.py", "core.learning.base") in hits
    assert ("core/other/x.py", "core.learning.quality_gate") in hits
    rel = _offenders([("core/learning/y.py", "from . import conflict_resolver\n")])
    assert ("core/learning/y.py", "core.learning.conflict_resolver") in rel
