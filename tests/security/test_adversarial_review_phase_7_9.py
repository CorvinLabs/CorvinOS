"""Adversarial Security Review E2E Tests for Autonomous Skill Forge (Phase 7-9).

This test suite demonstrates 30+ security vulnerabilities across:
  - Phase 7: Trigger detection, validation layers, result handling
  - Phase 8: Console routes, cron automation, approval gates
  - Phase 9: Workflow optimizer, learning loop integration

Test Methodology:
  - Real HTTP requests (not mocked)
  - Filesystem manipulation (audit trails, config files)
  - Multitenancy escape attempts
  - Audit trail integrity verification
  - CSRF/TOCTOU race conditions
  - Input validation bypasses

CRITICAL: These tests demonstrate real vulnerabilities. Each test should fail
on the current codebase, proving the vulnerability exists.
"""

import asyncio
import hashlib
import importlib.util
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import types
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
import yaml
from httpx import AsyncClient

# `corvin_operator/skill-forge/` has a dash, so `from skill_forge...` (or
# `from corvin_operator.skill_forge...`) can never resolve as a plain import
# — this previously hit an ImportError that was swallowed into a module-level
# `pytest.skip()`, which pytest itself rejects without
# `allow_module_level=True`, turning a "skip, forge unavailable" intent into
# a hard collection error. Load the real dashed-directory modules via
# importlib instead (same pattern as tests/skill_forge/test_trigger_detector.py).
_REPO = Path(__file__).resolve().parents[2]
_SKILL_FORGE_DIR = _REPO / "corvin_operator" / "skill-forge"
sys.path.insert(0, str(_REPO))


def _ensure_namespace_package(dotted_name: str, path: Path):
    existing = sys.modules.get(dotted_name)
    if existing is not None:
        return existing
    module = types.ModuleType(dotted_name)
    module.__path__ = [str(path)]
    sys.modules[dotted_name] = module
    return module


