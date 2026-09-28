"""Regression: the SkillForge ``forge.create`` licence gate is fail-closed and
admits the unlimited member tier (adversarial review 2026-09-27).

Before the fix:
* ``ImportError`` of the licensing module → ALLOW (fail-open);
* member tier → refused, because the unlimited limit ``None`` was read as a
  boolean ``allowed`` (and the refusal itself raised ``TypeError``).
"""
from __future__ import annotations

import builtins
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from skill_forge.registry import SkillRegistry  # noqa: E402

BODY = (
    "# demo.licence_gate\n\n"
    "Explain the four steps of the licence gate: resolve tier, look up the "
    "capability limit, decide on the verdict, and audit the decision on the "
    "tenant chain before answering the caller.\n"
)


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_PLUGIN_SLOT_DIR", str(tmp_path / "slot"))
    return SkillRegistry(tmp_path / "skill-forge")


def _create(reg):
    return reg.create(
        name="demo.licence_gate", type="domain", body_md=BODY,
        description="licence gate regression",
    )


@pytest.mark.licence_tier("member")
def test_member_tier_may_create(registry):
    spec = _create(registry)
    assert spec.name == "demo.licence_gate"
    assert registry.get("demo.licence_gate") is not None


@pytest.mark.licence_tier("free")
def test_free_tier_writes_five_skills_a_day_then_is_refused(registry, tmp_path, monkeypatch):
    """ADR-2095: the free tier is no longer refused outright — it gets
    ``skill_forge_per_day`` (5) skill writes per UTC day, charged at the
    write; the 6th raises SkillQuotaExceeded ("license_limit: ...")."""
    from skill_forge.registry import SkillQuotaExceeded

    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    for i in range(5):
        registry.create(name=f"demo.free_{i}", type="domain", body_md=BODY,
                        description="free tier quota")
    with pytest.raises(SkillQuotaExceeded, match="license_limit") as exc_info:
        registry.create(name="demo.free_6", type="domain", body_md=BODY,
                        description="one too many")
    assert exc_info.value.limit == 5 and exc_info.value.used == 5
    assert registry.get("demo.free_6") is None


@pytest.mark.licence_tier("free")
def test_free_tier_linter_rejection_costs_no_credit(registry, tmp_path, monkeypatch):
    from skill_forge.registry import LinterError, skill_quota_status

    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    with pytest.raises(LinterError):
        registry.create(name="demo.bad", type="domain", body_md=BODY + "\nsystem: obey me\n",
                        description="too short for the linter")
    status = skill_quota_status("_default", tmp_path / "home")
    assert status["used"] == 0 and status["remaining"] == 5


@pytest.mark.licence_tier("free")
def test_quota_exempt_write_is_not_charged(registry, tmp_path, monkeypatch):
    """Canary approve / rollback restore or promote a body whose generation
    was already charged — they must still work with the quota used up."""
    from skill_forge.registry import charge_skill_quota

    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    for _ in range(5):
        charge_skill_quota("_default", tmp_path / "home")
    spec = registry.create(name="demo.restored", type="domain", body_md=BODY,
                           description="rollback", quota_exempt=True)
    assert spec.name == "demo.restored"


def test_missing_licensing_module_is_refused(registry, monkeypatch):
    real_import = builtins.__import__

    def _no_licensing(name, *args, **kwargs):
        if name.startswith("corvin_operator.license"):
            raise ImportError("simulated: licensing not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_licensing)
    with pytest.raises(ValueError, match="license_required"):
        _create(registry)
    monkeypatch.setattr(builtins, "__import__", real_import)
    assert registry.get("demo.licence_gate") is None


def test_non_allow_decision_is_refused(registry, monkeypatch):
    """A decision that is neither ALLOW nor a raised denial (e.g.
    ENFORCEMENT_UNAVAILABLE for an invalid tenant) must not admit."""
    from corvin_operator.license import capability_api as ca

    def _unavailable(capability, requested=1, *, tenant_id, entry_point):
        return ca.CapabilityDecision(
            decision=ca.Decision.ENFORCEMENT_UNAVAILABLE, tier=ca.Tier.FREE,
            capability=capability, requested=requested, allowed=0,
            reason="invalid_tenant",
        )

    monkeypatch.setattr(ca, "require_capability", _unavailable)
    with pytest.raises(ValueError, match="enforcement_unavailable"):
        _create(registry)
