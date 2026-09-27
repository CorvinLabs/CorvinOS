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


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Every test writes to a scratch CORVIN_HOME — never ~/.corvin."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "home" / ".config"))
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.delenv("CORVIN_TENANT_ID", raising=False)
    yield


def chain_events(event_type: str, tenant: str = "_default") -> list[dict]:
    """``details`` of every record of ``event_type`` on the REAL tenant chain."""
    from forge import paths as fp

    chain = fp.tenant_audit_chain(tenant)
    if not chain.exists():
        return []
    recs = [json.loads(line) for line in chain.read_text().splitlines() if line.strip()]
    return [r["details"] for r in recs if r["event_type"] == event_type]


def as_tenant(monkeypatch, tenant: str) -> None:
    """Switch the process tenant — the audit writer refuses a record for any
    other tenant (ADR-0007), so a per-tenant action runs as that tenant."""
    monkeypatch.setenv("CORVIN_TENANT_ID", tenant)


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
    return SkillEcosystemLauncher(marketplace_root=temp_marketplace)


@pytest.fixture
def monitor(tmp_path):
    """Create EcosystemMonitor."""
    return EcosystemMonitor(data_dir=tmp_path / "ecosystem-metrics")


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

        audit_events = chain_events("marketplace.index_loaded")
        assert len(audit_events) == 1
        assert audit_events[0]["total_skills"] == 2
        assert audit_events[0]["buildin_count"] == 1
        assert audit_events[0]["contributor_count"] == 1

    def test_invalid_manifest_is_skipped_and_audited(self, launcher, temp_marketplace):
        bad = temp_marketplace / "plugins" / "contributor" / "memory" / "broken"
        bad.mkdir(parents=True)
        (bad / "skill.json").write_text("{not json")

        index = launcher.load_marketplace_index()
        assert index.total_skills == 2
        invalid = chain_events("marketplace.manifest_invalid")
        assert invalid == [{**invalid[0], "tier": "contributor", "reason": "JSONDecodeError"}]
        assert str(bad) not in json.dumps(invalid)
        assert chain_events("marketplace.index_loaded")[0]["invalid_count"] == 1

    def test_directory_decides_tier(self, launcher, temp_marketplace):
        """A contributor manifest claiming ``buildin`` is indexed as contributor."""
        d = temp_marketplace / "plugins" / "contributor" / "memory" / "liar"
        d.mkdir(parents=True)
        (d / "skill.json").write_text(json.dumps({
            "id": "skill:liar", "name": "Liar", "version": "1.0.0", "author": "x",
            "license": "MIT", "tier": "buildin", "category": "memory", "description": "d",
        }))
        launcher.load_marketplace_index()
        assert launcher.get_skill_details("skill:liar").tier == SkillTier.CONTRIBUTOR


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
        started = chain_events("marketplace.skill_installation_started")
        completed = chain_events("marketplace.skill_installation_completed")

        assert len(started) == 1
        assert len(completed) == 1
        assert started[0]["skill_id"] == "skill:buildin-memory-cel_session_memory"
        assert completed[0]["skill_id"] == "skill:buildin-memory-cel_session_memory"

    def test_audit_failure_blocks_installation(self, launcher, monkeypatch):
        """No chain commit → no installation (audit-first, fail-closed)."""
        from core.deployment.audit_sink import AuditWriteFailed

        launcher.load_marketplace_index()

        def boom(*a, **k):
            raise AuditWriteFailed("disk full")

        monkeypatch.setattr("core.marketplace.skill_ecosystem_launcher.audit_sink.emit", boom)
        with pytest.raises(AuditWriteFailed):
            launcher.install_skill("_default", "skill:buildin-memory-cel_session_memory")
        assert launcher.list_tenant_skills("_default") == []
        assert launcher.community_framework.get_install_count(
            "skill:buildin-memory-cel_session_memory") == 0

    def test_install_for_foreign_tenant_is_refused(self, launcher):
        """A _default process cannot write tenant-z's audit → install refused."""
        from core.deployment.audit_sink import AuditWriteFailed

        launcher.load_marketplace_index()
        with pytest.raises(AuditWriteFailed):
            launcher.install_skill("tenant-z", "skill:buildin-memory-cel_session_memory")
        assert launcher.list_tenant_skills("tenant-z") == []

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

    def test_tenant_isolation_different_installs(self, launcher, monkeypatch):
        """Test that different tenants have isolated skill installations."""
        launcher.load_marketplace_index()

        # Install same skill in two tenants
        as_tenant(monkeypatch, "tenant-a")
        launcher.install_skill(
            tenant_id="tenant-a",
            skill_id="skill:buildin-memory-cel_session_memory",
        )
        as_tenant(monkeypatch, "tenant-b")
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
        # ...and each tenant's record sits on its own chain only
        assert len(chain_events("marketplace.skill_installation_completed", "tenant-a")) == 1
        assert len(chain_events("marketplace.skill_installation_completed", "tenant-b")) == 1

    def test_tenant_isolation_registry_separate(self, launcher):
        """Test that tenant registries are separate."""
        registry_a = launcher.get_tenant_registry("tenant-a")
        registry_b = launcher.get_tenant_registry("tenant-b")

        assert registry_a is not registry_b
        assert registry_a.tenant_id == "tenant-a"
        assert registry_b.tenant_id == "tenant-b"

    def test_tenant_isolation_config_separate(self, launcher, monkeypatch):
        """Test that skill configs are tenant-specific."""
        launcher.load_marketplace_index()

        # Install same skill in two tenants
        as_tenant(monkeypatch, "tenant-a")
        launcher.install_skill(
            tenant_id="tenant-a",
            skill_id="skill:buildin-memory-cel_session_memory",
        )
        as_tenant(monkeypatch, "tenant-b")
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

    def test_multiple_ratings_average(self, launcher, monkeypatch):
        """Test averaging multiple ratings."""
        as_tenant(monkeypatch, "tenant-a")
        launcher.rate_skill(
            tenant_id="tenant-a",
            skill_id="skill:buildin-memory-cel_session_memory",
            rating=5.0,
        )
        as_tenant(monkeypatch, "tenant-b")
        launcher.rate_skill(
            tenant_id="tenant-b",
            skill_id="skill:buildin-memory-cel_session_memory",
            rating=3.0,
        )

        rating = launcher.community_framework.get_skill_rating(
            "skill:buildin-memory-cel_session_memory"
        )
        assert rating == 4.0  # Average of 5.0 and 3.0

    def test_rating_mean_over_three(self, launcher):
        """Arithmetic mean, not ``(old + new) / 2`` (which gave 2.5 here)."""
        for r in (5.0, 3.0, 1.0):
            assert launcher.rate_skill("_default", "skill:x", r) is True
        assert launcher.community_framework.get_skill_rating("skill:x") == pytest.approx(3.0)

    def test_out_of_range_rating_rejected_and_not_audited(self, launcher):
        assert launcher.rate_skill("_default", "skill:x", 9.0) is False
        assert chain_events("marketplace.skill_rated") == []

    def test_install_tracking(self, launcher, monkeypatch):
        """Test install count tracking."""
        launcher.load_marketplace_index()

        as_tenant(monkeypatch, "tenant-a")
        launcher.install_skill(
            tenant_id="tenant-a",
            skill_id="skill:buildin-memory-cel_session_memory",
        )
        as_tenant(monkeypatch, "tenant-b")
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

        audit_events = chain_events("marketplace.skill_rated")
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
        # exactly ONE open alert for the condition, not one per failing run
        assert [a.category for a in alerts] == ["high_error_rate"]
        raised = chain_events("marketplace.ecosystem_alert_raised")
        assert len(raised) == 1
        assert raised[0]["category"] == "high_error_rate"
        assert raised[0]["alert_id"] == alerts[0].alert_id

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

    def test_tenant_isolation_monitoring(self, monitor, monkeypatch):
        """Test that monitoring respects tenant isolation."""
        as_tenant(monkeypatch, "tenant-a")
        monitor.record_skill_execution(
            tenant_id="tenant-a",
            skill_id="skill-1",
            latency_ms=100.0,
            success=True,
        )
        as_tenant(monkeypatch, "tenant-b")
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
        # Nothing measured a confidence → None, never a fabricated 1.0
        assert metrics.confidence_score is None

    def test_unmeasured_skill_is_not_healthy(self, monitor):
        monitor.record_skill_rating("_default", "skill-1", 4.0)
        assert monitor.get_metrics("_default", "skill-1").status == HealthStatus.NOT_MEASURED

    def test_execution_keeps_ratings_and_feedback(self, monitor):
        """An execution used to rebuild the record and reset rating/feedback."""
        monitor.record_skill_rating("_default", "skill-1", 4.0)
        monitor.record_feedback("_default", "skill-1", "outcome", 0.5)
        monitor.record_skill_execution("_default", "skill-1", latency_ms=10.0, success=True)
        m = monitor.get_metrics("_default", "skill-1")
        assert m.rating_count == 1 and m.average_rating == 4.0
        assert m.feedback_count == 1 and m.improvement_trend == 0.5

    def test_p99_is_a_real_percentile(self, monitor):
        """p99 used to be max(prev, latency*0.95) and could never go down."""
        for ms in [10.0] * 99 + [1000.0]:
            monitor.record_skill_execution("_default", "skill-1", latency_ms=ms, success=True)
        m = monitor.get_metrics("_default", "skill-1")
        assert m.p99_latency_ms == 10.0
        assert m.p95_latency_ms == 10.0
        assert m.max_latency_ms == 1000.0

    def test_snapshot_roundtrip_restores_metrics(self, monitor, tmp_path):
        monitor.record_skill_execution("_default", "skill-1", latency_ms=20.0, success=True)
        monitor.record_skill_rating("_default", "skill-1", 5.0)
        path = monitor.save_metrics_snapshot("_default")

        fresh = EcosystemMonitor(data_dir=tmp_path / "other")
        assert fresh.load_metrics_snapshot(path) is True
        m = fresh.get_metrics("_default", "skill-1")
        assert m.total_executions == 1 and m.average_rating == 5.0
        assert m.status == HealthStatus.HEALTHY

    def test_corrupt_snapshot_load_fails(self, monitor, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"tenant_id": "_default", "metrics": {"s": {"skill_id": "s"}}}))
        assert monitor.load_metrics_snapshot(bad) is False
        assert monitor.get_metrics("_default", "s") is None

    def test_default_data_dir_is_tenant_scoped(self, tmp_path):
        mon = EcosystemMonitor()
        mon.record_skill_execution("_default", "skill-1", latency_ms=1.0, success=True)
        path = mon.save_metrics_snapshot("_default")
        assert str(path).startswith(str(tmp_path / "corvin" / "tenants" / "_default" / "global"))


