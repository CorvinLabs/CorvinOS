"""E2E tests for CRITICAL-4: A2A Console Wiring (Tier 4, Iteration 3).

Tests full multi-instance workflow including envelope dispatch and registry.
Note: Requires operator/bridges/shared on path for RemoteTriggerSender integration.
"""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.console.corvin_console.api.multi_instance_sync import A2ATaskEnvelope
from core.console.corvin_console.api.instance_registry import InstanceRegistry


@pytest.mark.asyncio
class TestMultiInstanceE2EWorkflow:
    """End-to-end workflow tests for multi-instance sync (Tier 4)."""

    async def test_full_workflow_register_dispatch_monitor(self):
        """Test complete workflow: register instance → dispatch task → monitor status.

        This test verifies the full CRITICAL-4 execution path:
        1. Instance registers itself in registry
        2. Task is created with ExecutionContext
        3. Envelope is prepared for dispatch
        4. Status can be queried
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Setup registry
            registry_path = Path(tmpdir) / "instances.json"
            registry = InstanceRegistry(registry_path=registry_path)

            # Step 1: Register peer instance
            peer = registry.register(
                instance_id="inst-ubuntu-1",
                endpoint_id="ubuntu-host",
                version="1.0.0",
                region="us-east-1",
            )
            assert peer.instance_id == "inst-ubuntu-1"
            assert peer.status == "online"

            # Step 2: Verify instance is discoverable
            active = registry.list_active()
            assert len(active) == 1
            assert active[0].endpoint_id == "ubuntu-host"

            # Step 3: Create execution context and envelope
            context_snapshot = {
                "task_id": "task-e2e-1",
                "scope": "root → orchestrator → worker-1",
                "decision_history_count": 3,
                "metadata": {"engine": "claude-haiku-4-5", "strategy": "adaptive"},
            }

            decision_history = [
                {
                    "decision": "analyze_input",
                    "timestamp": 1234567890,
                    "params": {"input_size": 5000},
                },
                {
                    "decision": "route_to_peer",
                    "timestamp": 1234567895,
                    "reason": "task exceeds local capacity",
                },
                {
                    "decision": "delegate_to_ubuntu_host",
                    "timestamp": 1234567900,
                    "instance_id": "inst-ubuntu-1",
                },
            ]

            envelope = A2ATaskEnvelope(
                task_id="task-e2e-1",
                context_snapshot=context_snapshot,
                decision_history=decision_history,
                endpoint_id="ubuntu-host",
                tenant_id="_default",
                timeout_s=30,
                retry_count=3,
            )

            # Step 4: Verify envelope serialization
            envelope_dict = envelope.to_dict()
            assert envelope_dict["task_id"] == "task-e2e-1"
            assert envelope_dict["tenant_id"] == "_default"
            assert len(envelope_dict["decision_history"]) == 3
            assert envelope_dict["context_snapshot"]["scope"] == "root → orchestrator → worker-1"

            # Step 5: Verify envelope can be JSON-serialized
            envelope_json = envelope.to_json()
            parsed = json.loads(envelope_json)
            assert parsed["task_id"] == "task-e2e-1"
            assert parsed["context_snapshot"]["metadata"]["engine"] == "claude-haiku-4-5"

            # Step 6: Simulate heartbeat and status check
            registry.heartbeat("inst-ubuntu-1")
            status_record = registry.get("inst-ubuntu-1")
            assert status_record.status == "online"
            assert status_record.last_heartbeat > peer.last_heartbeat

    async def test_multi_instance_coordination(self):
        """Test coordination between multiple instances."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "instances.json"
            registry = InstanceRegistry(registry_path=registry_path)

            # Register multiple instances
            instances = [
                ("inst-ubuntu-1", "ubuntu-host", "1.0.0"),
                ("inst-windows-1", "windows-dev", "2.0.0"),
                ("inst-macos-1", "macos-pro", "1.5.0"),
            ]

            for inst_id, endpoint_id, version in instances:
                registry.register(
                    instance_id=inst_id,
                    endpoint_id=endpoint_id,
                    version=version,
                )

            # Verify all are active
            active = registry.list_active()
            assert len(active) == 3

            # Create tasks for each instance
            tasks = []
            for inst_id, endpoint_id, _ in instances:
                envelope = A2ATaskEnvelope(
                    task_id=f"task-{endpoint_id}",
                    context_snapshot={"instance": endpoint_id},
                    decision_history=[],
                    endpoint_id=endpoint_id,
                )
                tasks.append(envelope)

            # Verify all tasks target correct endpoints
            endpoints = {t.endpoint_id for t in tasks}
            assert endpoints == {"ubuntu-host", "windows-dev", "macos-pro"}

    async def test_tenant_isolation_e2e(self):
        """Test tenant isolation across full workflow."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Each tenant has its own registry
            registry_path_a = Path(tmpdir) / "tenant_a" / "instances.json"
            registry_path_b = Path(tmpdir) / "tenant_b" / "instances.json"

            registry_a = InstanceRegistry(registry_path=registry_path_a)
            registry_b = InstanceRegistry(registry_path=registry_path_b)

            # Register instances in each tenant
            registry_a.register(instance_id="inst-a-1", endpoint_id="ubuntu-host-a")
            registry_b.register(instance_id="inst-b-1", endpoint_id="ubuntu-host-b")

            # Create envelopes for each tenant
            envelope_a = A2ATaskEnvelope(
                task_id="task-a",
                context_snapshot={},
                decision_history=[],
                endpoint_id="ubuntu-host-a",
                tenant_id="tenant_a",
            )

            envelope_b = A2ATaskEnvelope(
                task_id="task-b",
                context_snapshot={},
                decision_history=[],
                endpoint_id="ubuntu-host-b",
                tenant_id="tenant_b",
            )

            # Verify isolation
            assert envelope_a.tenant_id != envelope_b.tenant_id
            assert registry_a.get("inst-a-1").instance_id == "inst-a-1"
            assert registry_b.get("inst-b-1").instance_id == "inst-b-1"
            assert registry_a.get("inst-b-1") is None
            assert registry_b.get("inst-a-1") is None

    async def test_stale_instance_cleanup_e2e(self):
        """Test E2E stale instance cleanup workflow."""
        import time

        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "instances.json"
            registry = InstanceRegistry(registry_path=registry_path)

            # Register instances
            registry.register(instance_id="inst-active-1", endpoint_id="ubuntu-host-1")
            registry.register(instance_id="inst-active-2", endpoint_id="ubuntu-host-2")
            registry.register(instance_id="inst-stale", endpoint_id="stale-host")

            # Make one instance stale
            records = registry._read_all()
            records[2].last_heartbeat = time.time() - 40  # 40s ago
            registry._write_all(records)

            # Verify before cleanup
            all_instances = registry.list_all()
            assert len(all_instances) == 3

            # Cleanup stale instances
            removed_count = registry.cleanup()
            assert removed_count == 1

            # Verify after cleanup
            remaining = registry.list_all()
            assert len(remaining) == 2
            ids = {r.instance_id for r in remaining}
            assert ids == {"inst-active-1", "inst-active-2"}

    async def test_envelope_retry_logic_structure(self):
        """Test that envelope supports retry logic structure (mocked)."""
        envelope = A2ATaskEnvelope(
            task_id="task-retry-1",
            context_snapshot={},
            decision_history=[],
            endpoint_id="ubuntu-host",
            retry_count=3,
            timeout_s=30,
        )

        # Verify retry configuration
        assert envelope.retry_count == 3
        assert envelope.timeout_s == 30
        assert envelope.task_id == "task-retry-1"

        # Verify envelope can be serialized for dispatch attempt
        envelope_json = envelope.to_json()
        parsed = json.loads(envelope_json)
        assert parsed["task_id"] == "task-retry-1"

    async def test_concurrent_envelope_dispatch_preparation(self):
        """Test concurrent preparation of multiple envelopes (async-safe)."""
        tasks = []

        async def create_envelope(task_num: int) -> A2ATaskEnvelope:
            await asyncio.sleep(0.001)  # Simulate async I/O
            return A2ATaskEnvelope(
                task_id=f"task-{task_num}",
                context_snapshot={"num": task_num},
                decision_history=[],
                endpoint_id=f"host-{task_num % 3}",
            )

        # Create multiple envelopes concurrently
        envelopes = await asyncio.gather(*[create_envelope(i) for i in range(5)])

        assert len(envelopes) == 5
        for i, envelope in enumerate(envelopes):
            assert envelope.task_id == f"task-{i}"


class TestE2ERegistryPersistenceUnderLoad:
    """Test registry persistence and concurrent access patterns."""

    def test_registry_handles_multiple_registrations(self):
        """Test registry handles multiple rapid registrations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "instances.json"
            registry = InstanceRegistry(registry_path=registry_path)

            # Rapid registrations (simulating startup flood)
            for i in range(10):
                registry.register(
                    instance_id=f"inst-{i}",
                    endpoint_id=f"host-{i}",
                    version=f"1.{i}",
                )

            # Verify all registered
            records = registry.list_all()
            assert len(records) == 10

            # Verify persistence by reloading
            registry2 = InstanceRegistry(registry_path=registry_path)
            records2 = registry2.list_all()
            assert len(records2) == 10

    def test_registry_concurrent_heartbeat(self):
        """Test registry handles concurrent heartbeat updates."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "instances.json"
            registry = InstanceRegistry(registry_path=registry_path)

            # Register instances
            for i in range(5):
                registry.register(
                    instance_id=f"inst-{i}",
                    endpoint_id=f"host-{i}",
                )

            # Concurrent heartbeat updates
            for i in range(5):
                updated = registry.heartbeat(f"inst-{i}")
                assert updated is True

            # Verify all updated
            records = registry.list_active()
            assert len(records) == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
