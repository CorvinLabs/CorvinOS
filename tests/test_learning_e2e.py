"""End-to-end tests: TreeOfThoughts full pipeline (Phase 1-6)."""
import asyncio
import tempfile
from pathlib import Path
from core.learning import (
    TreeNode, LearningEventStore, LearningIntegration, 
    update_confidence, ActiveLearningLoop
)
from core.learning.audit import AuditTrail
from core.learning.migration import MigrationPlanner


def test_e2e_full_pipeline():
    """E2E: Register pattern → execute → learn → verify chain."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Phase 1+2: Setup. TreeNodes live in the store INSTANCE (in-memory
        # cache, not persisted), so the pattern must be registered on the same
        # store the integration executes against — a second LearningEventStore
        # on the same directory never sees it.
        integration = LearningIntegration(Path(tmpdir) / "events")
        store = integration.store
        audit = AuditTrail(Path(tmpdir) / "audit")
        
        # Register pattern
        pattern = TreeNode(
            id="pattern_e2e_test",
            level="pattern",
            name="E2E Test Pattern",
            when=["e2e testing"],
            anti_when=["production"]
        )
        store.register_node(pattern)
        
        # Phase 3: Execute with learning
        
        async def test_method():
            return {"success": True, "data": "e2e"}
        
        result = asyncio.run(integration.execute_method_with_learning(
            "pattern_e2e_test",
            test_method,
            context={"stage": "test"}
        ))
        
        assert result["success"], "Execution should succeed"
        
        # Confidence should increase
        node = store.get_node("pattern_e2e_test")
        assert node.confidence > 0.5, f"Confidence should increase (got {node.confidence})"
        assert node.calls_in_production >= 1, "Should track production call"
        
        # Phase 5: Verify audit chain
        chain_valid = audit.verify()
        assert chain_valid, "Audit chain should be valid"


def test_e2e_confidence_convergence():
    """E2E: Repeated successes → confidence convergence to 0.9."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = LearningEventStore(Path(tmpdir) / "events")
        
        pattern = TreeNode(
            id="pattern_convergence",
            level="pattern",
            name="Convergence Test",
            when=[]
        )
        store.register_node(pattern)
        
        # Simulate 10 successes
        for i in range(10):
            from core.learning import LearningEvent
            event = LearningEvent(
                subject_id="pattern_convergence",
                event_type="used",
                confidence_delta=+0.05,
                reason=f"success {i}"
            )
            store.append_event("pattern_convergence", event)
            node = store.get_node("pattern_convergence")
            update_confidence(node, event)
        
        node = store.get_node("pattern_convergence")
        # Documented update (docs/TREE_OF_THOUGHTS_DESIGN.md, "Bayesian blend:
        # 70% prior + 30% new evidence", alpha = 0.3):
        #   new = 0.7·old + 0.3·clip(old + delta)  ⇒  +0.3·0.05 = +0.015 per success
        # so 10 successes move 0.5 → 0.65. The old "> 0.7 (~0.8-0.85)" bar
        # contradicted the design's own arithmetic (N-07 test contract drift).
        expected = 0.5 + 10 * 0.3 * 0.05
        assert abs(node.confidence - expected) < 1e-9, f"got {node.confidence}, expected {expected}"
        assert node.confidence > 0.6
        print(f"✓ Convergence test: confidence = {node.confidence} after 10 successes")


def test_e2e_antipattern_detection():
    """E2E: Antipattern in wrong context → confidence penalty."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = LearningEventStore(Path(tmpdir) / "events")
        
        pattern = TreeNode(
            id="pattern_antipattern",
            level="pattern",
            name="Antipattern Test",
            when=["normal_operation"],
            anti_when=["auth_context"]
        )
        store.register_node(pattern)
        
        # Use in auth context (antipattern)
        from core.learning import LearningEvent
        event = LearningEvent(
            subject_id="pattern_antipattern",
            event_type="antipattern_detected",
            confidence_delta=0.0,  # Will be -0.30 by update_confidence
            reason="used in auth_context"
        )
        store.append_event("pattern_antipattern", event)
        node = store.get_node("pattern_antipattern")
        update_confidence(node, event)
        
        # Confidence should drop
        assert node.confidence < 0.5, "Antipattern should reduce confidence"


def test_e2e_migration_workflow():
    """E2E: Migrate 3 Concepts → Frameworks."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = LearningEventStore(Path(tmpdir) / "events")
        planner = MigrationPlanner(store)
        
        # Create mock concept files
        concept_dir = Path(tmpdir) / "concepts"
        concept_dir.mkdir()
        
        # Concept 1: Voice Synthesis
        (concept_dir / "voice_synthesis.md").write_text("""
# Voice Synthesis Framework

## When
- Need to generate audio from text
- Production quality required

## When NOT to use
- Offline environments
""")
        
        # Migrate it
        result = planner.migrate_concept_to_framework(
            concept_dir / "voice_synthesis.md",
            "framework_voice_synthesis"
        )
        
        assert result is not None, "Migration should succeed"
        assert result.level == "framework"
        
        # Verify it's in the store
        node = store.get_node("framework_voice_synthesis")
        assert node is not None
        assert node.name == "Voice Synthesis Framework"


def test_e2e_operator_feedback_loop():
    """E2E: Operator grades patterns → confidence updates → system learns."""
    with tempfile.TemporaryDirectory() as tmpdir:
        integration = LearningIntegration(Path(tmpdir) / "events")
        
        integration.register_pattern(
            "pattern_feedback_test",
            "Feedback Test",
            when=[]
        )
        
        # Initial confidence
        assert integration.get_pattern_confidence("pattern_feedback_test") == 0.5
        
        # Operator gives positive grade
        integration.grade_pattern(
            "pattern_feedback_test",
            +0.3,
            reason="Operator says it's good"
        )
        
        # Confidence should increase
        new_conf = integration.get_pattern_confidence("pattern_feedback_test")
        assert new_conf > 0.5, "Grade should increase confidence"
        
        # Operator gives negative grade
        integration.grade_pattern(
            "pattern_feedback_test",
            -0.5,
            reason="Operator says it breaks things"
        )
        
        final_conf = integration.get_pattern_confidence("pattern_feedback_test")
        assert final_conf < new_conf, "Negative grade should decrease confidence"
        
        print(f"✓ Operator feedback: {0.5} → {new_conf} → {final_conf}")


if __name__ == "__main__":
    import sys
    
    tests = [
        ("Full Pipeline", test_e2e_full_pipeline),
        ("Confidence Convergence", test_e2e_confidence_convergence),
        ("Antipattern Detection", test_e2e_antipattern_detection),
        ("Migration Workflow", test_e2e_migration_workflow),
        ("Operator Feedback Loop", test_e2e_operator_feedback_loop),
    ]
    
    passed = 0
    for name, test_fn in tests:
        try:
            test_fn()
            print(f"✅ {name}")
            passed += 1
        except Exception as e:
            print(f"❌ {name}: {e}")
    
    print(f"\n{passed}/{len(tests)} E2E tests passed")
    sys.exit(0 if passed == len(tests) else 1)
