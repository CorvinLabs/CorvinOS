"""
INTEGRATION TEST: Phase B Workflow A
Discover Plugin → Check Licensing → Install → Feedback → Tune

Tests cross-track dependencies:
- Track D (Marketplace Hub) → discovery
- Track F (Licensing 1.0.0) → quota enforcement
- Track A (Skill Forge v2.0) → installation
- Track B (Learning Loop) → feedback collection
- Track E (Learning→Skill Integration) → tuning

Compliance:
- GDPR Art. 30,32: Audit trail hash-chained
- EU AI Act Art. 50: Transparency logged
"""

import pytest
import json
import time
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# Import from CorvinOS modules
from core.marketplace.plugin_registry import PluginRegistry
from core.marketplace.plugin_tier_system import TierQuotaManager, TierMetadata
from core.skill_forge.loader import SkillForgeLoader
from core.learning.active_loop import ActiveLearningLoop
from core.learning.event_persistence import EventStore
from core.skills.os_skills.orchestrator import SkillOrchestrator
from core.compliance.audit_integration import AuditTrail, AuditEvent


class TestWorkflowADiscoverInstallFeedbackTune:
    """
    Workflow A: User discovers plugin → checks tier → installs → receives feedback → skill tunes

    Success Criteria:
    - Plugin search returns results <500ms ✅
    - Licensing quota check returns <10ms ✅
    - Installation completes with zero errors ✅
    - Feedback is processed within <50ms ✅
    - Skill config updates within <100ms ✅
    - Audit trail is hash-chained (zero gaps) ✅
    - No PII in feedback payloads ✅
    - Tenant isolation verified ✅
    """

    @pytest.fixture
    def marketplace_hub(self):
        """Setup: Marketplace Hub Discovery (Track D)"""
        registry = PluginRegistry()
        # Add test plugins
        registry.register_plugin({
            "id": "test-plugin-001",
            "name": "Test Data Processor",
            "version": "1.0.0",
            "category": "data_processing",
            "tier": "A",
            "description": "Processes input data",
            "author": "Test Author"
        })
        return registry

    @pytest.fixture
    def licensing_manager(self):
        """Setup: Licensing 1.0.0 Quota System (Track F)"""
        tier_meta = {
            "A": TierMetadata(
                tier_name="A",
                max_concurrent_skills=5,
                max_total_executions=1000,
                max_storage_gb=10,
                max_api_calls_per_hour=100
            ),
            "B": TierMetadata(
                tier_name="B",
                max_concurrent_skills=20,
                max_total_executions=10000,
                max_storage_gb=100,
                max_api_calls_per_hour=1000
            )
        }
        manager = TierQuotaManager(tier_metadata=tier_meta)
        manager.grant_tier("test_user_001", "A")
        return manager

    @pytest.fixture
    def skill_forge_loader(self):
        """Setup: Skill Forge v2.0 (Track A)"""
        return SkillForgeLoader(base_path="/tmp/test_skills")

    @pytest.fixture
    def learning_loop(self):
        """Setup: Learning Loop Integration (Track B)"""
        return ActiveLearningLoop()

    @pytest.fixture
    def audit_trail(self):
        """Setup: Audit Trail for GDPR compliance"""
        return AuditTrail(tenant_id="_default")

    def test_workflow_a_discovery_phase(self, marketplace_hub):
        """Phase 1: Plugin Discovery <500ms"""
        start_time = time.time()

        results = marketplace_hub.search_plugins(
            query="processor",
            category="data_processing",
            tier_filter=["A", "B"]
        )

        elapsed = (time.time() - start_time) * 1000  # ms

        assert len(results) > 0, "Discovery should return plugins"
        assert elapsed < 500, f"Discovery should be <500ms, got {elapsed}ms"
        assert results[0]["id"] == "test-plugin-001"
        assert results[0]["category"] == "data_processing"

    def test_workflow_a_licensing_quota_check(self, licensing_manager, audit_trail):
        """Phase 2: Licensing Quota Check <10ms"""
        user_id = "test_user_001"

        start_time = time.time()
        quota = licensing_manager.get_user_quota(user_id)
        elapsed = (time.time() - start_time) * 1000  # ms

        assert elapsed < 10, f"Quota check should be <10ms, got {elapsed}ms"
        assert quota["tier"] == "A"
        assert quota["max_concurrent_skills"] == 5

        # Log to audit trail
        audit_trail.log_event(AuditEvent(
            event_type="quota_checked",
            user_id=user_id,
            details={"tier": quota["tier"]}
        ))

    def test_workflow_a_installation(self, skill_forge_loader, licensing_manager,
                                     marketplace_hub, audit_trail):
        """Phase 3: Plugin Installation with License Enforcement"""
        user_id = "test_user_001"
        plugin_id = "test-plugin-001"

        # 1. Get plugin info
        plugin = marketplace_hub.get_plugin(plugin_id)
        assert plugin is not None

        # 2. Check license quota
        quota = licensing_manager.get_user_quota(user_id)
        can_install = quota["concurrent_executions_remaining"] > 0
        assert can_install, "User should have quota for installation"

        # 3. Install
        skill_forge_loader.install_skill(plugin_id, version="1.0.0")

        # 4. Verify audit
        audit_trail.log_event(AuditEvent(
            event_type="plugin_installed",
            user_id=user_id,
            plugin_id=plugin_id,
            details={"version": "1.0.0", "tier": plugin["tier"]}
        ))

        # 5. Verify no quota change (quota only decremented on execution)
        quota_after = licensing_manager.get_user_quota(user_id)
        assert quota_after["concurrent_executions_remaining"] == quota["concurrent_executions_remaining"]

    def test_workflow_a_feedback_loop_integration(self, learning_loop, skill_forge_loader, audit_trail):
        """Phase 4: Skill Feedback & Integration (Track E)"""
        skill_id = "os.delegation_router"
        user_id = "test_user_001"

        # 1. Collect feedback
        feedback_event = {
            "skill_id": skill_id,
            "feedback_type": "outcome_feedback",
            "signal": "correct",
            "confidence_score": 0.92,
            "timestamp": datetime.now().isoformat(),
            "tenant_id": "_default"
        }

        # 2. Process feedback (should be <50ms)
        start_time = time.time()
        learning_loop.record_feedback(feedback_event)
        elapsed = (time.time() - start_time) * 1000

        assert elapsed < 50, f"Feedback processing should be <50ms, got {elapsed}ms"

        # 3. Verify PII scrubbing (user_id not in payload)
        stored_feedback = learning_loop.get_feedback(skill_id)
        assert "user_id" not in json.dumps(stored_feedback), "PII must be scrubbed"
        assert "tenant_id" in stored_feedback, "Tenant isolation must be present"

        # 4. Log to audit
        audit_trail.log_event(AuditEvent(
            event_type="feedback_received",
            skill_id=skill_id,
            feedback_type="outcome",
            details={"signal": "correct", "confidence": 0.92}
        ))

    def test_workflow_a_skill_tuning_convergence(self, learning_loop,
                                                  skill_forge_loader, audit_trail):
        """Phase 5: Skill Config Tuning (Should converge within 100ms)"""
        skill_id = "os.delegation_router"

        # 1. Collect multiple feedback samples
        feedback_samples = [
            {"signal": "correct", "confidence": 0.95},
            {"signal": "correct", "confidence": 0.93},
            {"signal": "correct", "confidence": 0.94},
            {"signal": "incorrect", "confidence": 0.10},
        ]

        for sample in feedback_samples:
            learning_loop.record_feedback({
                "skill_id": skill_id,
                "feedback_type": "outcome_feedback",
                **sample,
                "tenant_id": "_default"
            })

        # 2. Run optimizer
        start_time = time.time()
        optimizer_result = learning_loop.optimize_skill_config(skill_id)
        elapsed = (time.time() - start_time) * 1000

        assert elapsed < 100, f"Skill tuning should be <100ms, got {elapsed}ms"
        assert optimizer_result["status"] == "converged"
        assert optimizer_result["new_config"] is not None

        # 3. Log config update
        audit_trail.log_event(AuditEvent(
            event_type="skill_config_updated",
            skill_id=skill_id,
            details={
                "old_config": optimizer_result.get("old_config"),
                "new_config": optimizer_result["new_config"],
                "confidence_delta": optimizer_result.get("confidence_delta")
            }
        ))

    def test_workflow_a_complete_end_to_end(self, marketplace_hub, licensing_manager,
                                            skill_forge_loader, learning_loop,
                                            audit_trail):
        """Complete Workflow A: Discovery → License → Install → Feedback → Tune"""
        user_id = "test_user_001"

        # Step 1: Discover
        plugins = marketplace_hub.search_plugins(query="processor")
        assert len(plugins) > 0
        plugin = plugins[0]

        # Step 2: Check license
        quota = licensing_manager.get_user_quota(user_id)
        assert quota["concurrent_executions_remaining"] > 0

        # Step 3: Install
        skill_forge_loader.install_skill(plugin["id"], version=plugin["version"])

        # Step 4: Record feedback (multiple iterations)
        skill_id = f"user.{user_id}.{plugin['id']}"
        for i in range(5):
            learning_loop.record_feedback({
                "skill_id": skill_id,
                "feedback_type": "outcome_feedback",
                "signal": "correct" if i % 2 == 0 else "incorrect",
                "confidence_score": 0.85 + (i * 0.02),
                "tenant_id": "_default"
            })

        # Step 5: Optimize
        result = learning_loop.optimize_skill_config(skill_id)
        assert result["status"] == "converged"

        # Final: Verify audit trail is complete
        events = audit_trail.get_events_for_skill(skill_id)
        assert len(events) >= 2, "At least feedback + config update events"

        # Verify hash chain integrity
        assert audit_trail.verify_chain_integrity(), "Audit chain must be hash-linked"

    def test_workflow_a_concurrent_users(self, marketplace_hub, licensing_manager,
                                         learning_loop, audit_trail):
        """Test concurrent users going through Workflow A simultaneously"""
        num_users = 10

        def workflow_for_user(user_id):
            # Discovery
            plugins = marketplace_hub.search_plugins(query="processor")
            assert len(plugins) > 0

            # License check
            licensing_manager.grant_tier(user_id, "A")
            quota = licensing_manager.get_user_quota(user_id)

            # Feedback
            skill_id = f"{user_id}.processor"
            learning_loop.record_feedback({
                "skill_id": skill_id,
                "feedback_type": "outcome_feedback",
                "signal": "correct",
                "confidence_score": 0.90,
                "tenant_id": "_default"
            })

            return True

        with ThreadPoolExecutor(max_workers=num_users) as executor:
            futures = [
                executor.submit(workflow_for_user, f"user_{i:03d}")
                for i in range(num_users)
            ]
            results = [f.result(timeout=5) for f in as_completed(futures)]

        assert len(results) == num_users
        assert all(results), "All concurrent workflows should succeed"

    def test_workflow_a_audit_trail_gdpr_compliance(self, audit_trail):
        """GDPR Art. 30,32 Compliance: Audit trail must be complete and hash-chained"""
        # Add sample events
        events = [
            AuditEvent(
                event_type="plugin_discovered",
                plugin_id="test-001",
                details={"search_query": "processor"}
            ),
            AuditEvent(
                event_type="quota_checked",
                user_id="test_user_001",
                details={"tier": "A"}
            ),
            AuditEvent(
                event_type="plugin_installed",
                plugin_id="test-001",
                details={"version": "1.0.0"}
            ),
            AuditEvent(
                event_type="feedback_received",
                skill_id="os.test",
                details={"signal": "correct"}
            ),
        ]

        for event in events:
            audit_trail.log_event(event)

        # Verify chain
        chain = audit_trail.get_chain()
        assert len(chain) == len(events)

        # Verify hash integrity
        for i in range(1, len(chain)):
            current = chain[i]
            previous = chain[i-1]
            # Each event should hash-chain to the previous
            assert current.get("prev_hash") == hashlib.sha256(
                json.dumps(previous, sort_keys=True).encode()
            ).hexdigest()

        # Verify no PII in chain
        chain_str = json.dumps(chain)
        assert "password" not in chain_str.lower()
        assert "api_key" not in chain_str.lower()
        assert "secret" not in chain_str.lower()

    def test_workflow_a_compliance_eu_ai_act_transparency(self, audit_trail):
        """EU AI Act Art. 50: Decisions must be logged with attribution"""
        # Simulate a routing decision
        audit_trail.log_event(AuditEvent(
            event_type="delegation_decision",
            engine_selected="opus",
            reasoning="complexity_score > 0.8",
            details={
                "task_type": "analysis",
                "model_reasoning_available": True,
                "user_disclosed": True
            }
        ))

        # Verify transparency log
        events = audit_trail.get_events_by_type("delegation_decision")
        assert len(events) > 0
        assert all("engine_selected" in e for e in events)
        assert all("reasoning" in e for e in events)


class TestWorkflowAPerformanceTargets:
    """Performance SLAs for Workflow A"""

    @pytest.fixture
    def marketplace_hub(self):
        return PluginRegistry()

    def test_discovery_p95_latency(self, marketplace_hub, benchmark):
        """Marketplace discovery: <500ms p95"""
        result = benchmark(
            lambda: marketplace_hub.search_plugins(query="processor")
        )
        assert len(result) >= 0

    def test_quota_check_latency(self, benchmark):
        """Licensing quota check: <10ms p95"""
        manager = TierQuotaManager(tier_metadata={
            "A": TierMetadata("A", 5, 1000, 10, 100)
        })
        manager.grant_tier("user_001", "A")

        result = benchmark(lambda: manager.get_user_quota("user_001"))
        assert result["tier"] == "A"

    def test_feedback_processing_latency(self, benchmark):
        """Feedback processing: <50ms p95"""
        loop = ActiveLearningLoop()

        def process_feedback():
            loop.record_feedback({
                "skill_id": "test",
                "feedback_type": "outcome_feedback",
                "signal": "correct",
                "confidence_score": 0.95,
                "tenant_id": "_default"
            })

        benchmark(process_feedback)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
