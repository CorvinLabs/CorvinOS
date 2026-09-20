"""E2E tests for cron automation — loss trigger detection and forge orchestration.

Tests:
- E2E: Loss signal triggers forge optimizer
- Respect autonomous_forge_enabled flag
- Operator can manually trigger cron
- Audit trail records all events

ADR-0613: Loss signals feed autonomous forge loop.
"""

import json
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from core.paths import tenant_audit_chain, corvin_home
from corvin_operator.skill_forge.autonomous import (
    LossTrigger,
    SkillLossTriggerDetector,
)
from corvin_operator.skill_forge.automation import (
    CronTriggerPoller,
    CronService,
    get_cron_service,
)


@pytest.fixture
def temp_corvin_home(monkeypatch, tmp_path):
    """Provide temporary CORVIN_HOME for E2E tests."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def setup_tenant(temp_corvin_home):
    """Setup test tenant with audit trail."""
    tenant_dir = temp_corvin_home / "tenants" / "_default" / "global" / "forge"
    tenant_dir.mkdir(parents=True, exist_ok=True)

    audit_file = tenant_dir / "audit.jsonl"

    # Write some sample skill execution events
    now = datetime.utcnow()
    events = []

    # 10 correct outcomes
    for i in range(10):
        event = {
            "ts": (now - timedelta(hours=i)).timestamp(),
            "event_type": "skill_executed",
            "tenant_id": "_default",
            "skill_id": "os.delegation_router",
            "version": "1.0.0",
            "outcome_feedback": {"correct": True},
        }
        events.append(event)

    # 5 incorrect outcomes (creates 60% confidence = 0.60 < 0.70 threshold)
    for i in range(5):
        event = {
            "ts": (now - timedelta(hours=10 + i)).timestamp(),
            "event_type": "skill_executed",
            "tenant_id": "_default",
            "skill_id": "os.delegation_router",
            "version": "1.0.0",
            "outcome_feedback": {"correct": False},
        }
        events.append(event)

    with open(audit_file, "w") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")

    return audit_file


def test_e2e_cron_detects_loss_triggers_forge(
    temp_corvin_home, setup_tenant
):
    """E2E: Loss signal detected → forge triggered (autonomous enabled).

    Scenario:
    1. Skill has low confidence (0.60 < 0.70 threshold)
    2. Create config with autonomous_forge_enabled=true
    3. Run cron poll
    4. Verify: Loss signal detected, forge triggered, audit logged
    """
    # Setup config
    config_path = (
        temp_corvin_home / "tenants" / "_default" / "global" / "autonomous_forge.yaml"
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)

    config = {"autonomous_forge": {"enabled": True}}
    with open(config_path, "w") as f:
        json.dump(config, f)

    # Run cron poll
    poller = CronTriggerPoller()
    triggers = poller.run_once("_default")

    # Verify loss signal detected
    assert len(triggers) == 1
    assert triggers[0].skill_id == "os.delegation_router"
    assert triggers[0].confidence == 0.6  # 10 correct / 15 total
    assert triggers[0].confidence < 0.70

    # Verify forge trigger file created
    trigger_file = (
        temp_corvin_home
        / "tenants"
        / "_default"
        / "global"
        / "skill-forge"
        / "triggers"
        / "os.delegation_router.json"
    )
    assert trigger_file.exists()

    with open(trigger_file, "r") as f:
        trigger_data = json.load(f)
    assert trigger_data["skill_id"] == "os.delegation_router"

    # Verify audit events written
    audit_file = (
        temp_corvin_home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    )
    with open(audit_file, "r") as f:
        lines = f.readlines()

    # Find cron trigger events
    cron_events = [
        json.loads(line)
        for line in lines
        if json.loads(line).get("event_type") == "skill_forge_triggered_by_cron"
    ]
    assert len(cron_events) >= 1
    assert cron_events[0]["skill_id"] == "os.delegation_router"
    assert cron_events[0]["confidence"] == 0.6


def test_e2e_cron_respects_autonomous_disabled_flag(
    temp_corvin_home, setup_tenant
):
    """E2E: Loss signal detected but forge NOT triggered (autonomous disabled).

    Scenario:
    1. Skill has low confidence (0.60 < 0.70 threshold)
    2. Create config with autonomous_forge_enabled=false
    3. Run cron poll
    4. Verify: Loss signal detected, forge NOT triggered, audit logged
    """
    # Setup config with autonomous disabled
    config_path = (
        temp_corvin_home / "tenants" / "_default" / "global" / "autonomous_forge.yaml"
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)

    config = {"autonomous_forge": {"enabled": False}}
    with open(config_path, "w") as f:
        json.dump(config, f)

    # Run cron poll
    poller = CronTriggerPoller()
    triggers = poller.run_once("_default")

    # Verify loss signal detected
    assert len(triggers) == 1
    assert triggers[0].confidence == 0.6

    # Verify forge trigger file NOT created
    trigger_file = (
        temp_corvin_home
        / "tenants"
        / "_default"
        / "global"
        / "skill-forge"
        / "triggers"
        / "os.delegation_router.json"
    )
    assert not trigger_file.exists()

    # Verify cron trigger audit event still written
    audit_file = (
        temp_corvin_home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    )
    with open(audit_file, "r") as f:
        lines = f.readlines()

    cron_events = [
        json.loads(line)
        for line in lines
        if json.loads(line).get("event_type") == "skill_forge_triggered_by_cron"
    ]
    assert len(cron_events) >= 1
    assert cron_events[0]["skill_id"] == "os.delegation_router"


def test_e2e_operator_can_manually_trigger_cron(temp_corvin_home, setup_tenant):
    """E2E: Operator can trigger cron manually (doesn't wait 1 hour).

    Scenario:
    1. Call trigger_now() on poller
    2. Verify it runs immediately (blocking)
    3. Verify results returned
    """
    poller = CronTriggerPoller()

    # Manual trigger should be fast (no scheduling delay)
    start_time = time.time()
    result = poller.run_once("_default")
    elapsed = time.time() - start_time

    # Verify result returned immediately
    assert elapsed < 2.0  # Should be sub-second
    assert len(result) == 1
    assert result[0].skill_id == "os.delegation_router"


def test_e2e_cron_service_lifecycle(temp_corvin_home, setup_tenant):
    """E2E: CronService lifecycle (start, pause, resume, stop).

    Scenario:
    1. Start service
    2. Check status
    3. Pause service
    4. Check paused
    5. Resume service
    6. Stop service
    """
    service = CronService()

    # Start service
    service.start()
    assert service.scheduler is not None

    # Get status
    status = service.get_status()
    assert status["running"] is True
    assert status["paused"] is False

    # Pause service
    service.pause()
    assert service._paused is True

    # Resume service
    service.resume()
    assert service._paused is False

    # Stop service
    service.stop()
    assert service.scheduler is None

    # Cleanup
    service._initialized = False


def test_e2e_cron_service_manual_trigger(temp_corvin_home, setup_tenant):
    """E2E: CronService manual trigger endpoints.

    Scenario:
    1. Get service singleton
    2. Call trigger_now() for specific tenant
    3. Verify result returned
    """
    service = get_cron_service()

    result = service.trigger_now("_default")

    assert "tenant_id" in result
    assert result["tenant_id"] == "_default"
    assert "loss_signals" in result
    assert result["loss_signals"] == 1
    assert "elapsed_seconds" in result


def test_e2e_multiple_tenants_polled_independently(temp_corvin_home):
    """E2E: Multiple tenants polled independently with separate configurations.

    Scenario:
    1. Setup tenant _default with low confidence
    2. Setup tenant tenant1 with high confidence (no loss)
    3. Run poll_all_tenants()
    4. Verify: Only _default has loss trigger
    """
    # Setup _default with low confidence
    default_dir = temp_corvin_home / "tenants" / "_default" / "global" / "forge"
    default_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.utcnow()
    default_events = []

    # 10 correct + 5 incorrect = 0.67 confidence (below threshold)
    for i in range(10):
        default_events.append({
            "ts": (now - timedelta(hours=i)).timestamp(),
            "event_type": "skill_executed",
            "tenant_id": "_default",
            "skill_id": "test.skill",
            "version": "1.0.0",
            "outcome_feedback": {"correct": True},
        })

    for i in range(5):
        default_events.append({
            "ts": (now - timedelta(hours=10 + i)).timestamp(),
            "event_type": "skill_executed",
            "tenant_id": "_default",
            "skill_id": "test.skill",
            "version": "1.0.0",
            "outcome_feedback": {"correct": False},
        })

    with open(default_dir / "audit.jsonl", "w") as f:
        for event in default_events:
            f.write(json.dumps(event) + "\n")

    # Setup tenant1 with high confidence
    tenant1_dir = temp_corvin_home / "tenants" / "tenant1" / "global" / "forge"
    tenant1_dir.mkdir(parents=True, exist_ok=True)

    tenant1_events = []

    # 100 correct + 0 incorrect = 1.0 confidence (above threshold)
    for i in range(100):
        tenant1_events.append({
            "ts": (now - timedelta(hours=i % 24)).timestamp(),
            "event_type": "skill_executed",
            "tenant_id": "tenant1",
            "skill_id": "test.skill",
            "version": "1.0.0",
            "outcome_feedback": {"correct": True},
        })

    with open(tenant1_dir / "audit.jsonl", "w") as f:
        for event in tenant1_events:
            f.write(json.dumps(event) + "\n")

    # Run poll
    poller = CronTriggerPoller()
    total = poller.poll_all_tenants()

    # Verify only _default has loss signal
    assert total == 1

    # Verify status updated
    status = poller.get_status()
    assert status["last_poll_count"] == 1
    assert status["last_poll_time"] is not None
