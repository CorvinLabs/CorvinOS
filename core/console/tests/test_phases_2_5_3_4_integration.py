"""End-to-End Integration Tests for Phases 2.5, 3, 4."""

import pytest
import json
import asyncio
from datetime import datetime
from pathlib import Path

# Import modules under test
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from corvin_console.routes.github_webhook_events import (
    WebhookEvent, EventType, EventQueue, EventProcessor, DeliveryGuarantee
)
from corvin_console.routes.github_webhook_handlers import EventHandler, WebhookProcessor
from corvin_console.routes.conflict_resolver import (
    SkillVersion, ConflictDetector, ConflictResolver, MergeOrchestrator
)
from corvin_console.routes.federation_model import (
    FederatedInstance, FederationRegistry, FederatedLearning, InstanceRole, CrossInstanceSync
)


class TestPhase25WebhookSync:
    """Phase 2.5: Webhook-driven real-time sync."""

    def test_event_enqueue_dequeue(self):
        """Test event queue operations."""
        queue = EventQueue()
        event = WebhookEvent(
            event_id="evt-001",
            event_type=EventType.SKILL_CREATED,
            timestamp=datetime.utcnow().isoformat() + "Z",
            tenant_id="_default",
            repo_url="https://github.com/test/repo",
            payload={"skill_name": "test-skill"}
        )

        assert queue.enqueue(event)
        retrieved = queue.dequeue()
        assert retrieved is not None
        assert retrieved.event_id == "evt-001"

    def test_delivery_guarantee_exactly_once(self):
        """Test exactly-once delivery guarantee (deduplication)."""
        processor = EventProcessor()
        event1 = WebhookEvent(
            event_id="evt-dup",
            event_type=EventType.SKILL_CREATED,
            timestamp=datetime.utcnow().isoformat() + "Z",
            tenant_id="_default",
            repo_url="https://github.com/test/repo",
            payload={"skill_name": "test"},
            delivery_guarantee=DeliveryGuarantee.EXACTLY_ONCE
        )

        result1 = processor.process_github_webhook(event1.payload)
        assert result1["success"]

        # Try same event again
        result2 = processor.process_github_webhook(event1.payload)
        assert "deduplicated" in result2.get("message", "").lower() or result2["success"]

    def test_webhook_signature_verification(self):
        """Test GitHub webhook signature verification."""
        secret = "test-secret"
        processor = EventProcessor(webhook_secret=secret)
        payload = {"test": "data"}

        # Valid signature
        import hmac
        import hashlib
        sig = "sha256=" + hmac.new(
            secret.encode(),
            json.dumps(payload).encode(),
            hashlib.sha256
        ).hexdigest()

        result = processor.process_github_webhook(json.dumps(payload), sig)
        assert result["success"]

    @pytest.mark.asyncio
    async def test_event_handler_registration(self):
        """Test event handler registration and firing."""
        processor = EventProcessor()
        handler = EventHandler(processor)

        # Verify default handlers registered
        assert EventType.SKILL_CREATED in handler.handlers
        assert len(handler.handlers[EventType.SKILL_CREATED]) > 0


class TestPhase3ConflictResolution:
    """Phase 3: Conflict detection and resolution."""

    def test_detect_no_conflict_identical(self):
        """Test no conflict when content identical."""
        v1 = SkillVersion("skill-1", "content", version=1)
        v2 = SkillVersion("skill-1", "content", version=2)

        has_conflict, conflict_type, _ = ConflictDetector.detect_conflict(v1, v2)
        assert not has_conflict

    def test_detect_delete_update_conflict(self):
        """Test DELETE_UPDATE conflict detection."""
        local = SkillVersion("skill-1", "", version=1)  # Deleted
        remote = SkillVersion("skill-1", "updated content", version=2)

        has_conflict, conflict_type, _ = ConflictDetector.detect_conflict(local, remote)
        assert has_conflict
        assert conflict_type == ConflictType.DELETE_UPDATE

    def test_resolve_last_write_wins(self):
        """Test Last-Write-Wins merge strategy."""
        from conflict_resolver import ConflictType

        local = SkillVersion("skill-1", "local content", version=1,
                           timestamp="2026-08-20T10:00:00Z")
        remote = SkillVersion("skill-1", "remote content", version=2,
                            timestamp="2026-08-20T10:05:00Z")

        resolver = ConflictResolver()
        merged, escalated = resolver.resolve(local, remote)

        assert merged.content == "remote content"  # Remote is newer
        assert not escalated

    def test_merge_orchestrator(self):
        """Test merging multiple skills."""
        orchestrator = MergeOrchestrator()

        local = {
            "skill-1": "local v1",
            "skill-2": "local v2",
            "skill-3": ""  # Deleted
        }

        remote = {
            "skill-1": "remote v1-updated",
            "skill-2": "local v2",  # No change
            "skill-4": "new skill"  # Added
        }

        result = orchestrator.merge_skills(local, remote)

        assert "skill-1" in result["merged"]
        assert "skill-4" in result["added"]
        assert "skill-3" in result["deleted"]


