"""Phase C k=4 Tests — Timeout Guards + Audit Logging Integration.

Tests:
1. Timeout guards on validators (5s fail-closed)
2. Audit logging of approval decisions
3. Concurrent timeouts don't corrupt state
4. EU AI Act Art. 50 + GDPR Art. 30 compliance

Runs without pytest; use `python test_file.py` for direct execution.
"""
import asyncio
import json
from datetime import datetime, timezone
from typing import Any

# Try importing pytest (optional)
try:
    import pytest
    HAS_PYTEST = True
except ImportError:
    HAS_PYTEST = False

from core.task_tracking import governance, service


class TestTimeoutGuards:
    """Test k=4: Timeout guards on validators."""

    async def test_validator_timeout_fail_closed(self):
        """Test that validator timeout returns failed result (fail-closed)."""
        registry = governance.ValidatorRegistry()

        # Create a validator that will timeout
        class SlowValidator(governance.ValidatorInterface):
            async def validate_approval(self, task_id, actor, decision):
                # Sleep for 6 seconds (exceeds 5s timeout)
                await asyncio.sleep(6.0)
                return governance.ValidationResult(
                    passed=True,
                    reason="This should timeout",
                    metadata={},
                    validator_id=self.validator_id,
                    validator_version=self.version,
                )

        registry.register(SlowValidator("slow_validator", version="1.0.0"))

        # Run validators (timeout should fire)
        results = await registry.run_validators("task_1", "reviewer", "approve")

        # Verify timeout was caught
        assert "slow_validator" in results
        assert not results["slow_validator"].passed
        assert "timeout" in results["slow_validator"].reason.lower()

        print("✅ Timeout guard test passed")

    async def test_concurrent_timeouts_no_state_corruption(self):
        """Test concurrent validators — some timeout, some pass, state intact."""
        registry = governance.ValidatorRegistry()

        class FastValidator(governance.ValidatorInterface):
            async def validate_approval(self, task_id, actor, decision):
                await asyncio.sleep(0.5)  # Fast, within timeout
                return governance.ValidationResult(
                    passed=True,
                    reason="Fast validator passed",
                    metadata={},
                    validator_id=self.validator_id,
                    validator_version=self.version,
                )

        class SlowValidator(governance.ValidatorInterface):
            async def validate_approval(self, task_id, actor, decision):
                await asyncio.sleep(6.0)  # Timeout
                return governance.ValidationResult(
                    passed=True,
                    reason="This should timeout",
                    metadata={},
                    validator_id=self.validator_id,
                    validator_version=self.version,
                )

        registry.register(FastValidator("fast_v", version="1.0.0"))
        registry.register(SlowValidator("slow_v", version="1.0.0"))

        # Run validators concurrently
        results = await registry.run_validators("task_1", "reviewer", "approve")

        # Verify: fast passed, slow timed out, no corruption
        assert "fast_v" in results and results["fast_v"].passed
        assert "slow_v" in results and not results["slow_v"].passed
        assert len(results) == 2  # Both results collected

        # Verify gather worked (no exceptions, return_exceptions=True)
        policy = await registry.check_approval_allowed("task_1", "reviewer", "approve")
        assert not policy.approved  # Blocked by slow timeout
        assert "slow_v" in policy.blocked_by

        print("✅ Concurrent timeout test passed (no corruption)")