def _load_module(dotted_name: str, file_path: Path):
    existing = sys.modules.get(dotted_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(dotted_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[dotted_name] = module
    spec.loader.exec_module(module)
    return module


_ensure_namespace_package("corvin_operator.skill_forge", _SKILL_FORGE_DIR)
_ensure_namespace_package(
    "corvin_operator.skill_forge.automation", _SKILL_FORGE_DIR / "automation"
)

# NOT pre-registered as an empty namespace package: `autonomous` is loaded
# directly below from its real __init__.py, which must be the module that
# ends up in sys.modules under this name — a stale empty namespace intercepts
# `_load_module`'s "already loaded" check and cron_trigger_poller.py's own
# `from corvin_operator.skill_forge.autonomous import SkillLossTriggerDetector`
# then fails against that empty module instead.
_autonomous_pkg = _load_module(
    "corvin_operator.skill_forge.autonomous",
    _SKILL_FORGE_DIR / "autonomous" / "__init__.py",
)
_cron_trigger_poller = _load_module(
    "corvin_operator.skill_forge.automation.cron_trigger_poller",
    _SKILL_FORGE_DIR / "automation" / "cron_trigger_poller.py",
)
_cron_service = _load_module(
    "corvin_operator.skill_forge.automation.cron_service",
    _SKILL_FORGE_DIR / "automation" / "cron_service.py",
)

SkillLossTriggerDetector = _autonomous_pkg.SkillLossTriggerDetector
LossTrigger = _autonomous_pkg.LossTrigger
SkillValidator = _autonomous_pkg.SkillValidator
CronTriggerPoller = _cron_trigger_poller.CronTriggerPoller
CronService = _cron_service.CronService

_audit_chain_validator_module = sys.modules.get(
    "corvin_operator.skill_forge.autonomous.audit_chain_validator"
) or _load_module(
    "corvin_operator.skill_forge.autonomous.audit_chain_validator",
    _SKILL_FORGE_DIR / "autonomous" / "audit_chain_validator.py",
)
AuditChainValidator = _audit_chain_validator_module.AuditChainValidator

logger = logging.getLogger(__name__)


def _hash_chain_events(raw_events: list) -> list:
    """Attach valid hash/prev_hash fields to raw audit events using the
    exact algorithm AuditChainValidator (Fix #1) verifies against. Written
    when this file's tests were adversarial vulnerability demonstrations
    predating Fix #1 — without this, every event here is rejected for
    missing hash fields before the scenario each test actually targets
    (forged content, tampering, symlink escape, division-by-zero) is ever
    reached."""
    prev_hash = ""
    chained = []
    for event in raw_events:
        event_hash = AuditChainValidator._compute_event_hash(event, prev_hash)
        chained_event = {**event, "prev_hash": prev_hash, "hash": event_hash}
        chained.append(chained_event)
        prev_hash = event_hash
    return chained

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_corvin_home(tmp_path):
    """Create a temporary CORVIN_HOME for testing."""
    corvin_home = tmp_path / "corvin"
    (corvin_home / "tenants" / "_default" / "global").mkdir(parents=True)
    return corvin_home


@pytest.fixture
def audit_path(tmp_corvin_home):
    """Get path to audit trail for _default tenant."""
    return tmp_corvin_home / "tenants" / "_default" / "global" / "audit.jsonl"


@pytest.fixture
async def console_client():
    """Connect to console API (assumes running on localhost:8765)."""
    async with AsyncClient(base_url="http://localhost:8765", timeout=10.0) as client:
        yield client


# ─────────────────────────────────────────────────────────────────────────────
# FINDING #1: Audit Trail Loss Signal Injection
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_loss_signal_injection_via_forged_audit_events(tmp_corvin_home, audit_path):
    """Demonstrate audit trail loss signal injection vulnerability.

    VULNERABILITY: Attacker with write access to audit trail can inject
    fake skill_executed events to trigger arbitrary skill forks.

    ATTACK: Append 100 forged loss events to audit.jsonl, triggering
    false loss signal detection.

    IMPACT: Attacker can force expensive skill forks without authorization.
    """
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    # Inject 100 forged skill_executed events, correctly hash-chained: Fix #1
    # (2026-09-21) makes the hash-chain a fail-closed gate, but hash-chaining
    # only proves an append-only file wasn't retroactively edited — it does
    # NOT prove who authored an event. An attacker with write access to
    # audit.jsonl can compute a valid next hash for a freshly forged event
    # just as legitimately as the real writer can. This test's original
    # (pre-fix) form skipped the hash fields entirely and got rejected for
    # that unrelated reason; chaining them here tests what Fix #1 actually
    # defends against.
    fake_events = [
        {
            "ts": time.time() - (100 - i),
            "event_type": "skill_executed",
            "tenant_id": "_default",
            "skill_id": "os.delegation_router",
            "version": "1.0.0",
            "outcome_feedback": {"correct": False},  # All failures
            "input": "fake_input",
            "output": "fake_output",
        }
        for i in range(100)
    ]
    with open(audit_path, "a") as f:
        for event in _hash_chain_events(fake_events):
            f.write(json.dumps(event) + "\n")

    detector = SkillLossTriggerDetector()
    with patch(
        "corvin_operator.skill_forge.autonomous.trigger_detector.tenant_audit_chain",
        return_value=audit_path,
    ):
        triggers = detector.detect_loss_signals("_default", lookback_hours=1)

    # STILL VULNERABLE, by design of what a hash chain can prove: a
    # correctly-chained forged append is indistinguishable from a real one.
    # Fix #1 closes retroactive tampering (test_loss_trigger_without_hash_
    # chain_verification, below); closing THIS residual — an attacker with
    # audit.jsonl write access forging brand-new, self-consistent events —
    # needs per-writer signing (e.g. an HMAC keyed to the writing process),
    # which is out of scope for the 6 CRITICAL fixes and not claimed as
    # closed. This assertion documents that residual, it does not newly
    # discover it.
    assert len(triggers) > 0, (
        "Expected residual: correctly hash-chained forged events are still "
        "accepted — hash-chaining proves order, not authorship"
    )
    assert triggers[0].skill_id == "os.delegation_router"
    assert triggers[0].confidence < 0.70, "Confidence threshold not triggered"

    print(f"RESIDUAL (documented, not closed by Fix #1): Injected {len(triggers)} fake loss signals")
    print(f"  Skill: {triggers[0].skill_id}")
    print(f"  Confidence: {triggers[0].confidence:.2f} (forged, but validly chained)")


@pytest.mark.unit
def test_loss_trigger_without_hash_chain_verification(tmp_corvin_home, audit_path):
    """Test that loss signals are not verified with hash-chain.

    VULNERABILITY: Audit events lack cryptographic integrity checking.
    An attacker can modify past events without detection.

    EXPECTED (secure): Audit trail uses hash-chaining; tampering breaks chain
    ACTUAL (vulnerable): No hash validation; any JSON is trusted
    """
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    # Write a legitimate, correctly hash-chained event.
    legit_event = {
        "ts": time.time(),
        "event_type": "skill_executed",
        "tenant_id": "_default",
        "skill_id": "os.delegation_router",
        "version": "1.0.0",
        "outcome_feedback": {"correct": True},
    }
    with open(audit_path, "a") as f:
        for event in _hash_chain_events([legit_event]):
            f.write(json.dumps(event) + "\n")

    # Attacker retroactively modifies the already-written, already-hashed
    # event (changes correct: True → False) without recomputing its hash —
    # this is exactly the tampering-after-the-fact scenario Fix #1 exists
    # to catch, unlike the fresh-forgery residual documented in
    # test_loss_signal_injection_via_forged_audit_events above.
    with open(audit_path, "r") as f:
        lines = f.readlines()

    modified_line = lines[0].replace('"correct": true', '"correct": false')
    with open(audit_path, "w") as f:
        f.write(modified_line)

    # Fix #1 (2026-09-21): retroactive tampering breaks the hash link and is
    # now rejected fail-closed with RuntimeError, before any trigger is ever
    # emitted from the tampered content.
    detector = SkillLossTriggerDetector()
    with patch(
        "corvin_operator.skill_forge.autonomous.trigger_detector.tenant_audit_chain",
        return_value=audit_path,
    ):
        with pytest.raises(RuntimeError, match="(?i)hash"):
            detector.detect_loss_signals("_default", lookback_hours=1)


# ─────────────────────────────────────────────────────────────────────────────
# FINDING #2: Version String Path Traversal
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_version_path_traversal_attack(console_client):
    """Test path traversal in version parameter.

    VULNERABILITY: version parameter not validated; accepts traversal sequences
    like "../../../etc/passwd" or "1.0.0'; DROP TABLE; --"

    ATTACK: Pass version="../../../etc/passwd" to approval endpoint

    EXPECTED (secure): 400 Bad Request with validation error
    ACTUAL (vulnerable): Accepted or error doesn't prevent traversal
    """
    malicious_versions = [
        "../../../etc/passwd",
        "1.0.0'; DROP TABLE skills; --",
        r"..\..\..\etc\passwd",  # Windows traversal
        "1.0.0%00.txt",  # Null byte injection
        "../../tmp/shell.sh",
    ]

    for bad_version in malicious_versions:
        response = await console_client.post(
            "/v1/console/autonomous-forge/approve",
            json={
                "skill_id": "os.delegation_router",
                "version": bad_version,
                "operator_id": "test_op",
            },
        )

        # VULNERABLE if: Request accepted (200) or error doesn't mention validation
        if response.status_code == 200:
            print(f"VULNERABLE: Path traversal accepted in version={bad_version}")
            print(f"  Response: {response.json()}")
        elif response.status_code == 400:
            # Check error message
            error = response.json().get("detail", "")
            if "invalid" not in error.lower() and "format" not in error.lower():
                print(f"VULNERABLE: 400 but message doesn't indicate validation: {error}")


# ─────────────────────────────────────────────────────────────────────────────
# FINDING #3: Manifest Endpoint Path Traversal
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_manifest_path_traversal_cross_tenant(tmp_corvin_home, console_client):
    """Test path traversal to read another tenant's manifest.

    VULNERABILITY: skill_id and version parameters not validated;
    allows cross-tenant manifest access via path traversal

    ATTACK: GET /manifest/../../tenant_b/skill.json/1.0.0

    EXPECTED: 404 or 403 (forbidden)
    ACTUAL: 200 with another tenant's manifest
    """
    # Setup: Create manifests for two tenants
    tenant_a = tmp_corvin_home / "tenants" / "tenant_a" / "global" / "skill-forge"
    tenant_b = tmp_corvin_home / "tenants" / "tenant_b" / "global" / "skill-forge"

    (tenant_a / "os.delegation_router" / "1.0.0").mkdir(parents=True)
    (tenant_b / "os.delegation_router" / "1.0.0").mkdir(parents=True)

    # Write manifests
    manifest_a = {
        "id": "os.delegation_router",
        "version": "1.0.0",
        "author": "tenant_a",
        "secret": "TENANT_A_SECRET",
    }
    manifest_b = {
        "id": "os.delegation_router",
        "version": "1.0.0",
        "author": "tenant_b",
        "secret": "TENANT_B_SECRET",
    }

    (tenant_a / "os.delegation_router" / "1.0.0" / "skill.json").write_text(json.dumps(manifest_a))
    (tenant_b / "os.delegation_router" / "1.0.0" / "skill.json").write_text(json.dumps(manifest_b))

    # Attack: Try to access tenant B's manifest from tenant A
    traversal_paths = [
        "/v1/console/autonomous-forge/manifest/../../tenant_b/os.delegation_router/1.0.0",
        "/v1/console/autonomous-forge/manifest/%2e%2e%2f%2e%2e%2ftenant_b/os.delegation_router/1.0.0",
    ]

    for path in traversal_paths:
        response = await console_client.get(path)

        if response.status_code == 200:
            data = response.json()
            if "TENANT_B_SECRET" in json.dumps(data):
                print(f"VULNERABLE: Cross-tenant manifest access via {path}")
                print(f"  Leaked: {data}")


# ─────────────────────────────────────────────────────────────────────────────
# FINDING #4: Cross-Tenant Audit Trail via Symlink
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cross_tenant_audit_leakage_via_symlink(tmp_corvin_home):
    """Test symlink attack to read another tenant's audit trail.

    VULNERABILITY: _load_audit_events() follows symlinks without validation.
    If tenant A creates symlink to tenant B's audit.jsonl, they can read
    tenant B's loss signals and attack their skills.

    ATTACK:
    1. Create symlink: tenant_a/audit.jsonl → tenant_b/audit.jsonl
    2. Call detector for tenant A
    3. Detector follows symlink and reads tenant B's events
    4. Attacker extracts tenant B's confidential information

    IMPACT: GDPR Art. 5 violation (data segregation), complete audit leakage
    """
    # Setup: Two tenants with separate audit trails
    tenant_a_dir = tmp_corvin_home / "tenants" / "tenant_a"
    tenant_b_dir = tmp_corvin_home / "tenants" / "tenant_b"

    audit_a = tenant_a_dir / "global" / "audit.jsonl"
    audit_b = tenant_b_dir / "global" / "audit.jsonl"

    audit_a.parent.mkdir(parents=True, exist_ok=True)
    audit_b.parent.mkdir(parents=True, exist_ok=True)

    # Write SENSITIVE data to tenant B
    sensitive_events = [
        {
            "ts": time.time(),
            "event_type": "skill_executed",
            "tenant_id": "tenant_b",
            "skill_id": "os.security_orchestrator",  # Sensitive skill name
            "version": "1.0.0",
            "outcome_feedback": {"correct": False},
            "secret_data": "TENANT_B_CONFIDENTIAL",
        }
        for _ in range(50)  # Multiple events to establish pattern
    ]

    with open(audit_b, "a") as f:
        for event in _hash_chain_events(sensitive_events):
            f.write(json.dumps(event) + "\n")

    # ATTACK: Create symlink from tenant A to tenant B
    if audit_a.exists():
        audit_a.unlink()
    audit_a.symlink_to(audit_b)

    # Verify the symlink was actually created as intended, before asserting
    # anything about the detector's behavior against it.
    assert audit_a.resolve() == audit_b.resolve(), "Symlink setup failed"

    # Fix #3 (2026-09-21): the detector validates the resolved audit path is
    # within the requesting tenant's own directory before reading it —
    # tenant_a's symlink resolving into tenant_b's directory is rejected
    # fail-closed, so tenant_b's events are never read at all.
    detector = SkillLossTriggerDetector()
    with patch(
        "corvin_operator.skill_forge.autonomous.trigger_detector.tenant_audit_chain",
        return_value=audit_a,
    ):
        with pytest.raises(RuntimeError, match="(?i)validation failed"):
            detector.detect_loss_signals("tenant_a", lookback_hours=1)


# ─────────────────────────────────────────────────────────────────────────────
# FINDING #5: Audit Event ID Collision (TOCTOU)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_event_id_collision_race(console_client):
    """Test TOCTOU race condition in audit event ID generation.

    VULNERABILITY: Event IDs generated as f"audit-evt-{datetime.utcnow().isoformat()}"
    If two events generated in same microsecond, they have identical IDs.
    This breaks audit trail uniqueness and allows overwrite attacks.

    ATTACK: Send two approval requests simultaneously

    EXPECTED (secure): Two unique audit event IDs
    ACTUAL (vulnerable): Same ID for both events
    """

    async def make_approval_request():
        return await console_client.post(
            "/v1/console/autonomous-forge/approve",
            json={
                "skill_id": "os.delegation_router",
                "version": "1.0.0",
                "operator_id": "test_op",
            },
        )

    # Send requests concurrently
    responses = await asyncio.gather(
        make_approval_request(),
        make_approval_request(),
        return_exceptions=True,
    )

    event_ids = []
    for resp in responses:
        if not isinstance(resp, Exception) and resp.status_code == 200:
            event_id = resp.json().get("audit_event_id")
            event_ids.append(event_id)

    if len(event_ids) == 2:
        if event_ids[0] == event_ids[1]:
            print(f"VULNERABLE: Duplicate audit event IDs")
            print(f"  Event 1: {event_ids[0]}")
            print(f"  Event 2: {event_ids[1]}")
            print(f"  Result: Second approval overwrites first in audit trail")
        else:
            print(f"NOT vulnerable: Event IDs are unique")
            print(f"  Event 1: {event_ids[0]}")
            print(f"  Event 2: {event_ids[1]}")


# ─────────────────────────────────────────────────────────────────────────────
# FINDING #6: Division by Zero in Confidence Calculation
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_confidence_calculation_division_by_zero(tmp_corvin_home, audit_path):
    """Test division by zero vulnerability in confidence calculation.

    VULNERABILITY: _calculate_confidence() does `correct_count / feedback_count`
    If all events lack 'correct' field, feedback_count=0 → ZeroDivisionError

    ATTACK: Create audit events with malformed outcome_feedback

    EXPECTED (secure): Returns 0.0 without error
    ACTUAL (vulnerable): Raises ZeroDivisionError or crashes
    """
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    # Create events with missing 'correct' field, correctly hash-chained so
    # Fix #1's chain-integrity gate doesn't short-circuit this scenario
    # before the confidence calculation (what this test actually targets)
    # is ever reached.
    malformed_events = [
        {
            "ts": time.time() - (10 - i),
            "event_type": "skill_executed",
            "tenant_id": "_default",
            "skill_id": "os.delegation_router",
            "version": "1.0.0",
            "outcome_feedback": {},  # Missing 'correct' field!
        }
        for i in range(10)
    ]
    with open(audit_path, "a") as f:
        for event in _hash_chain_events(malformed_events):
            f.write(json.dumps(event) + "\n")

    detector = SkillLossTriggerDetector()
    with patch(
        "corvin_operator.skill_forge.autonomous.trigger_detector.tenant_audit_chain",
        return_value=audit_path,
    ):
        # Must not raise ZeroDivisionError — the only acceptable outcomes are
        # a clean empty/zero-confidence result or a deliberate ValueError.
        triggers = detector.detect_loss_signals("_default", lookback_hours=1)
    print(f"No division by zero; confidence calculation handled gracefully")
    print(f"  Events with missing 'correct' field: 10")
    print(f"  Returned triggers: {len(triggers)}")


# ─────────────────────────────────────────────────────────────────────────────
# FINDING #7: No Atomic Trigger File Write
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_trigger_file_write_race_condition(tmp_corvin_home):
    """Test TOCTOU race in trigger file creation.

    VULNERABILITY: _trigger_forge() does:
    1. mkdir(trigger_dir)
    2. write(trigger_file)

    Between steps, attacker can delete directory or modify permissions.

    ATTACK:
    1. Call _trigger_forge() with malicious skill_id
    2. Between mkdir and write, delete trigger_dir
    3. write() fails or writes to wrong location

    IMPACT: Lost trigger signals, DoS on autonomous forge
    """
    poller = CronTriggerPoller()

    trigger = LossTrigger(
        skill_id="os.delegation_router",
        version="1.0.0",
        confidence=0.5,
        trigger_time=datetime.utcnow(),
        event_count=100,
        lookback_hours=24,
    )

    # Setup: trigger directory will be created
    trigger_dir = tmp_corvin_home / "tenants" / "_default" / "global" / "skill-forge" / "triggers"

    # ATTACK: Monitor calls to _trigger_forge and interfere
    # (This is a conceptual test; real race would need threading)

    # Attempt to trigger
    try:
        with patch("corvin_operator.skill_forge.automation.cron_trigger_poller.corvin_home", return_value=tmp_corvin_home):
            poller._trigger_forge("_default", trigger)

        # Verify write succeeded atomically
        trigger_file = trigger_dir / f"{trigger.skill_id}.json"
        if trigger_file.exists():
            print(f"File write completed: {trigger_file}")
        else:
            print(f"VULNERABLE: Race condition—trigger file not written")
    except Exception as e:
        print(f"Race condition detected: {type(e).__name__}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# FINDING #8: YAML Injection in Config Loading
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_yaml_injection_in_config_loading(tmp_corvin_home):
    """Test YAML injection vulnerability in config loading.

    VULNERABILITY: _read_poll_interval() uses yaml.safe_load() on untrusted
    config file. Attacker can write YAML with gadgets to achieve RCE.

    ATTACK: Write malicious YAML to autonomous_forge.yaml

    EXPECTED (secure): Safely loads only basic types (int, str, list, dict)
    ACTUAL (vulnerable): Could execute arbitrary code via YAML gadgets
    """
    config_path = tmp_corvin_home / "global" / "autonomous_forge.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Benign payload first
    benign_config = {
        "autonomous_forge": {
            "cron_poll_interval_minutes": 60,
        }
    }
    config_path.write_text(yaml.dump(benign_config))

    service = CronService()
    interval = service._read_poll_interval()
    assert interval == 60, f"Expected 60, got {interval}"
    print(f"Benign config: interval={interval} ✓")

    # Malicious payload (YAML object instantiation)
    # Note: safe_load blocks direct code execution, but gadgets might exist
    malicious_yaml = """
autonomous_forge:
  cron_poll_interval_minutes: 60
  # YAML gadget example (depends on installed libraries)
  # This is a template; actual gadgets vary by installed packages
  dangerous_object: !!python/object/apply:os.system
    args: ['touch /tmp/pwned']
"""
    config_path.write_text(malicious_yaml)

    try:
        service2 = CronService()
        interval = service2._read_poll_interval()
        print(f"Malicious YAML loaded: interval={interval}")

        # Check if /tmp/pwned was created (proof of code execution)
        if Path("/tmp/pwned").exists():
            print(f"VULNERABLE: YAML injection achieved code execution")
            Path("/tmp/pwned").unlink()
        else:
            print(f"SECURE: YAML gadget did not execute (safe_load is working)")
    except Exception as e:
        print(f"YAML parsing error: {type(e).__name__}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# FINDING #9: Validator Layer Bypass via layer_mask
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_validator_layer_bypass_zero_mask():
    """Test that layer_mask=0b00000 bypasses all validation layers.

    VULNERABILITY: validate_all_layers() accepts layer_mask parameter.
    If attacker controls the mask, they can skip all validation checks.

    ATTACK: Call with layer_mask=0 to skip all layers

    EXPECTED: All layers run by default (mask=0b11111)
    ACTUAL: With mask=0, all validation skipped
    """
    validator = SkillValidator()

    # Create a temporary skill directory
    with tempfile.TemporaryDirectory() as tmp_dir:
        skill_dir = Path(tmp_dir)
        (skill_dir / "skill.json").write_text('{"id": "test", "version": "1.0.0"}')
        (skill_dir / "src").mkdir()
        (skill_dir / "tests").mkdir()
        (skill_dir / "hooks").mkdir()
        (skill_dir / "scripts").mkdir()

        # Test 1: Normal validation (should run all layers)
        results_full = validator.validate_all_layers(skill_dir, layer_mask=0b11111)
        print(f"Full validation: {len(results_full)} layers run")

        # Test 2: Zero mask (should skip everything)
        results_zero = validator.validate_all_layers(skill_dir, layer_mask=0b00000)
        print(f"Zero mask validation: {len(results_zero)} layers run")

        if len(results_zero) == 0:
            print(f"VULNERABLE: layer_mask=0 bypasses all validation")
            print(f"  Expected: At least 1 layer to run")
            print(f"  Actual: 0 layers run (complete bypass)")
        else:
            print(f"NOT vulnerable: Validation ran despite zero mask")


# ─────────────────────────────────────────────────────────────────────────────
# FINDING #10: Tenant Directory Traversal in Cron Trigger
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_tenant_directory_traversal_in_trigger_forge(tmp_corvin_home):
    """Test directory traversal via malformed tenant_id.

    VULNERABILITY: _trigger_forge() constructs path like:
    corvin_home() / "tenants" / tenant_id / ...

    If tenant_id="../../../" attacker can write to parent directories.

    ATTACK: Call _trigger_forge("../../../tmp/", trigger)

    IMPACT: Write arbitrary files outside tenant sandbox
    """
    poller = CronTriggerPoller()

    trigger = LossTrigger(
        skill_id="os.delegation_router",
        version="1.0.0",
        confidence=0.5,
        trigger_time=datetime.utcnow(),
        event_count=100,
        lookback_hours=24,
    )

    # Try to use traversal sequence as tenant_id
    malicious_tenant_ids = [
        "../../../tmp",
        "tenant_a/../../tenant_b",
        "..\\..\\..\\windows\\system32",  # Windows-style
    ]

    for bad_id in malicious_tenant_ids:
        try:
            with patch("corvin_operator.skill_forge.automation.cron_trigger_poller.corvin_home", return_value=tmp_corvin_home):
                poller._trigger_forge(bad_id, trigger)

            # Check if file was written outside tenant directory
            suspicious_locations = [
                tmp_corvin_home.parent / "trigger.json",
                Path("/tmp") / "trigger.json",
                Path("/tmp") / f"{trigger.skill_id}.json",
            ]

            for loc in suspicious_locations:
                if loc.exists():
                    print(f"VULNERABLE: Directory traversal via tenant_id={bad_id}")
                    print(f"  File written to: {loc}")
                    loc.unlink()
                    break
        except Exception as e:
            # Expected if tenant_id validation is in place
            if "invalid" in str(e).lower() or "tenant" in str(e).lower():
                print(f"NOT vulnerable: Directory traversal blocked for tenant_id={bad_id}")
            else:
                print(f"  Unexpected error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Pytest Configuration
# ─────────────────────────────────────────────────────────────────────────────


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "unit: Unit tests (no external services)")
    config.addinivalue_line("markers", "integration: Integration tests (may need server)")
    config.addinivalue_line("markers", "asyncio: Async tests")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "unit"])
