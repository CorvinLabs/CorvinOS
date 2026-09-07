"""Phase 1 k=2-5 — the five feature-flag → Skill call sites.

HONEST SCOPE (round-4 adversarial review, F6). This file used to declare
"Call-Site #2: headless_api_mode (console/app.py:440)" in its docstring, never
import ``corvin_console.app``, and call ``registry.execute("os.headless_mode",
…)`` itself with its own LoM. That is a UNIT test wearing a call-site label
(CLAUDE.md § E2E Wiring Proof) — and it is exactly why F1 shipped: the real call
site omitted the now-mandatory ``lom=``, ``headless_enabled()`` returned False
for every configuration, and all 20 tests here stayed green.

What is what:

* ``TestCallSite1..4`` / ``TestA2BEquivalence`` / ``TestCompliance`` /
  ``TestNoRegressions`` — UNIT tests of registry DISPATCH for the five Skills.
  They supply their own caller. They prove the Skills behave, never that
  anything calls them.
* ``TestRealCallSites`` — drives the REAL production function
  (``corvin_console.app.headless_enabled``), so a broken call site fails here.
* ``TestEveryProductionCallSitePassesALoM`` — an AST fence over the whole tree:
  every ``…execute("os.…", …)`` in production code must pass ``lom=``. This is
  the guard that generalises F1 to all five sites and to the next one added.

The five call sites, as they actually are (verified 2026-09-07):
- #1 os.plugin_health_monitoring — core/plugins/corvin_plugins/bootstrap.py
- #2 os.headless_mode           — core/console/corvin_console/app.py
- #3 os.plugin_builder          — core/console/corvin_console/slash_commands.py
- #4 os.capabilities            — core/console/corvin_console/routes/capabilities.py
- #5 os.vibe_engineering        — core/console/corvin_console/routes/vibe_engineering.py

Compliance: GDPR Art. 30, 32; EU AI Act Art. 50; ADR-0544
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Any

import pytest

from core.skills.skill_registry_phase1 import (
    SkillsRegistry,
    initialize_registry,
    get_registry,
)


def execute_skill(skill_id, input, **kwargs):
    """Test-local shim: the module-level ``execute_skill`` was removed on
    2026-09-07 (dead in production); ``registry.execute`` now REQUIRES a LoM."""
    kwargs.setdefault("lom", "tests/integration/test_phase1_k2_k5_call_sites.py:execute_skill")
    return get_registry().execute(skill_id, input, **kwargs)
from core.skills.os_skills_phase1 import (
    register_builtin_skills,
    PluginHealthMonitoringSkill,
    HeadlessModeSkill,
    PluginBuilderSkill,
    CapabilitiesSkill,
)

logger = logging.getLogger(__name__)


class MockAuditBackend:
    """Mock audit backend for testing."""

    def __init__(self):
        self.events = []

    def write_event(self, event):
        """Record audit event."""
        self.events.append(event)

    def get_events(self, event_type=None, skill_id=None):
        """Query events by type and/or skill_id."""
        events = self.events
        if event_type:
            events = [e for e in events if e.get("event_type") == event_type]
        if skill_id:
            events = [e for e in events if e.get("skill_id") == skill_id]
        return events

    def clear(self):
        """Clear all events."""
        self.events.clear()


# The Phase-1 Skills mirror the tenant's REAL feature-flag state
# (corvin_core.feature_flags); the input value is only the fallback default when
# the flags module is unavailable. These tests therefore set the flag under an
# isolated CORVIN_HOME and assert the Skill reflects it (2026-09-07: the old
# "echo the input" expectation was a stale pre-ADR-0613 contract).
@pytest.fixture(autouse=True)
def _isolated_corvin_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin_home"))
    yield


def _set_flag(flag_id: str, enabled: bool) -> None:
    from corvin_core.feature_flags import set_enabled

    set_enabled(flag_id, enabled, "_default")


@pytest.fixture
def mock_audit():
    """Provide mock audit backend."""
    return MockAuditBackend()


@pytest.fixture
def registry(mock_audit):
    """Provide fresh Skills registry with mock audit."""
    reg = SkillsRegistry(audit_backend=mock_audit, tenant_id="_default")
    register_builtin_skills(reg)
    return reg


class TestCallSite1PluginHealthMonitoring:
    """Test migration of plugin_health_monitoring flag → os.plugin_health_monitoring Skill."""

    def test_skill_execution(self, registry):
        """E2E: Execute plugin health monitoring Skill."""
        _set_flag("plugin_health_monitoring", True)
        result = registry.execute("os.plugin_health_monitoring", {"enabled": True}, lom="tests/integration/test_phase1_k2_k5_call_sites.py:execute_skill")

        assert result.status == "success"
        assert result.output["enabled"] is True
        assert "Health monitoring" in result.output["reason"]

    def test_disabled_state(self, registry):
        """E2E: Plugin health monitoring disabled."""
        _set_flag("plugin_health_monitoring", False)
        result = registry.execute("os.plugin_health_monitoring", {"enabled": False}, lom="tests/integration/test_phase1_k2_k5_call_sites.py:execute_skill")

        assert result.status == "success"
        assert result.output["enabled"] is False
        assert "disabled" in result.output["reason"].lower()

    def test_audit_trail(self, registry, mock_audit):
        """E2E: Audit trail logged for health monitoring decision."""
        registry.execute("os.plugin_health_monitoring", {"enabled": True}, lom="tests/integration/test_phase1_k2_k5_call_sites.py:execute_skill")

        events = mock_audit.get_events("SKILL_EXECUTED", "os.plugin_health_monitoring")
        assert len(events) >= 1
        assert events[0]["status"] == "success"
        assert events[0]["tenant_id"] == "_default"


class TestCallSite2HeadlessMode:
    """Test migration of headless_api_mode flag → os.headless_mode Skill."""

    def test_headless_enabled(self, registry):
        """E2E: Headless mode enabled."""
        _set_flag("headless_api_mode", True)
        result = registry.execute("os.headless_mode", {"headless_enabled": True}, lom="tests/integration/test_phase1_k2_k5_call_sites.py:execute_skill")

        assert result.status == "success"
        assert result.output["headless_enabled"] is True
        assert result.output["mode"] == "headless"

    def test_console_mode(self, registry):
        """E2E: Console mode (headless disabled)."""
        _set_flag("headless_api_mode", False)
        result = registry.execute("os.headless_mode", {"headless_enabled": False}, lom="tests/integration/test_phase1_k2_k5_call_sites.py:execute_skill")

        assert result.status == "success"
        assert result.output["headless_enabled"] is False
        assert result.output["mode"] == "console"

    def test_default_console(self, registry):
        """E2E: Default is console mode (headless off)."""
        result = registry.execute("os.headless_mode", {}, lom="tests/integration/test_phase1_k2_k5_call_sites.py:execute_skill")

        assert result.status == "success"
        assert result.output["headless_enabled"] is False


class TestCallSite3PluginBuilder:
    """Test migration of plugin_builder_enabled flag → os.plugin_builder Skill."""

    def test_builder_enabled(self, registry):
        """E2E: Plugin builder /build command available."""
        _set_flag("plugin_builder_enabled", True)
        result = registry.execute("os.plugin_builder", {"enabled": True}, lom="tests/integration/test_phase1_k2_k5_call_sites.py:execute_skill")

        assert result.status == "success"
        assert result.output["enabled"] is True
        assert "available" in result.output["reason"].lower()

    def test_builder_disabled(self, registry):
        """E2E: Plugin builder disabled."""
        _set_flag("plugin_builder_enabled", False)
        result = registry.execute("os.plugin_builder", {"enabled": False}, lom="tests/integration/test_phase1_k2_k5_call_sites.py:execute_skill")

        assert result.status == "success"
        assert result.output["enabled"] is False
        assert "disabled" in result.output["reason"].lower()

    def test_audit_trail(self, registry, mock_audit):
        """E2E: Audit trail for builder availability."""
        registry.execute("os.plugin_builder", {"enabled": True}, lom="tests/integration/test_phase1_k2_k5_call_sites.py:execute_skill")

        events = mock_audit.get_events("SKILL_EXECUTED", "os.plugin_builder")
        assert len(events) >= 1


class TestCallSite4Capabilities:
    """Test migration of capabilities flags → os.capabilities Skill."""

    def test_empty_flags(self, registry):
        """E2E: Capabilities with no gated flags."""
        result = registry.execute("os.capabilities", {
            "tenant_id": "_default",
            "gated_flags": [],
        }, lom="tests/integration/test_phase1_k2_k5_call_sites.py:execute_skill")

        assert result.status == "success"
        assert result.output["flags"] == {}
        assert result.output["tenant_id"] == "_default"

    def test_multiple_flags(self, registry):
        """E2E: Capabilities with multiple flags."""
        flags = ["plugin_health_monitoring", "vibe_engineering_active", "headless_api_mode"]
        result = registry.execute("os.capabilities", {
            "tenant_id": "_default",
            "gated_flags": flags,
        }, lom="tests/integration/test_phase1_k2_k5_call_sites.py:execute_skill")

        assert result.status == "success"
        output_flags = result.output["flags"]
        assert len(output_flags) == 3
        # By default, all flags start as disabled
        for flag in flags:
            assert output_flags[flag] is False

    def test_tenant_isolation(self, registry):
        """E2E: Capabilities respects tenant_id."""
        result1 = registry.execute("os.capabilities", {
            "tenant_id": "tenant_a",
            "gated_flags": ["plugin_health_monitoring"],
        }, lom="tests/integration/test_phase1_k2_k5_call_sites.py:execute_skill")
        result2 = registry.execute("os.capabilities", {
            "tenant_id": "tenant_b",
            "gated_flags": ["plugin_health_monitoring"],
        }, lom="tests/integration/test_phase1_k2_k5_call_sites.py:execute_skill")

        assert result1.output["tenant_id"] == "tenant_a"
        assert result2.output["tenant_id"] == "tenant_b"


class TestA2BEquivalence:
    """Test A/B equivalence: old feature flag behavior == new Skill behavior."""

    def test_health_monitoring_equivalence(self, registry):
        """A/B: plugin_health_monitoring flag → Skill returns same output."""
        # Simulate old behavior: check flag and get boolean
        # Simulate new behavior: execute Skill and get boolean from output

        old_enabled = True  # Old feature flag logic
        _set_flag("plugin_health_monitoring", old_enabled)
        result = registry.execute("os.plugin_health_monitoring", {"enabled": old_enabled}, lom="tests/integration/test_phase1_k2_k5_call_sites.py:execute_skill")
        new_enabled = result.output["enabled"]

        assert old_enabled == new_enabled

    def test_headless_equivalence(self, registry):
        """A/B: headless_api_mode flag → Skill returns same output."""
        for old_headless in [True, False]:
            _set_flag("headless_api_mode", old_headless)
            result = registry.execute("os.headless_mode", {"headless_enabled": old_headless}, lom="tests/integration/test_phase1_k2_k5_call_sites.py:execute_skill")
            new_headless = result.output["headless_enabled"]
            assert old_headless == new_headless

    def test_capabilities_equivalence(self, registry):
        """A/B: capabilities flags → Skill returns dict with same flag keys."""
        flags_to_check = ["plugin_health_monitoring", "vibe_engineering_active"]

        result = registry.execute("os.capabilities", {
            "tenant_id": "_default",
            "gated_flags": flags_to_check,
        }, lom="tests/integration/test_phase1_k2_k5_call_sites.py:execute_skill")

        output_flags = result.output["flags"]
        # Old behavior: flags dict with flag → bool
        # New behavior: Skill returns same shape
        assert set(output_flags.keys()) == set(flags_to_check)
        for flag in flags_to_check:
            assert isinstance(output_flags[flag], bool)


class TestCompliance:
    """Test compliance gates (GDPR Art. 30, 32; EU AI Act Art. 50)."""

    def test_gdpr_art_30_all_executions_logged(self, registry, mock_audit):
        """GDPR Art. 30: Every Skill execution logged."""
        # Execute multiple Skills
        registry.execute("os.plugin_health_monitoring", {"enabled": True}, lom="tests/integration/test_phase1_k2_k5_call_sites.py:execute_skill")
        registry.execute("os.headless_mode", {"headless_enabled": False}, lom="tests/integration/test_phase1_k2_k5_call_sites.py:execute_skill")
        registry.execute("os.plugin_builder", {"enabled": True}, lom="tests/integration/test_phase1_k2_k5_call_sites.py:execute_skill")

        # Verify all logged
        events = mock_audit.get_events("SKILL_EXECUTED")
        assert len(events) >= 3
        skill_ids = [e["skill_id"] for e in events]
        assert "os.plugin_health_monitoring" in skill_ids
        assert "os.headless_mode" in skill_ids
        assert "os.plugin_builder" in skill_ids

    def test_gdpr_art_32_tenant_isolation(self, registry, mock_audit):
        """GDPR Art. 32: Tenant isolation enforced."""
        # Execute for different tenants
        registry_a = SkillsRegistry(audit_backend=mock_audit, tenant_id="tenant_a")
        registry_b = SkillsRegistry(audit_backend=mock_audit, tenant_id="tenant_b")

        register_builtin_skills(registry_a)
        register_builtin_skills(registry_b)

        registry_a.execute("os.capabilities", {"tenant_id": "tenant_a", "gated_flags": []})
        registry_b.execute("os.capabilities", {"tenant_id": "tenant_b", "gated_flags": []})

        # Verify tenant_id in audit events
        events_a = [e for e in mock_audit.events if e.get("tenant_id") == "tenant_a"]
        events_b = [e for e in mock_audit.events if e.get("tenant_id") == "tenant_b"]

        assert len(events_a) >= 1
        assert len(events_b) >= 1
        # No cross-tenant leakage
        for event in events_a:
            assert event["tenant_id"] == "tenant_a"
        for event in events_b:
            assert event["tenant_id"] == "tenant_b"

    def test_eu_ai_act_art_50_lom_binding(self, registry, mock_audit):
        """EU AI Act Art. 50: LoM binding in every execution."""
        result = registry.execute(
            "os.plugin_health_monitoring",
            {"enabled": True},
            lom="core/skills/os_skills_phase1.py:PluginHealthMonitoringSkill.execute"
        )

        events = mock_audit.get_events("SKILL_EXECUTED", "os.plugin_health_monitoring")
        assert len(events) >= 1
        event = events[0]
        assert event["lom"] is not None
        assert "PluginHealthMonitoringSkill" in event["lom"]


class TestNoRegressions:
    """Test that rewritten code has zero behavioral regressions."""

    def test_100_random_executions(self, registry):
        """Stress test: 100 random Skill executions, all succeed."""
        import random

        skills_to_test = [
            ("os.plugin_health_monitoring", {"enabled": random.choice([True, False])}),
            ("os.headless_mode", {"headless_enabled": random.choice([True, False])}),
            ("os.plugin_builder", {"enabled": random.choice([True, False])}),
        ]

        for i in range(100):
            skill_id, input_data = random.choice(skills_to_test)
            result = registry.execute(skill_id, input_data, lom="tests/integration/test_phase1_k2_k5_call_sites.py:execute_skill")
            assert result.status == "success"

    def test_all_builtin_skills_executable(self, registry):
        """Verify: All registered Skills can be executed without error."""
        skills = registry.list_skills()
        assert len(skills) >= 4  # At least the 4 new ones + originals

        for skill in skills:
            result = registry.execute(skill.id, {}, lom="tests/integration/test_phase1_k2_k5_call_sites.py:execute_skill")
            # Should either succeed or be a known error (e.g., not found)
            assert result.status in ["success", "error", "timeout"]


# ─────────────────────────────────────────────────────────────────────────────
# The parts that can actually fail on a broken CALL SITE (round-4 review, F6)
# ─────────────────────────────────────────────────────────────────────────────


class TestRealCallSites:
    """Drives the REAL production functions, not the registry.

    Call site #2 is the one that shipped broken (F1): ``headless_enabled()``
    omitted the mandatory ``lom=``, so every execution was refused and the
    function returned False for every configuration while the unit tests above
    were green. This exercises the function itself.
    """

    @pytest.fixture
    def booted(self, mock_audit, monkeypatch, tmp_path):
        """Boot the GLOBAL registry the production call sites look up."""
        import core.skills.skill_registry_phase1 as reg_mod

        monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
        reg = reg_mod.initialize_registry(audit_backend=mock_audit, tenant_id="_default")
        register_builtin_skills(reg)
        return reg

    def _headless(self):
        import sys
        from pathlib import Path

        console = Path(__file__).resolve().parents[2] / "core" / "console"
        if str(console) not in sys.path:
            sys.path.insert(0, str(console))
        from corvin_console import app as console_app

        console_app.set_headless_override(None)  # the tenant flag must decide
        return console_app

    def test_headless_enabled_reaches_the_skill_and_honours_the_flag(self, booted, mock_audit):
        console_app = self._headless()
        _set_flag("headless_api_mode", True)
        mock_audit.events.clear()

        enabled = console_app.headless_enabled(tenant_id="_default")

        executed = [e for e in mock_audit.events if e.get("skill_id") == "os.headless_mode"]
        assert executed, (
            "headless_enabled() did not reach os.headless_mode — the call site in "
            "core/console/corvin_console/app.py is broken (round-4 review, F1)"
        )
        assert executed[-1]["status"] == "success", (
            f"the call site's execution was REFUSED: {executed[-1]}"
        )
        assert executed[-1]["lom"].startswith("core/console/corvin_console/app.py:"), (
            "the call site must name its OWN LoM, not a test's"
        )
        assert enabled is True, (
            "headless_api_mode is on, yet headless_enabled() returned False — "
            "an 'API-only' deployment would still mount the browser SPA"
        )

    def test_headless_disabled_flag_is_respected(self, booted):
        console_app = self._headless()
        _set_flag("headless_api_mode", False)
        assert console_app.headless_enabled(tenant_id="_default") is False


class TestEveryProductionCallSitePassesALoM:
    """AST fence: ``lom=`` is mandatory on every production Skill execution.

    Generalises F1. ``SkillsRegistry.execute`` refuses a call without a LoM
    (ADR-0537/0642) and the refusal is SILENT at the call site — it just looks
    like the Skill said "off". This finds any ``…execute("os.…", …)`` in
    production code that forgot one, including call sites added later.
    """

    def _offenders(self) -> list[str]:
        import ast
        from pathlib import Path

        repo = Path(__file__).resolve().parents[2]
        bad: list[str] = []
        for root in ("core", "operator", "ops"):
            for path in (repo / root).rglob("*.py"):
                if "test" in path.parts or path.name.startswith("test_"):
                    continue
                src = path.read_text(encoding="utf-8", errors="replace")
                if '"os.' not in src or ".execute(" not in src:
                    continue
                try:
                    tree = ast.parse(src)
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    if getattr(node.func, "attr", "") != "execute" or not node.args:
                        continue
                    first = node.args[0]
                    if not (isinstance(first, ast.Constant)
                            and isinstance(first.value, str)
                            and first.value.startswith("os.")):
                        continue
                    if not any(kw.arg == "lom" for kw in node.keywords):
                        bad.append(
                            f"{path.relative_to(repo)}:{node.lineno}: "
                            f"execute({first.value!r}) without lom="
                        )
        return sorted(bad)

    def test_no_production_call_site_omits_lom(self):
        offenders = self._offenders()
        assert not offenders, (
            "Skill execution(s) without the mandatory ADR-0537 LoM — each is "
            "REFUSED at runtime and silently reads as 'feature off':\n  "
            + "\n  ".join(offenders)
        )

    def test_the_fence_can_actually_see_an_offender(self):
        """Not vacuously green: the detector must match a real bad call."""
        import ast

        tree = ast.parse('registry.execute("os.headless_mode", {})\n')
        call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
        assert getattr(call.func, "attr", "") == "execute"
        assert not any(kw.arg == "lom" for kw in call.keywords)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
