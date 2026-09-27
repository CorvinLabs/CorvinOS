"""E2E Tests for Marketplace Skill Ecosystem Launch (Phase 3, Week 3+).

Test scenarios:
  1. Skill discovery + installation flow
  2. Community plugin registration + ratings
  3. Multi-tenant skill isolation (ADR-0007)
  4. Ecosystem health monitoring
  5. Learning loop integration (ADR-0314)
  6. Audit trail verification (ADR-0232/0233)

Compliance:
  - Tenant isolation enforced
  - No cross-tenant leakage
  - All health events logged
  - Immutable audit trail
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import pytest

from core.marketplace.skill_ecosystem_launcher import (
    SkillEcosystemLauncher,
    SkillManifest,
    SkillTier,
    SkillCategory,
    SkillInstallation,
    TenantSkillRegistry,
    CommunitySkillFramework,
)
from core.marketplace.ecosystem_monitoring import (
    EcosystemMonitor,
    SkillHealthMetrics,
    HealthStatus,
    AlertSeverity,
)


class MockAuditCallback:
    """Mock audit callback for testing."""

    def __init__(self):
        self.events = []

    def __call__(self, event: dict[str, Any]) -> None:
        self.events.append({
            **event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def get_events_by_type(self, event_type: str) -> list[dict]:
        return [e for e in self.events if e.get("event_type") == event_type]


@pytest.fixture
def temp_marketplace():
    """Create temporary marketplace directory with sample skills."""
    with tempfile.TemporaryDirectory() as tmpdir:
        marketplace = Path(tmpdir) / "marketplace"
        marketplace.mkdir()

        # Create buildin skills
        buildin_memory = marketplace / "plugins" / "buildin" / "memory" / "cel_session_memory"
        buildin_memory.mkdir(parents=True)

        session_manifest = {
            "id": "skill:buildin-memory-cel_session_memory",
            "name": "CEL Session Memory",
            "version": "1.0.0",
            "author": "Anthropic",
            "license": "Apache-2.0",
            "tier": "buildin",
            "category": "memory",
            "description": "Session memory using CEL expressions",
            "supports_source": True,
            "supports_wheel": True,
            "boot_layer": "bundled",
            "audit_events": ["memory_snapshot", "memory_restored"],
            "learning_loop_enabled": True,
            "sla_level": "buildin",
        }
        (buildin_memory / "skill.json").write_text(json.dumps(session_manifest))

        # Create contributor skill
        contrib_memory = marketplace / "plugins" / "contributor" / "memory" / "user_model"
        contrib_memory.mkdir(parents=True)

        user_model_manifest = {
            "id": "skill:contributor-memory-user_model",
            "name": "User Model",
            "version": "0.5.0",
            "author": "Community",
            "license": "MIT",
            "tier": "contributor",
            "category": "memory",
            "description": "Community user model skill",
            "supports_source": True,
            "supports_wheel": False,
            "boot_layer": "installed",
            "audit_events": ["model_updated"],
            "learning_loop_enabled": True,
            "sla_level": "community",
        }
        (contrib_memory / "skill.json").write_text(json.dumps(user_model_manifest))

        yield marketplace


@pytest.fixture
def launcher(temp_marketplace):
    """Create SkillEcosystemLauncher with temp marketplace."""
    audit_callback = MockAuditCallback()
    return SkillEcosystemLauncher(
        marketplace_root=temp_marketplace,
        audit_callback=audit_callback,
    )


@pytest.fixture
def monitor():
    """Create EcosystemMonitor."""
    with tempfile.TemporaryDirectory() as tmpdir:
        return EcosystemMonitor(data_dir=Path(tmpdir))


class TestSkillDiscovery:
    """Test skill discovery from marketplace."""

    def test_load_marketplace_index(self, launcher):
        """Test loading marketplace index."""
        index = launcher.load_marketplace_index()

        assert index.total_skills >= 2
        assert len(index.skills) >= 2

        # Check both buildin and contributor skills loaded
        tiers = {s.tier for s in index.skills}
        assert SkillTier.BUILDIN in tiers
        assert SkillTier.CONTRIBUTOR in tiers

    def test_discover_by_category(self, launcher):
        """Test discovering skills by category."""
        launcher.load_marketplace_index()

        memory_skills = launcher.discover_skills(category=SkillCategory.MEMORY)
        assert len(memory_skills) >= 2

        for skill in memory_skills:
            assert skill.category == SkillCategory.MEMORY

    def test_discover_by_tier(self, launcher):
        """Test discovering skills by tier."""
        launcher.load_marketplace_index()

        buildin_skills = launcher.discover_skills(tier=SkillTier.BUILDIN)
        assert len(buildin_skills) >= 1

        for skill in buildin_skills:
            assert skill.tier == SkillTier.BUILDIN

        contributor_skills = launcher.discover_skills(tier=SkillTier.CONTRIBUTOR)
        assert len(contributor_skills) >= 1

        for skill in contributor_skills:
            assert skill.tier == SkillTier.CONTRIBUTOR

    def test_search_skills(self, launcher):
        """Test skill search."""
        launcher.load_marketplace_index()

        results = launcher.discover_skills(search_text="CEL")
        assert len(results) >= 1
        assert "CEL" in results[0].name

    def test_get_skill_details(self, launcher):
        """Test getting skill details."""
        launcher.load_marketplace_index()

        skill = launcher.get_skill_details("skill:buildin-memory-cel_session_memory")
        assert skill is not None
        assert skill.name == "CEL Session Memory"
        assert skill.tier == SkillTier.BUILDIN

    def test_audit_index_loaded(self, launcher):
        """Test audit event for index load."""
        launcher.load_marketplace_index()

        audit_events = launcher.audit_callback.get_events_by_type("marketplace_index_loaded")
        assert len(audit_events) == 1
        assert audit_events[0]["total_skills"] >= 2


class TestSkillInstallation:
    """Test skill installation per tenant."""

    def test_install_buildin_skill(self, launcher):
        """Test installing a buildin skill."""
        launcher.load_marketplace_index()

        installation = launcher.install_skill(
            tenant_id="_default",
            skill_id="skill:buildin-memory-cel_session_memory",
            auto_update=True,
        )

        assert installation is not None
        assert installation.tenant_id == "_default"
        assert installation.skill_id == "skill:buildin-memory-cel_session_memory"
        assert installation.installed_version == "1.0.0"
        assert installation.enabled is True

    def test_install_contributor_skill(self, launcher):
        """Test installing a contributor skill."""
        launcher.load_marketplace_index()

        installation = launcher.install_skill(
            tenant_id="_default",
            skill_id="skill:contributor-memory-user_model",
        )

        assert installation is not None
        assert installation.tier == SkillTier.CONTRIBUTOR

    def test_install_nonexistent_skill(self, launcher):
        """Test installing nonexistent skill returns None."""
        launcher.load_marketplace_index()

        installation = launcher.install_skill(
            tenant_id="_default",
            skill_id="skill:nonexistent",
        )

        assert installation is None

    def test_audit_installation(self, launcher):
        """Test audit events for installation."""
        launcher.load_marketplace_index()

        launcher.install_skill(
            tenant_id="_default",
            skill_id="skill:buildin-memory-cel_session_memory",
        )

        # Check for installation events
        started = launcher.audit_callback.get_events_by_type("skill_installation_started")
        completed = launcher.audit_callback.get_events_by_type("skill_installation_completed")

        assert len(started) == 1
        assert len(completed) == 1
        assert started[0]["skill_id"] == "skill:buildin-memory-cel_session_memory"
        assert completed[0]["skill_id"] == "skill:buildin-memory-cel_session_memory"

    def test_list_tenant_skills(self, launcher):
        """Test listing skills installed in a tenant."""
        launcher.load_marketplace_index()

        # Install two skills
        launcher.install_skill(
            tenant_id="_default",
            skill_id="skill:buildin-memory-cel_session_memory",
        )
        launcher.install_skill(
            tenant_id="_default",
            skill_id="skill:contributor-memory-user_model",
        )

        installed = launcher.list_tenant_skills("_default")
        assert len(installed) == 2


class TestTenantIsolation:
    """Test tenant isolation (ADR-0007)."""

    def test_tenant_isolation_different_installs(self, launcher):
        """Test that different tenants have isolated skill installations."""
        launcher.load_marketplace_index()

        # Install same skill in two tenants
        launcher.install_skill(
            tenant_id="tenant-a",
            skill_id="skill:buildin-memory-cel_session_memory",
        )
        launcher.install_skill(
            tenant_id="tenant-b",
            skill_id="skill:buildin-memory-cel_session_memory",
        )

        # Each tenant has their own installation
        a_skills = launcher.list_tenant_skills("tenant-a")
        b_skills = launcher.list_tenant_skills("tenant-b")

        assert len(a_skills) == 1
        assert len(b_skills) == 1

        # But they have different installation IDs
        assert a_skills[0].installation_id != b_skills[0].installation_id

    def test_tenant_isolation_registry_separate(self, launcher):
        """Test that tenant registries are separate."""
        registry_a = launcher.get_tenant_registry("tenant-a")
        registry_b = launcher.get_tenant_registry("tenant-b")

        assert registry_a is not registry_b
        assert registry_a.tenant_id == "tenant-a"
        assert registry_b.tenant_id == "tenant-b"

    def test_tenant_isolation_config_separate(self, launcher):
        """Test that skill configs are tenant-specific."""
        launcher.load_marketplace_index()

        # Install same skill in two tenants
        launcher.install_skill(
            tenant_id="tenant-a",
            skill_id="skill:buildin-memory-cel_session_memory",
        )
        launcher.install_skill(
            tenant_id="tenant-b",
            skill_id="skill:buildin-memory-cel_session_memory",
        )

        # Update config for tenant-a
        registry_a = launcher.get_tenant_registry("tenant-a")
        registry_a.update_skill_config(
            "skill:buildin-memory-cel_session_memory",
            {"setting1": "value1"},
        )

        # Tenant-b should not be affected
        registry_b = launcher.get_tenant_registry("tenant-b")
        skill_b = registry_b.get_installed_skill("skill:buildin-memory-cel_session_memory")

        assert skill_b.custom_config == {}


class TestCommunityFramework:
    """Test community plugin framework."""

    def test_rate_skill(self, launcher):
        """Test rating a skill."""
        success = launcher.rate_skill(
            tenant_id="_default",
            skill_id="skill:buildin-memory-cel_session_memory",
            rating=4.5,
            comment="Great skill!",
        )

        assert success is True

        # Check rating was recorded
        rating = launcher.community_framework.get_skill_rating(
            "skill:buildin-memory-cel_session_memory"
        )
        assert rating == 4.5

    def test_multiple_ratings_average(self, launcher):
        """Test averaging multiple ratings."""
        launcher.rate_skill(
            tenant_id="tenant-a",
            skill_id="skill:buildin-memory-cel_session_memory",
            rating=5.0,
        )
        launcher.rate_skill(
            tenant_id="tenant-b",
            skill_id="skill:buildin-memory-cel_session_memory",
            rating=3.0,
        )

        rating = launcher.community_framework.get_skill_rating(
            "skill:buildin-memory-cel_session_memory"
        )
        assert rating == 4.0  # Average of 5.0 and 3.0

    def test_install_tracking(self, launcher):
        """Test install count tracking."""
        launcher.load_marketplace_index()

        launcher.install_skill(
            tenant_id="tenant-a",
            skill_id="skill:buildin-memory-cel_session_memory",
        )
        launcher.install_skill(
            tenant_id="tenant-b",
            skill_id="skill:buildin-memory-cel_session_memory",
        )

        count = launcher.community_framework.get_install_count(
            "skill:buildin-memory-cel_session_memory"
        )
        assert count == 2

    def test_audit_rating(self, launcher):
        """Test audit event for skill rating."""
        launcher.rate_skill(
            tenant_id="_default",
            skill_id="skill:buildin-memory-cel_session_memory",
            rating=4.5,
        )

        audit_events = launcher.audit_callback.get_events_by_type("skill_rated")
        assert len(audit_events) == 1
        assert audit_events[0]["rating"] == 4.5


class TestEcosystemMonitoring:
    """Test ecosystem health monitoring."""

    def test_record_skill_execution_success(self, monitor):
        """Test recording successful skill execution."""
        monitor.record_skill_execution(
            tenant_id="_default",
            skill_id="skill-1",
            latency_ms=100.0,
            success=True,
        )

        metrics = monitor.get_metrics("_default", "skill-1")
        assert metrics is not None
        assert metrics.total_executions == 1
        assert metrics.successful_executions == 1
        assert metrics.failed_executions == 0
        assert metrics.error_rate == 0.0

    def test_record_skill_execution_failure(self, monitor):
        """Test recording failed skill execution."""
        monitor.record_skill_execution(
            tenant_id="_default",
            skill_id="skill-1",
            latency_ms=200.0,
            success=False,
            error="Timeout",
        )

        metrics = monitor.get_metrics("_default", "skill-1")
        assert metrics.failed_executions == 1
        assert metrics.error_rate == 1.0

    def test_error_rate_computation(self, monitor):
        """Test error rate computation."""
        # Record 90 successes, 10 failures
        for _ in range(90):
            monitor.record_skill_execution(
                tenant_id="_default",
                skill_id="skill-1",
                latency_ms=100.0,
                success=True,
            )

        for _ in range(10):
            monitor.record_skill_execution(
                tenant_id="_default",
                skill_id="skill-1",
                latency_ms=200.0,
                success=False,
            )

        metrics = monitor.get_metrics("_default", "skill-1")
        assert abs(metrics.error_rate - 0.1) < 0.01  # 10%

    def test_health_status_healthy(self, monitor):
        """Test health status computation (healthy)."""
        monitor.record_skill_execution(
            tenant_id="_default",
            skill_id="skill-1",
            latency_ms=100.0,
            success=True,
        )

        metrics = monitor.get_metrics("_default", "skill-1")
        assert metrics.status == HealthStatus.HEALTHY

    def test_health_status_degraded(self, monitor):
        """Test health status computation (degraded)."""
        # High latency
        monitor.record_skill_execution(
            tenant_id="_default",
            skill_id="skill-1",
            latency_ms=6000.0,
            success=True,
        )

        metrics = monitor.get_metrics("_default", "skill-1")
        assert metrics.status == HealthStatus.DEGRADED

    def test_abandoned_skill_detection(self, monitor):
        """Test detection of abandoned skills."""
        monitor.record_skill_execution(
            tenant_id="_default",
            skill_id="skill-1",
            latency_ms=100.0,
            success=True,
        )

        # Manually set last_execution to old date (for testing)
        metrics = monitor.get_metrics("_default", "skill-1")
        old_time = datetime.now(timezone.utc) - timedelta(days=35)
        monitor.metrics["_default"]["skill-1"] = SkillHealthMetrics(
            **{**metrics.__dict__, "last_execution": old_time}
        )

        alerts = monitor.check_abandoned_skills("_default")
        assert len(alerts) == 1
        assert alerts[0].category == "abandoned_skill"

    def test_high_error_rate_alert(self, monitor):
        """Test high error rate alert generation."""
        # Record 100% failure rate
        for _ in range(10):
            monitor.record_skill_execution(
                tenant_id="_default",
                skill_id="skill-1",
                latency_ms=100.0,
                success=False,
            )

        alerts = monitor.get_alerts("_default")
        assert any(a.category == "high_error_rate" for a in alerts)

    def test_trending_skills(self, monitor):
        """Test trending skill computation."""
        # Record activity for skill-1 (more recent)
        for _ in range(50):
            monitor.record_skill_execution(
                tenant_id="_default",
                skill_id="skill-1",
                latency_ms=100.0,
                success=True,
            )
        monitor.record_skill_rating("_default", "skill-1", 5.0)
        monitor.record_feedback("_default", "skill-1", "outcome", 0.8)

        # Record less activity for skill-2
        monitor.record_skill_execution(
            tenant_id="_default",
            skill_id="skill-2",
            latency_ms=100.0,
            success=True,
        )

        trending = monitor.compute_trending_skills("_default")
        assert len(trending) >= 2
        assert trending[0][0] == "skill-1"  # skill-1 is trending

    def test_tenant_isolation_monitoring(self, monitor):
        """Test that monitoring respects tenant isolation."""
        monitor.record_skill_execution(
            tenant_id="tenant-a",
            skill_id="skill-1",
            latency_ms=100.0,
            success=True,
        )
        monitor.record_skill_execution(
            tenant_id="tenant-b",
            skill_id="skill-1",
            latency_ms=200.0,
            success=False,
        )

        # Each tenant has isolated metrics
        metrics_a = monitor.get_metrics("tenant-a", "skill-1")
        metrics_b = monitor.get_metrics("tenant-b", "skill-1")

        assert metrics_a.error_rate == 0.0
        assert metrics_b.error_rate == 1.0


class TestLearningLoopIntegration:
    """Test integration with learning loop (ADR-0314)."""

    def test_record_feedback(self, monitor):
        """Test recording learning feedback."""
        monitor.record_feedback(
            tenant_id="_default",
            skill_id="skill-1",
            feedback_type="outcome",
            signal=0.8,
        )

        metrics = monitor.get_metrics("_default", "skill-1")
        assert metrics.feedback_count == 1
        assert abs(metrics.improvement_trend - 0.8) < 0.01

    def test_improvement_trend_averaging(self, monitor):
        """Test improvement trend computation."""
        monitor.record_feedback(
            tenant_id="_default",
            skill_id="skill-1",
            feedback_type="outcome",
            signal=1.0,
        )
        monitor.record_feedback(
            tenant_id="_default",
            skill_id="skill-1",
            feedback_type="outcome",
            signal=0.6,
        )

        metrics = monitor.get_metrics("_default", "skill-1")
        assert metrics.feedback_count == 2
        assert abs(metrics.improvement_trend - 0.8) < 0.01  # Average of 1.0 and 0.6

    def test_confidence_tracking(self, monitor):
        """Test confidence score tracking from learning."""
        monitor.record_skill_execution(
            tenant_id="_default",
            skill_id="skill-1",
            latency_ms=100.0,
            success=True,
        )

        metrics = monitor.get_metrics("_default", "skill-1")
        # Confidence starts at 1.0 (no feedback)
        assert metrics.confidence_score == 1.0


class TestAuditTrailCompleteness:
    """Test audit trail compliance (ADR-0232/0233)."""

    def test_all_skill_operations_audited(self, launcher):
        """Test that all skill operations are audited."""
        launcher.load_marketplace_index()

        # Perform various operations
        launcher.install_skill("_default", "skill:buildin-memory-cel_session_memory")
        launcher.rate_skill("_default", "skill:buildin-memory-cel_session_memory", 4.5)
        launcher.uninstall_skill("_default", "skill:buildin-memory-cel_session_memory")

        # Check audit trail
        all_events = launcher.audit_callback.events

        event_types = {e["event_type"] for e in all_events}
        assert "marketplace_index_loaded" in event_types
        assert "skill_installation_started" in event_types
        assert "skill_installation_completed" in event_types
        assert "skill_rated" in event_types
        assert "skill_uninstalled" in event_types

    def test_audit_includes_tenant_id(self, launcher):
        """Test that audit events include tenant_id."""
        launcher.load_marketplace_index()
        launcher.install_skill("tenant-x", "skill:buildin-memory-cel_session_memory")

        audit_events = launcher.audit_callback.get_events_by_type("skill_installation_completed")
        assert audit_events[0]["tenant_id"] == "tenant-x"
