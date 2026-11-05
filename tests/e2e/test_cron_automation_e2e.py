"""E2E tests for cron automation — loss trigger detection and forge orchestration.

Tests:
- E2E: Loss signal triggers forge optimizer
- Respect autonomous_forge_enabled flag
- Operator can manually trigger cron
- Audit trail records all events

ADR-0613: Loss signals feed autonomous forge loop.
"""

import json
import sys
import time
from pathlib import Path

import pytest

# `corvin_operator/skill-forge/` carries a dash, so `corvin_operator.skill_forge`
# never resolves as a plain import. The console registers that alias at runtime
# (routes/autonomous_forge_routes.py); the shared test helper registers the same one.
_SKILL_FORGE_TESTS = Path(__file__).resolve().parents[2] / "corvin_operator" / "skill-forge" / "tests"
if str(_SKILL_FORGE_TESTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_FORGE_TESTS))
import _skill_forge_ns  # noqa: E402,F401 — registers corvin_operator.skill_forge

from core.paths import tenant_audit_chain  # noqa: E402
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


#: 10 correct + 5 incorrect outcomes -> confidence 10/15 (below the 0.70 threshold).
LOW_CONFIDENCE = 10 / 15

_FEEDBACK_FIELDS = frozenset({"skill_id", "skill_version", "tenant_id", "outcome_feedback"})


def _core_security_events():
    """The core chain writer (``corvin_operator/forge/forge/security_events``)."""
    forge_dir = Path(__file__).resolve().parents[2] / "corvin_operator" / "forge"
    if str(forge_dir) not in sys.path:
        sys.path.insert(0, str(forge_dir))
    from forge import security_events

    return security_events


def _write_skill_feedback(monkeypatch, tenant_id: str, skill_id: str, outcomes: list[bool]) -> Path:
    """Write one chained ``skill.feedback`` record per outcome through the REAL writer.

    The detector reads only what the core writer produces: a hash-chained record
    (it verifies the chain first, fail-closed) whose payload sits under
    ``details`` with the tenant's own id (audit-injection fix 3df49d947). Raw
    unchained lines, as this file used to plant, are both refused by the chain
    check and invisible to the ``details``-based filter. The writer refuses a
    ``details.tenant_id`` other than the process tenant, so it is set per tenant.
    """
    se = _core_security_events()
    se.register_event_allowlist("skill.feedback", _FEEDBACK_FIELDS)
    chain = tenant_audit_chain(tenant_id)
    chain.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    monkeypatch.setenv("CORVIN_TENANT_ID", tenant_id)
    try:
        for i, correct in enumerate(outcomes):
            record = se.write_event(
                chain, "skill.feedback", severity="INFO",
                ts=now - 60 * (len(outcomes) - i),
                details={
                    "skill_id": skill_id,
                    "skill_version": "1.0.0",
                    "tenant_id": tenant_id,
                    "outcome_feedback": {"correct": correct},
                },
            )
            assert not (record.get("details") or {}).get("_dropped_fields"), record
    finally:
        monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    return chain


def _chain_events(tenant_id: str, event_type: str) -> list[dict]:
    chain = tenant_audit_chain(tenant_id)
    return [
        rec for rec in (json.loads(line) for line in chain.read_text(encoding="utf-8").splitlines() if line.strip())
        if rec.get("event_type") == event_type
    ]


@pytest.fixture
def setup_tenant(temp_corvin_home, monkeypatch):
    """Tenant ``_default`` whose chain carries 10 correct + 5 incorrect outcomes."""
    return _write_skill_feedback(monkeypatch, "_default", "os.delegation_router", [True] * 10 + [False] * 5)


def test_e2e_cron_detects_loss_triggers_forge(
    temp_corvin_home, setup_tenant
):
    """E2E: Loss signal detected → forge triggered (autonomous enabled).

    Scenario:
    1. Skill has low confidence (10/15 < 0.70 threshold)
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
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f)

    # Run cron poll
    poller = CronTriggerPoller()
    triggers = poller.run_once("_default")

    # Verify loss signal detected
    assert len(triggers) == 1
    assert triggers[0].skill_id == "os.delegation_router"
    assert triggers[0].confidence == pytest.approx(LOW_CONFIDENCE)  # 10 correct / 15 total
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

    with open(trigger_file, "r", encoding="utf-8") as f:
        trigger_data = json.load(f)
    assert trigger_data["skill_id"] == "os.delegation_router"

    # Verify chained audit events written (audit-first: alert, then optimizer)
    cron_events = _chain_events("_default", "skill_forge_triggered_by_cron")
    assert len(cron_events) == 1
    assert cron_events[0]["details"]["skill_id"] == "os.delegation_router"
    assert cron_events[0]["details"]["confidence"] == pytest.approx(LOW_CONFIDENCE)
    optimizer_events = _chain_events("_default", "skill_forge_optimizer_triggered")
    assert [e["details"]["skill_id"] for e in optimizer_events] == ["os.delegation_router"]
    ok, problems = _core_security_events().verify_chain(tenant_audit_chain("_default"))
    assert ok, problems


def test_e2e_cron_respects_autonomous_disabled_flag(
    temp_corvin_home, setup_tenant
):
    """E2E: Loss signal detected but forge NOT triggered (autonomous disabled).

    Scenario:
    1. Skill has low confidence (10/15 < 0.70 threshold)
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
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f)

    # Run cron poll
    poller = CronTriggerPoller()
    triggers = poller.run_once("_default")

    # Verify loss signal detected
    assert len(triggers) == 1
    assert triggers[0].confidence == pytest.approx(LOW_CONFIDENCE)

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

    # Verify cron trigger audit event still written, and no optimizer event
    cron_events = _chain_events("_default", "skill_forge_triggered_by_cron")
    assert len(cron_events) == 1
    assert cron_events[0]["details"]["skill_id"] == "os.delegation_router"
    assert _chain_events("_default", "skill_forge_optimizer_triggered") == []


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


def test_e2e_multiple_tenants_polled_independently(temp_corvin_home, monkeypatch):
    """E2E: Multiple tenants polled independently with separate configurations.

    Scenario:
    1. Setup tenant _default with low confidence
    2. Setup tenant tenant1 with high confidence (no loss)
    3. Run poll_all_tenants()
    4. Verify: Only _default has loss trigger
    """
    # _default: 10 correct + 5 incorrect (below threshold); tenant1: 100 correct (above).
    _write_skill_feedback(monkeypatch, "_default", "test.skill", [True] * 10 + [False] * 5)
    _write_skill_feedback(monkeypatch, "tenant1", "test.skill", [True] * 100)

    # Run poll
    poller = CronTriggerPoller()
    total = poller.poll_all_tenants()

    # Verify only _default has loss signal
    assert total == 1

    # Verify status updated
    status = poller.get_status()
    assert status["last_poll_count"] == 1
    assert status["last_poll_time"] is not None