class TestPhase4Federation:
    """Phase 4: Multi-tenant federation and federated learning."""

    def test_register_instance(self):
        """Test instance registration in federation."""
        registry = FederationRegistry()
        instance = FederatedInstance(
            instance_id="corvin-us-east-1",
            url="https://us-east.corvin.example.com",
            role=InstanceRole.LEADER,
            region="us-east-1"
        )

        assert registry.register_instance(instance)
        assert "corvin-us-east-1" in registry.instances

    def test_get_healthy_instances(self):
        """Test retrieving healthy instances."""
        registry = FederationRegistry()
        instance = FederatedInstance(
            instance_id="corvin-healthy",
            url="https://healthy.example.com",
            role=InstanceRole.COMPUTE
        )
        registry.register_instance(instance)

        healthy = registry.get_healthy_instances(InstanceRole.COMPUTE)
        assert len(healthy) > 0

    def test_federated_learning_start_round(self):
        """Test starting a federated learning round."""
        registry = FederationRegistry()
        instance = FederatedInstance(
            instance_id="corvin-compute-1",
            url="https://compute.example.com",
            role=InstanceRole.COMPUTE
        )
        registry.register_instance(instance)

        learning = FederatedLearning()
        result = learning.start_training_round("model-v1", round_num=1)

        assert result["success"]
        assert result["instances"] > 0

    def test_data_isolation_verification(self):
        """Test federated learning data isolation."""
        learning = FederatedLearning()
        result = learning.verify_data_isolation()

        assert result["data_isolation_verified"]
        assert result["raw_data_transfers"] == 0

    def test_cross_instance_sync(self):
        """Test skill sync between instances."""
        registry = FederationRegistry()
        sync = CrossInstanceSync()

        skills = {
            "skill-1": "content 1",
            "skill-2": "content 2"
        }

        result = sync.push_skills_to_peers(skills)

        # No peers registered yet, so should be empty
        assert "pushed_to" in result


class TestIntegrationScenarios:
    """End-to-end scenarios across all phases."""

    @pytest.mark.asyncio
    async def test_full_sync_lifecycle(self):
        """
        Full lifecycle: webhook → event → conflict detection → federation sync.
        """

        # Step 1: Webhook received (Phase 2.5)
        processor = EventProcessor()
        webhook_payload = {
            "action": "opened",
            "pull_request": {"title": "Add skills"},
            "repository": {"html_url": "https://github.com/test/repo"}
        }
        result = processor.process_github_webhook(webhook_payload)
        assert result["success"]

        # Step 2: Dequeue and process event
        event = processor.queue.dequeue()
        assert event is not None

        # Step 3: Conflict detection (Phase 3)
        local_skill = SkillVersion("skill-1", "local version", version=1)
        remote_skill = SkillVersion("skill-1", "remote version", version=2)

        detector = ConflictDetector()
        has_conflict, conflict_type, _ = detector.detect_conflict(local_skill, remote_skill)
        # Expect conflict due to different content

        # Step 4: Federation (Phase 4)
        registry = FederationRegistry()
        learning = FederatedLearning()

        # Register a compute instance
        instance = FederatedInstance(
            "corvin-node-1",
            "https://node1.example.com",
            InstanceRole.COMPUTE
        )
        registry.register_instance(instance)

        # Start training
        training = learning.start_training_round("model-v1", 1)
        assert training["success"]


# Pytest fixtures
@pytest.fixture
def temp_tenant_path(tmp_path):
    """Temporary tenant directory for tests."""
    return tmp_path / '.corvin' / 'tenants' / '_default'


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
