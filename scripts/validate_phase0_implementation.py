#!/usr/bin/env python3
"""Phase 0 Implementation Validation Script.

Verifies:
1. All core modules can be imported
2. Basic instantiation works
3. Key functions execute without errors
"""

from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime

# Add repo to path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))


def validate_engine_interface():
    """Validate EngineInterface module."""
    print("\n[1/5] Validating EngineInterface...")
    try:
        from core.engines.engine_interface import (
            EngineType,
            EngineStatus,
            EngineCapability,
            EngineRequest,
            EngineResponse,
            EnginePool,
        )

        # Test instantiation
        capability = EngineCapability(
            engine_type=EngineType.HAIKU,
            max_latency_ms=100,
            max_tokens=2048,
            cost_per_1m_input_tokens=80,
            cost_per_1m_output_tokens=400,
        )

        request = EngineRequest(
            task_id="test-1",
            task_type="code_gen",
            prompt="Hello world",
        )

        response = EngineResponse(
            task_id="test-1",
            engine_type=EngineType.HAIKU,
            success=True,
            output="Test output",
        )

        pool = EnginePool()

        print("  ✓ EngineInterface module OK")
        return True

    except Exception as e:
        print(f"  ✗ EngineInterface module FAILED: {e}")
        return False


def validate_execution_context():
    """Validate ExecutionContext module."""
    print("[2/5] Validating ExecutionContext...")
    try:
        from core.engines.execution_context import (
            ExecutionState,
            ExecutionContext,
            ExecutionContextUpdate,
            ExecutionContextStore,
        )

        # Test instantiation
        context = ExecutionContext(
            task_id="task-1",
            tenant_id="_default",
            session_id="session-1",
            user_id="user-1",
        )

        # Test methods
        json_str = context.to_json()
        context2 = ExecutionContext.from_json(json_str)
        assert context2.task_id == "task-1"

        # Test store
        store = ExecutionContextStore()
        store.save(context)
        loaded = store.load("task-1")
        assert loaded is not None

        print("  ✓ ExecutionContext module OK")
        return True

    except Exception as e:
        print(f"  ✗ ExecutionContext module FAILED: {e}")
        return False


def validate_event_store():
    """Validate EventStore module."""
    print("[3/5] Validating EventStore...")
    try:
        import tempfile
        from core.learning.event_schema import LearningEvent, LearningEventType
        from core.learning.event_store import EventStore

        # Create temp database
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            store = EventStore(db_path)

            # Create and write event
            event = LearningEvent(
                event_type=LearningEventType.OUTCOME_OBSERVED,
                tenant_id="_default",
                instance_id="instance-1",
                skill_name="test_skill",
                session_id="session-1",
                timestamp_utc=datetime.utcnow(),
            )

            event_hash = store.write_event(event)
            assert event_hash is not None

            # Verify chain
            assert store.verify_chain() is True

            # Read event
            read_event = store.read_event(event.event_id)
            assert read_event is not None

            print("  ✓ EventStore module OK")
            return True

        finally:
            Path(db_path).unlink()

    except Exception as e:
        print(f"  ✗ EventStore module FAILED: {e}")
        return False


def validate_audit_chain_writer():
    """Validate AuditChainWriter module."""
    print("[4/5] Validating AuditChainWriter...")
    try:
        import tempfile
        from core.compliance.audit_chain_writer import (
            AuditEvent,
            AuditChainWriter,
        )

        # Create temp log file
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            log_path = f.name

        try:
            writer = AuditChainWriter(log_path)

            # Write event
            event = AuditEvent(
                event_id="audit-1",
                event_type="test_event",
                tenant_id="_default",
                user_id="user-1",
                timestamp=datetime.utcnow().isoformat(),
                details={"test": "data"},
            )

            event_hash = writer.write_event(event)
            assert event_hash is not None

            # Verify chain
            assert writer.verify_chain() is True

            # Read events
            events = writer.read_events()
            assert len(events) == 1

            print("  ✓ AuditChainWriter module OK")
            return True

        finally:
            Path(log_path).unlink()

    except Exception as e:
        print(f"  ✗ AuditChainWriter module FAILED: {e}")
        return False


def validate_bayesian_tuner():
    """Validate BayesianTemplateTuner module."""
    print("[5/5] Validating BayesianTemplateTuner...")
    try:
        from core.learning.bayesian_tuner import (
            TaskTemplate,
            TaskOutcome,
            BayesianTemplateTuner,
            TemplateRegistry,
        )

        # Create template
        template = TaskTemplate(
            template_id="t1",
            task_type="code_gen",
            engine="haiku",
            prompt_style="concise",
            temperature=0.7,
            max_tokens=2048,
        )

        # Create tuner
        tuner = BayesianTemplateTuner(template)

        # Add outcomes
        for i in range(60):
            outcome = TaskOutcome(
                task_id=f"task-{i}",
                template_id="t1",
                accuracy=0.85,
                latency_ms=50,
                cost_cents=10,
                quality_score=0.8,
            )
            tuner.update(outcome)

        # Check convergence
        assert tuner.convergence_check() is True

        # Test registry
        registry = TemplateRegistry()
        registry.register_template(template)
        stats = registry.get_stats()
        assert stats["total_templates"] == 1

        print("  ✓ BayesianTemplateTuner module OK")
        return True

    except Exception as e:
        print(f"  ✗ BayesianTemplateTuner module FAILED: {e}")
        return False


def main():
    """Run all Phase 0 validations."""
    print("=" * 80)
    print("PHASE 0 IMPLEMENTATION VALIDATION")
    print("=" * 80)
    print(f"\nTimestamp: {datetime.utcnow().isoformat()}")

    results = {
        "EngineInterface": validate_engine_interface(),
        "ExecutionContext": validate_execution_context(),
        "EventStore": validate_event_store(),
        "AuditChainWriter": validate_audit_chain_writer(),
        "BayesianTemplateTuner": validate_bayesian_tuner(),
    }

    # Summary
    print("\n" + "=" * 80)
    print("VALIDATION SUMMARY")
    print("=" * 80)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for module, result in results.items():
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {module:30} {status}")

    print(f"\nTotal: {passed}/{total} modules passed")

    if passed == total:
        print("\n✓ All Phase 0 modules validated successfully!")
        return 0
    else:
        print(f"\n✗ {total - passed} module(s) failed validation")
        return 1


if __name__ == "__main__":
    exit(main())
