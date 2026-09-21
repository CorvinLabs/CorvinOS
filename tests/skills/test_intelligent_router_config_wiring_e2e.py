"""E2E wiring proof — ADR-0867 Phase 3 routing strategy config (spec.routing).

Proves the missing link the 2026-09-20 audit found: complexity_judge.py and
intelligent_router.py's route_with_judge() existed, but nothing in the
config-loading path ever selected it — IntelligentRouterBridge always called
router.route_task(), the pre-Phase-3 method.

This does NOT construct IntelligentRouter/ComplexityJudge directly and call a
method — that would be a unit test wearing an E2E label (see the CorvinOS
e2e-wiring-proof-standard). It goes through the real boundary instead:

    write tenant.corvin.yaml to disk (spec.routing.strategy: ...)
      -> corvin_gateway.tenant_config.load()  [real YAML + Pydantic validation]
      -> IntelligentRouterBridge.route_with_intelligent_selection(tenant_id=...)
      -> asserts the RoutingDecision reflects the configured strategy

Same prompt, same tenant machinery, only spec.routing.strategy differs between
the two halves of each test — isolating the config wiring as the variable.
"""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "core" / "gateway"))
sys.path.insert(0, str(_REPO / "corvin_operator" / "forge"))

from corvin_gateway.tenant_config import TenantConfig, save  # noqa: E402

from core.skills.os_skills.intelligent_router_integration import (  # noqa: E402
    IntelligentRouterBridge,
    _routing_strategy_for_tenant,
)

# "kurz aber komplex" — a short ethics question. Token count is tiny (SIMPLE
# by the token heuristic alone), but ComplexityJudge scores it COMPLEX with
# confidence 0.75 (2/3 signals agree — see complexity_judge.py's
# _compute_confidence). This is the exact case ADR-0867 Phase 3 exists for.
PROMPT = "Is lying ever morally justified?"
TENANT = "_default"


@contextmanager
def sandboxed_tenant(strategy: str, *, judge_enabled: bool):
    """Write a real tenant.corvin.yaml with spec.routing set, in an isolated
    CORVIN_HOME — never touches the operator's real ~/.corvin/."""
    with tempfile.TemporaryDirectory(prefix="ir-cfg-wiring-test-") as td:
        home = Path(td)
        prior = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = str(home)
        (home / "tenants" / TENANT / "global").mkdir(parents=True)
        try:
            cfg = TenantConfig.default(TENANT)
            cfg.spec.routing.strategy = strategy
            cfg.spec.routing.judge_enabled = judge_enabled
            save(cfg)
            IntelligentRouterBridge.reset_router()
            yield home
        finally:
            IntelligentRouterBridge.reset_router()
            if prior is None:
                os.environ.pop("CORVIN_HOME", None)
            else:
                os.environ["CORVIN_HOME"] = prior


class TestConfigResolution:
    """The resolver itself reads what was actually written to disk."""

    def test_phase2_conservative_resolves_from_disk(self):
        with sandboxed_tenant("phase2_conservative", judge_enabled=False):
            strategy, judge_enabled = _routing_strategy_for_tenant(TENANT)
            assert strategy == "phase2_conservative"
            assert judge_enabled is False

    def test_phase3_judge_resolves_from_disk(self):
        with sandboxed_tenant("phase3_judge", judge_enabled=True):
            strategy, judge_enabled = _routing_strategy_for_tenant(TENANT)
            assert strategy == "phase3_judge"
            assert judge_enabled is True

    def test_missing_tenant_config_fails_open_to_phase2(self):
        """No tenant.corvin.yaml at all -> phase2_conservative, never an
        unconfigured opt-in to the judge path."""
        with tempfile.TemporaryDirectory(prefix="ir-cfg-wiring-missing-") as td:
            prior = os.environ.get("CORVIN_HOME")
            os.environ["CORVIN_HOME"] = td
            try:
                strategy, judge_enabled = _routing_strategy_for_tenant(
                    "tenant_with_no_config_file"
                )
                assert strategy == "phase2_conservative"
                assert judge_enabled is False
            finally:
                if prior is None:
                    os.environ.pop("CORVIN_HOME", None)
                else:
                    os.environ["CORVIN_HOME"] = prior


class TestRoutingBehaviorFollowsConfig:
    """The actual production entry point (route_with_intelligent_selection)
    behaves differently for the identical prompt, purely as a function of
    the on-disk tenant config — proving the wiring, not just the resolver."""

    def test_phase2_conservative_ignores_judge_signal(self):
        with sandboxed_tenant("phase2_conservative", judge_enabled=False):
            decision = IntelligentRouterBridge.route_with_intelligent_selection(
                PROMPT, tenant_id=TENANT
            )
            # Token-only heuristic: this short prompt stays SIMPLE/Haiku.
            assert decision.tier == "simple"
            assert decision.model == "claude-haiku-4-5"
            assert "Judge" not in decision.reasoning

    def test_phase3_judge_enabled_applies_judge_override(self):
        with sandboxed_tenant("phase3_judge", judge_enabled=True):
            decision = IntelligentRouterBridge.route_with_intelligent_selection(
                PROMPT, tenant_id=TENANT
            )
            # ComplexityJudge overrides the token-only SIMPLE verdict to
            # COMPLEX/Opus for this "kurz aber komplex" prompt.
            assert decision.tier == "complex"
            assert decision.model == "claude-opus-5"
            assert "Judge" in decision.reasoning

    def test_phase3_judge_strategy_with_judge_disabled_falls_back(self):
        """The documented kill-switch: strategy=phase3_judge but
        judge_enabled=False must behave exactly like phase2_conservative."""
        with sandboxed_tenant("phase3_judge", judge_enabled=False):
            decision = IntelligentRouterBridge.route_with_intelligent_selection(
                PROMPT, tenant_id=TENANT
            )
            assert decision.tier == "simple"
            assert decision.model == "claude-haiku-4-5"
            assert "Judge" not in decision.reasoning

    def test_same_prompt_different_tenants_different_tiers(self):
        """The clearest wiring proof: identical prompt, identical process,
        only the on-disk config for the SAME tenant_id argument changes
        between the two calls -- and the routing decision changes with it."""
        with sandboxed_tenant("phase2_conservative", judge_enabled=False):
            baseline = IntelligentRouterBridge.route_with_intelligent_selection(
                PROMPT, tenant_id=TENANT
            )
        with sandboxed_tenant("phase3_judge", judge_enabled=True):
            judged = IntelligentRouterBridge.route_with_intelligent_selection(
                PROMPT, tenant_id=TENANT
            )
        assert baseline.tier != judged.tier
        assert baseline.tier == "simple"
        assert judged.tier == "complex"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
