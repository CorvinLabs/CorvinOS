"""Ratchet: production code must not grow NEW hardcoded Haiku 4.5 model ids.

ADR-2090: automatic routing and every default use the NEWEST model of a
family (``model_lineage.latest`` / ``model_selector.tier_model``). A literal
``claude-haiku-4-5`` in production code freezes that default on the day it is
written — the OS tier moved to Haiku 5.5 while helpers, ACS workers, the tier
map and the voice summariser kept serving 4.5.

``BASELINE`` lists the files that still carry the literal and how many times.
It may only SHRINK. Legitimate remaining uses: price/rank/label tables (an old
model must stay priced and ranked), curated last-resort fallbacks, and the
API-key paths (batch, CI review) whose model change needs its own review.
Fix a file, lower its number here. Never raise one.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PATTERN = re.compile(r"claude-haiku-4-5")

BASELINE: dict[str, int] = {
    "compliance/ci_review.py": 1,
    "core/compute/corvin_compute/engines/anthropic_batch.py": 2,
    "core/console/corvin_console/marketplace_integrations.py": 3,
    "core/console/corvin_console/routes/cost_insights_routes.py": 1,
    "core/console/corvin_console/routes/engine.py": 2,
    "core/console/corvin_console/routes/model_cost_optimizer_api.py": 2,
    "core/console/corvin_console/routes/model_selection_analytics.py": 1,
    "core/console/corvin_console/routes/workflows.py": 1,
    "core/console/corvin_core/execution_context.py": 1,
    "core/cost/calculator.py": 1,
    "core/cost/optimizer.py": 1,
    "core/delegate/corvin_delegate/output_judge.py": 1,
    "core/gateway/corvin_gateway/dispatcher.py": 4,
    "core/learning/model_selection_learner.py": 4,
    "core/license/models/billing.py": 2,
    "core/license/tier_enforcer.py": 2,
    "core/licensing/billing.py": 2,
    "core/models/model_selection_config.py": 3,
    "core/models/multi_model_router.py": 1,
    "core/skills/os_skills/intelligent_router.py": 1,
    "core/skills/os_skills/intelligent_router_integration.py": 1,
    "core/skills/os_skills/model_selector.py": 6,
    "core/skills/os_skills/model_selector_variants.py": 3,
    "core/skills/os_skills/workflow_optimizer_wrapper.py": 1,
    "corvin_operator/bridges/shared/acs_gate_chain.py": 1,
    "corvin_operator/bridges/shared/acs_runtime.py": 1,
    "corvin_operator/bridges/shared/adapter.py": 3,
    "corvin_operator/bridges/shared/compute_narrator.py": 1,
    "corvin_operator/bridges/shared/engine_models.py": 1,
    "corvin_operator/bridges/shared/helper_model.py": 2,
    "corvin_operator/bridges/shared/model_lineage.py": 1,
    "corvin_operator/bridges/shared/model_selector.py": 8,
    "corvin_operator/context_engineering/stages/llm_synthesis.py": 1,
    "corvin_operator/orchestration/tde/tde_engine.py": 1,
    "corvin_operator/voice/scripts/summarize.py": 2,
    "scripts/adr0377_phase3_cost_baseline.py": 2,
    "scripts/e2e_full_audit_fixture.py": 8,
    "scripts/phase3_comprehensive_benchmark.py": 4,
    "scripts/validate_model_selector_variants.py": 6
}


def _skip(rel: str) -> bool:
    return (
        rel.startswith(("benchmark/", "docs/", "tests/", ".corvin/"))
        or "/tests/" in rel
        or "/processed/" in rel
        or re.search(r"(^|/)test_[^/]*\.py$", rel) is not None
    )


def _current() -> dict[str, int]:
    out = subprocess.check_output(["git", "ls-files", "*.py"], cwd=REPO, text=True)
    found: dict[str, int] = {}
    for rel in out.split():
        if _skip(rel):
            continue
        try:
            n = len(PATTERN.findall((REPO / rel).read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            continue
        if n:
            found[rel] = n
    return found


def test_no_new_hardcoded_haiku_4_5() -> None:
    current = _current()
    # Positive control: the scan must actually see files, or "no growth" is vacuous.
    assert len(current) >= 10, f"scan saw only {len(current)} files - the walk is broken"
    grew = {
        f: (BASELINE.get(f, 0), n) for f, n in current.items() if n > BASELINE.get(f, 0)
    }
    assert not grew, (
        "hardcoded claude-haiku-4-5 grew (file: baseline -> now); resolve through "
        "model_lineage.latest('haiku') / model_selector.tier_model('haiku') instead: "
        f"{grew}"
    )


def test_baseline_only_shrinks() -> None:
    current = _current()
    stale = {f: (b, current.get(f, 0)) for f, b in BASELINE.items() if current.get(f, 0) < b}
    assert not stale, f"lower these BASELINE entries (file: baseline -> now): {stale}"
