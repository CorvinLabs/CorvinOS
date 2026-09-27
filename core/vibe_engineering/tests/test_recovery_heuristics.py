"""ADR-2091 — the VibeEngine's recovery diagnosis is a deterministic heuristic
(the Hermes-Healing bridge was removed); behaviour carried over unchanged."""
import importlib.util

import pytest

from core.vibe_engineering.recovery_heuristics import (
    RecoveryDiagnosis, diagnose_error, map_to_recovery_strategy,
)


def test_hermes_bridge_module_is_gone():
    assert importlib.util.find_spec("core.vibe_engineering.hermes_bridge") is None


@pytest.mark.parametrize("err,strategy", [
    (TimeoutError("x"), "retry"),
    (ConnectionError("x"), "retry"),
    (Exception("request timed out after 30s"), "retry"),
    (MemoryError(), "decompose"),
    (Exception("input too complex for the skill"), "decompose"),
    (FileNotFoundError("gone"), "escalate"),
    (Exception("something odd"), "escalate"),
])
def test_heuristic_strategies(err, strategy):
    d = diagnose_error(err, {})
    assert isinstance(d, RecoveryDiagnosis)
    assert map_to_recovery_strategy(d) == strategy


def test_not_found_with_fallback_skill():
    d = diagnose_error(Exception("skill not found"), {"fallback_skills": ["alt"]})
    assert d.primary_strategy == "fallback" and d.fallback_skill == "alt"


def test_vibe_engine_has_no_hermes_client():
    import inspect
    from core.vibe_engineering.vibe_engine import VibeEngine
    assert "hermes_client" not in inspect.signature(VibeEngine.__init__).parameters