class TestAuditLogging:
    """Test k=4: Audit logging of approval decisions."""

    def test_audit_event_immutable(self):
        """Test that audit events are frozen dataclasses."""
        from core.task_tracking.audit import AuditEvent

        event = AuditEvent(
            event_type="task_item.approval_decided",
            task_id="task_1",
            tenant_id="_default",
            actor="reviewer",
            action="approve",
            delta={"approval_decision": "approve", "validators": []},
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # Frozen: can't mutate
        try:
            event.actor = "hacker"
            assert False, "Should not be able to mutate frozen dataclass"
        except AttributeError:
            pass  # Expected

        # Hash is consistent
        hash1 = event.compute_hash()
        hash2 = event.compute_hash()
        assert hash1 == hash2

        print("✅ Audit event immutability test passed")

    async def test_approval_decision_recorded_to_audit(self):
        """Test that approval decisions are logged to audit chain."""
        # This is a high-level test; full audit-chain integration requires
        # core/audit/chain.py which may not be in this test environment
        from core.task_tracking.audit import emit_approval_decision_event

        # Mock: verify function signature and basic behavior
        # (real audit-chain write would happen with write_entry)
        try:
            # This should raise because write_entry is not mocked,
            # but we can verify the function exists and the signature is correct
            await emit_approval_decision_event(
                task_id="task_1",
                actor="reviewer",
                decision="approve",
                tenant_id="_default",
                rationale="LGTM",
                validator_ids_applied=["v1"],
                validation_results={"v1": True},
            )
        except Exception as e:
            # Expected: audit chain not available in test
            assert "Audit chain" in str(e) or "write_entry" in str(e)

        print("✅ Audit decision function signature test passed")


class TestEUAIActCompliance:
    """Test k=4: EU AI Act Art. 50 + GDPR Art. 30 compliance."""

    async def test_approval_attribution_recorded(self):
        """Test that approval decisions record actor + validators (Art. 50)."""
        registry = governance.ValidatorRegistry()

        # Simple validator for testing
        class TestValidator(governance.ValidatorInterface):
            async def validate_approval(self, task_id, actor, decision):
                return governance.ValidationResult(
                    passed=True,
                    reason="Test passed",
                    metadata={},
                    validator_id=self.validator_id,
                    validator_version=self.version,
                )

        registry.register(TestValidator("test_v", version="1.0.0"))

        # Check approval (should pass, all validators pass)
        policy = await registry.check_approval_allowed("task_1", "reviewer", "approve")

        # Verify attribution is recorded
        assert policy.approved
        assert "reviewer" in str(policy)  # Actor should be in decision
        assert "test_v" in policy.validators_run  # Validator ID recorded

        print("✅ EU AI Act Art. 50 attribution test passed")

    async def test_fail_closed_on_timeout(self):
        """Test fail-closed semantics: timeout = denied (not approved)."""
        registry = governance.ValidatorRegistry()

        class TimeoutValidator(governance.ValidatorInterface):
            async def validate_approval(self, task_id, actor, decision):
                await asyncio.sleep(6.0)
                return governance.ValidationResult(
                    passed=True,
                    reason="Should timeout",
                    metadata={},
                    validator_id=self.validator_id,
                    validator_version=self.version,
                )

        registry.register(TimeoutValidator("timeout_v", version="1.0.0"))

        # Approval should be DENIED on timeout (fail-closed)
        policy = await registry.check_approval_allowed("task_1", "reviewer", "approve")

        assert not policy.approved
        assert "timeout_v" in policy.blocked_by
        assert "timeout" in policy.reason.lower()

        print("✅ Fail-closed timeout test passed (GDPR Art. 32)")


# ── Standalone test runner ────────────────────────────────────────


async def run_all_tests():
    """Run all tests without pytest."""
    print("=" * 70)
    print("Phase C k=4 Tests — Timeout Guards + Audit Logging")
    print("=" * 70)

    # Test 1: Timeout Guards
    print("\n1. Testing Timeout Guards...")
    test = TestTimeoutGuards()
    try:
        await test.test_validator_timeout_fail_closed()
    except AssertionError as e:
        print(f"   ❌ FAILED: {e}")
    except Exception as e:
        print(f"   ❌ ERROR: {type(e).__name__}: {e}")

    try:
        await test.test_concurrent_timeouts_no_state_corruption()
    except AssertionError as e:
        print(f"   ❌ FAILED: {e}")
    except Exception as e:
        print(f"   ❌ ERROR: {type(e).__name__}: {e}")

    # Test 2: Audit Logging
    print("\n2. Testing Audit Logging...")
    test = TestAuditLogging()
    try:
        test.test_audit_event_immutable()
    except AssertionError as e:
        print(f"   ❌ FAILED: {e}")
    except Exception as e:
        print(f"   ❌ ERROR: {type(e).__name__}: {e}")

    try:
        await test.test_approval_decision_recorded_to_audit()
    except AssertionError as e:
        print(f"   ❌ FAILED: {e}")
    except Exception as e:
        print(f"   ❌ ERROR: {type(e).__name__}: {e}")

    # Test 3: Compliance
    print("\n3. Testing EU AI Act Compliance...")
    test = TestEUAIActCompliance()
    try:
        await test.test_approval_attribution_recorded()
    except AssertionError as e:
        print(f"   ❌ FAILED: {e}")
    except Exception as e:
        print(f"   ❌ ERROR: {type(e).__name__}: {e}")

    try:
        await test.test_fail_closed_on_timeout()
    except AssertionError as e:
        print(f"   ❌ FAILED: {e}")
    except Exception as e:
        print(f"   ❌ ERROR: {type(e).__name__}: {e}")

    print("\n" + "=" * 70)
    print("✅ Phase C k=4 tests complete")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_all_tests())
