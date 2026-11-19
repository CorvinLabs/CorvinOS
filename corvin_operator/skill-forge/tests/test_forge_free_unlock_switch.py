"""One switch for the Free-tier Forge unlock, and an honest tier (review 2026-11-19, TEN-6).

* ``limits.FORGE_FREE_UNLOCK`` is the ONLY place the unlock lives; the three
  values it controls are derived from it and move together.
* While the unlock is on, ``forge.create`` is ALLOWED on Free. That must not
  make a Free install report itself as ``member``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from skill_forge import registry as reg_mod  # noqa: E402
from skill_forge.registry import SkillRegistry  # noqa: E402

LIMITS_SRC = Path(__file__).resolve().parents[2] / "license" / "limits.py"

BODY = (
    "# demo.free_unlock\n\n"
    "Explain the four steps of the licence gate: resolve tier, look up the "
    "capability limit, decide on the verdict, and audit the decision on the "
    "tenant chain before answering the caller.\n"
)


def _limits_with(unlock: bool):
    """Execute limits.py with the switch forced, without touching the real module."""
    src = LIMITS_SRC.read_text(encoding="utf-8")
    line = "FORGE_FREE_UNLOCK: bool = True"
    assert src.count(line) == 1, "the switch must be defined exactly once"
    ns: dict = {"__name__": "limits_under_test"}
    exec(compile(src.replace(line, f"FORGE_FREE_UNLOCK: bool = {unlock}"), str(LIMITS_SRC), "exec"), ns)
    return ns


def test_switch_on_unlocks_all_three_together():
    ns = _limits_with(True)
    assert ns["FREE_TIER"]["tool_forge_per_day"] is None
    assert ns["FREE_TIER"]["skill_forge_per_day"] is None
    assert ns["CAPABILITIES"]["forge.create"]["free"]["limit"] is None


def test_switch_off_restores_the_member_only_model():
    ns = _limits_with(False)
    assert ns["FREE_TIER"]["tool_forge_per_day"] == 3
    assert ns["FREE_TIER"]["skill_forge_per_day"] == 5
    assert ns["CAPABILITIES"]["forge.create"]["free"]["limit"] == 0
    # Member is never affected by the switch.
    assert ns["TIER_RESOURCE_LIMITS"]["member"]["tool_forge_per_day"] is None
    assert ns["CAPABILITIES"]["forge.create"]["member"]["limit"] is None


def test_no_second_literal_for_the_three_values():
    """The derived values are referenced, never re-typed as literals."""
    src = LIMITS_SRC.read_text(encoding="utf-8")
    assert '"tool_forge_per_day":       _FREE_TOOL_FORGE_PER_DAY' in src
    assert '"skill_forge_per_day":      _FREE_SKILL_FORGE_PER_DAY' in src
    assert '"free": {"limit": _FREE_FORGE_CREATE_LIMIT}' in src
    assert "Tool Forge stays member-only" not in src


def test_shipped_behaviour_free_is_unlimited():
    """The current operator decision: the unlock is ON."""
    from corvin_operator.license import limits
    assert limits.FORGE_FREE_UNLOCK is True
    assert limits.FREE_TIER["tool_forge_per_day"] is None
    assert limits.FREE_TIER["skill_forge_per_day"] is None
    assert limits.CAPABILITIES["forge.create"]["free"]["limit"] is None


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_PLUGIN_SLOT_DIR", str(tmp_path / "slot"))
    return SkillRegistry(tmp_path / "skill-forge")


@pytest.mark.licence_tier("free")
def test_free_install_is_reported_as_free_not_member(registry):
    """forge.create is ALLOW on Free while unlocked - the tier is still 'free'."""
    from corvin_operator.license import capability_api as ca
    decision = ca.require_capability(
        "forge.create", requested=1, tenant_id="_default", entry_point="test")
    assert decision.decision is ca.Decision.ALLOW and decision.tier is ca.Tier.FREE

    assert reg_mod.skill_authoring_tier("_default") == "free"
    tenant_id, home = reg_mod._tenant_and_home(registry.root)
    status = reg_mod.skill_quota_status(tenant_id, home)
    assert status["tier"] == "free"
    assert status["limit"] is None and status["remaining"] is None   # unlimited


@pytest.mark.licence_tier("free")
def test_free_install_still_writes_without_a_limit(registry):
    for i in range(7):   # more than the 5/day of the locked model
        registry.create(name=f"demo.free_unlock_{i}", type="domain", body_md=BODY,
                        description="free unlock")
    assert registry.get("demo.free_unlock_6") is not None


@pytest.mark.licence_tier("member")
def test_member_install_is_reported_as_member(registry):
    assert reg_mod.skill_authoring_tier("_default") == "member"
    tenant_id, home = reg_mod._tenant_and_home(registry.root)
    assert reg_mod.skill_quota_status(tenant_id, home)["tier"] == "member"