class TestAuditTrailCompleteness:
    """Test audit trail compliance (ADR-0232/0233)."""

    def test_all_skill_operations_audited(self, launcher):
        """Test that all skill operations are audited."""
        launcher.load_marketplace_index()

        # Perform various operations
        launcher.install_skill("_default", "skill:buildin-memory-cel_session_memory")
        launcher.rate_skill("_default", "skill:buildin-memory-cel_session_memory", 4.5)
        launcher.uninstall_skill("_default", "skill:buildin-memory-cel_session_memory")

        # Check the REAL chain — and that it still verifies
        from forge import paths as fp
        from forge import security_events as se

        for et in ("marketplace.index_loaded", "marketplace.skill_installation_started",
                   "marketplace.skill_installation_completed", "marketplace.skill_rated",
                   "marketplace.skill_uninstalled"):
            assert len(chain_events(et)) == 1, et
        ok, issues = se.verify_chain(fp.tenant_audit_chain("_default"))
        assert ok, issues

    def test_audit_includes_tenant_id(self, launcher, monkeypatch):
        """Test that audit events include tenant_id and land on that tenant's chain."""
        launcher.load_marketplace_index()
        as_tenant(monkeypatch, "tenant-x")
        launcher.install_skill("tenant-x", "skill:buildin-memory-cel_session_memory")

        audit_events = chain_events("marketplace.skill_installation_completed", "tenant-x")
        assert audit_events[0]["tenant_id"] == "tenant-x"
        assert chain_events("marketplace.skill_installation_completed", "_default") == []
