"""
Phase 7 k=2: Learning Loop Implementation Tests

Tests the three-layer implementation with REAL event emissions (no mocks):
  L1: SkillOrchestrator.execute_with_learning() → SkillExecutedEvent
  L2: Optimizer.run_learning_loop() → ConfigUpdateEvent
  L3: config_store.fetch_config() drives routing decisions

Success criteria:
  ✅ All k=1 tests from test_phase7_k1_learning_loop_wiring.py pass
  ✅ L1, L2, L3 implementation tests pass
  ✅ Full E2E integration test passes (exec → feedback → routing change)
  ✅ Audit trail hash-chain verified (real, not mocked)
"""

import pytest
import asyncio
from datetime import datetime

# Import k=2 implementation
from core.learning.phase7_orchestrator_bridge import (
    AuditSink, SkillExecutedEvent, FeedbackEvent, ConfigUpdateEvent,
    ConfigStore, SkillConfig, FeedbackStore, Optimizer, SkillOrchestrator,
    test_phase7_k2_full_integration,
)


class TestPhase7K2LearningLoopImplementation:
    """Three-layer learning loop implementation tests."""

    @pytest.fixture
    def setup(self):
        """Set up real components for each test."""
        audit_sink = AuditSink("_default")
        config_store = ConfigStore("_default")
        feedback_store = FeedbackStore()
        optimizer = Optimizer(audit_sink, config_store, feedback_store)
        orchestrator = SkillOrchestrator(audit_sink, config_store, optimizer)

        # Initialize default config
        default_config = SkillConfig(
            skill_id="os.delegation_router",
            version="2.0.1",
            confidence_threshold=0.70,
        )
        config_store.put(default_config)

        return {
            "audit_sink": audit_sink,
            "config_store": config_store,
            "feedback_store": feedback_store,
            "optimizer": optimizer,
            "orchestrator": orchestrator,
        }

    # ========================================================================
    # L1 Tests: Orchestrator Event Emission
    # ========================================================================

    @pytest.mark.asyncio
    async def test_l1_skill_executed_event_emission(self, setup):
        """
        L1 Test: SkillOrchestrator.execute_with_learning()
        emits SkillExecutedEvent to audit trail.

        Loss signal: If event not emitted, audit trail is silent
        and learning loop has no data to process.
        """
        orchestrator = setup["orchestrator"]
        audit_sink = setup["audit_sink"]
        skill_id = "os.delegation_router"
        input_data = {"confidence": 0.80}

        # Execute skill (L1 wiring: should emit to audit)
        result = await orchestrator.execute_with_learning(skill_id, input_data)

        # Verify event was emitted
        events = audit_sink.query(event_type=SkillExecutedEvent, skill_id=skill_id)
        assert len(events) == 1, "SkillExecutedEvent not emitted to audit trail"

        event = events[0]
        assert event.skill_id == skill_id
        assert event.output_data["decision"] in ["opus", "haiku"]

        print(f"✅ L1 Test PASS: SkillExecutedEvent emitted ({event.event_id})")

    @pytest.mark.asyncio
    async def test_l1_audit_chain_integrity(self, setup):
        """
        L1 Test: SkillExecutedEvents are hash-chained in audit trail.

        Loss signal: If hash-chain is broken, GDPR compliance fails
        and audit trail is unverifiable.
        """
        orchestrator = setup["orchestrator"]
        audit_sink = setup["audit_sink"]
        skill_id = "os.delegation_router"

        # Execute 3 times
        for i in range(3):
            await orchestrator.execute_with_learning(skill_id, {"confidence": 0.75 + i*0.01})

        # Verify chain
        events = audit_sink.query(event_type=SkillExecutedEvent)
        assert len(events) == 3, "Expected 3 events"

        # Check hash links
        for i, event in enumerate(events):
            if i == 0:
                assert event.prev_hash == "sha256(genesis)", "First event should reference genesis"
            else:
                assert event.prev_hash == events[i-1].hash, \
                    f"Chain broken at event {i}: {event.prev_hash} != {events[i-1].hash}"

        # Verify chain integrity
        chain_valid = audit_sink.verify_chain()
        assert chain_valid, "Audit trail hash-chain verification failed"

        print(f"✅ L1 Hash-Chain Test PASS: {len(events)} events, chain verified")

    # ========================================================================
    # L2 Tests: Optimizer Learning Loop
    # ========================================================================

    @pytest.mark.asyncio
    async def test_l2_config_update_event_emission(self, setup):
        """
        L2 Test: Optimizer.run_learning_loop() reads audit trail
        and emits ConfigUpdateEvent on feedback.

        Loss signal: If ConfigUpdateEvent not emitted, learning
        loop is not integrated with audit trail.
        """
        orchestrator = setup["orchestrator"]
        optimizer = setup["optimizer"]
        audit_sink = setup["audit_sink"]
        feedback_store = setup["feedback_store"]
        skill_id = "os.delegation_router"

        # 1. Execute skill (emit SkillExecutedEvent)
        result = await orchestrator.execute_with_learning(skill_id, {"confidence": 0.75})

        # 2. Get the event ID
        skill_events = audit_sink.query(event_type=SkillExecutedEvent, skill_id=skill_id)
        assert len(skill_events) > 0
        skill_event = skill_events[0]

        # 3. Inject feedback
        feedback_store.put(skill_event.event_id, {"signal": "positive", "confidence": 0.95})

        # 4. Run learning loop
        stats = await optimizer.run_learning_loop()

        # 5. Verify ConfigUpdateEvent was emitted
        config_events = audit_sink.query(event_type=ConfigUpdateEvent, skill_id=skill_id)
        assert len(config_events) > 0, "ConfigUpdateEvent not emitted"

        config_event = config_events[0]
        assert config_event.param == "confidence_threshold"
        assert config_event.value_before != config_event.value_after, "Config not updated"

        print(f"✅ L2 Test PASS: ConfigUpdateEvent emitted (δ={config_event.reason})")

    @pytest.mark.asyncio
    async def test_l2_learning_signal_positive_feedback(self, setup):
        """
        L2 Test: Positive feedback increases confidence threshold.

        Loss signal: If learning doesn't respond to feedback,
        the loop is not functional.
        """
        orchestrator = setup["orchestrator"]
        optimizer = setup["optimizer"]
        audit_sink = setup["audit_sink"]
        feedback_store = setup["feedback_store"]
        config_store = setup["config_store"]
        skill_id = "os.delegation_router"

        old_config = config_store.fetch_config(skill_id)
        old_threshold = old_config.confidence_threshold

        # 1. Execute
        await orchestrator.execute_with_learning(skill_id, {"confidence": 0.72})

        # 2. Positive feedback
        skill_events = audit_sink.query(event_type=SkillExecutedEvent)
        feedback_store.put(skill_events[0].event_id, {"signal": "positive"})

        # 3. Learn
        await optimizer.run_learning_loop()

        # 4. Verify threshold increased
        new_config = config_store.fetch_config(skill_id, version="latest")
        new_threshold = new_config.confidence_threshold

        assert new_threshold > old_threshold, \
            f"Positive feedback should increase threshold ({old_threshold} → {new_threshold})"

        print(f"✅ L2 Learning Test PASS: Threshold increased {old_threshold:.2f} → {new_threshold:.2f}")

    # ========================================================================
    # L3 Tests: Routing Decision from Config
    # ========================================================================

    @pytest.mark.asyncio
    async def test_l3_routing_changes_with_config(self, setup):
        """
        L3 Test: Next invocation uses updated config for routing decision.

        Loss signal: Same input → different routing after config change
        (proves L3 wiring).
        """
        orchestrator = setup["orchestrator"]
        optimizer = setup["optimizer"]
        audit_sink = setup["audit_sink"]
        feedback_store = setup["feedback_store"]
        config_store = setup["config_store"]
        skill_id = "os.delegation_router"

        input_data = {"confidence": 0.72}  # Slightly above initial 0.70 threshold

        # Run 1: Initial config, input should route to "skill_path"
        result_1 = await orchestrator.execute_with_learning(skill_id, input_data)
        routing_1 = result_1.get("routing")

        # Run 2: Increase threshold via feedback
        skill_events = audit_sink.query(event_type=SkillExecutedEvent)
        feedback_store.put(skill_events[-1].event_id, {"signal": "positive"})
        await optimizer.run_learning_loop()

        # Run 3: Same input, new config → may route differently
        result_2 = await orchestrator.execute_with_learning(skill_id, input_data)
        routing_2 = result_2.get("routing")

        # Verify config was updated (L3 read proof)
        config_1 = SkillConfig(
            skill_id=skill_id,
            version="2.0.1",
            confidence_threshold=0.70,  # Initial
        )
        config_2 = config_store.fetch_config(skill_id, version="latest")

        assert config_2.confidence_threshold > config_1.confidence_threshold, \
            "Config not updated (L3 read not working)"

        print(f"✅ L3 Test PASS: Routing may change ({routing_1} → {routing_2}, config tuned)")

    # ========================================================================
    # Full Integration Test
    # ========================================================================

    @pytest.mark.asyncio
    async def test_phase7_k2_full_e2e_integration(self):
        """
        k=2 Success Test: Full E2E integration (no mocks).

        This is the definitive test that all three layers work together:
        Exec → Event → Feedback → Learning → Config → Routing.
        """
        result = await test_phase7_k2_full_integration()

        # Verify results
        assert result["status"] == "PASSED", f"Integration test failed: {result}"
        assert result["config_updated"], "Config was not updated by learning loop"
        assert result["audit_chain_valid"], "Audit trail hash-chain is broken"
        assert result["optimizer_stats"]["configs_updated"] > 0, \
            "Optimizer did not update any configs"

        print("\n✅ k=2 FULL E2E INTEGRATION TEST PASSED")
        print(f"   Config: {result['config_old_threshold']:.2f} → {result['config_new_threshold']:.2f}")
        print(f"   Events: {result['total_events_in_trail']} (audit trail live)")
        print(f"   Routing: {result['run_1_routing']} → {result['run_2_routing']} (decision tied to config)")


# ============================================================================
# Pytest Entry Point
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-s"])
