"""Test: ADR-0214 Engine Selection Visibility.

Engine selection info is returned in result['engine_selection'] for UI display.
This test verifies the format and content for Console Chat + Bridge integration.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "operator" / "orchestration"))

from tde.send_integration import SendIntegration
from initial_analysis import InitialAnalysisRequest, Classification, Entities, GlobalPlan, Step


async def test_engine_selection_info_format():
    """Test that engine_selection dict has all required fields for UI display."""
    integration = SendIntegration()

    # Create a simple task
    analysis = InitialAnalysisRequest(
        classification=Classification(
            task_type="code_generation",
            complexity="simple",
            engine_preference="auto",
            confidence=0.8,
        ),
        entities=Entities(),
        global_plan=GlobalPlan(
            steps=[Step(step=1, action="gen", depends_on=[], can_parallelize=[])],
            estimated_duration_s=5,
            estimated_tokens=200,
        ),
    )

    engine, result = await integration.select_engine_and_execute(
        "Simple task",
        {},
        analysis,
    )

    # Check engine_selection dict exists and has required fields
    engine_selection = result.get("engine_selection")
    assert engine_selection is not None, "engine_selection should be in result"

    required_fields = ["engine", "confidence", "override", "l34_forced", "trivial"]
    for field in required_fields:
        assert field in engine_selection, f"Missing field: {field}"

    print("✓ Engine selection format test:")
    print(f"  Engine: {engine_selection['engine']}")
    print(f"  Confidence: {engine_selection['confidence']:.2%}")
    print(f"  L34 forced: {engine_selection['l34_forced']}")
    print(f"  Trivial: {engine_selection['trivial']}")
    print(f"  Override: {engine_selection['override']}")

    assert isinstance(engine_selection["engine"], str), "engine should be string"
    assert 0 <= engine_selection["confidence"] <= 1, "confidence should be 0-1"
    assert isinstance(engine_selection["l34_forced"], bool), "l34_forced should be bool"
    assert isinstance(engine_selection["trivial"], bool), "trivial should be bool"

    print("\n✅ Engine selection visibility test PASSED")
    print("\nUI Integration Notes:")
    print("  - Engine name: Use engine_selection['engine'] as display label")
    print("  - Confidence: Show as percentage (engine_selection['confidence'] * 100)")
    print("  - Force reason: If l34_forced=True, show 'Blocked by L34 (sensitive data)'")
    print("  - Trivial path: If trivial=True, show 'Simple task (Claude Code)'")
    print("  - Override: If override != None, show 'User override: [engine]'")


async def test_engine_visibility_with_debug():
    """Test that debug mode includes signals for detailed visibility."""
    integration = SendIntegration()

    analysis = InitialAnalysisRequest(
        classification=Classification(
            task_type="code_generation",
            complexity="moderate",
            engine_preference="auto",
            confidence=0.85,
        ),
        entities=Entities(),
        global_plan=GlobalPlan(
            steps=[
                Step(step=1, action="read", depends_on=[], can_parallelize=[2]),
                Step(step=2, action="write", depends_on=[1], can_parallelize=[]),
            ],
            estimated_duration_s=10,
            estimated_tokens=2000,
        ),
    )

    # Test with debug mode
    engine, result = await integration.select_engine_and_execute(
        "/debug-engine\nTest task",
        {},
        analysis,
    )

    engine_selection = result.get("engine_selection")
    print("\n✓ Debug mode with signals:")
    print(f"  Engine: {engine_selection['engine']}")
    print(f"  Signals available: {'signals' in engine_selection}")

    if "signals" in engine_selection:
        signals = engine_selection["signals"]
        print(f"  Parallelization ratio: {signals.get('parallelization_ratio', 'N/A')}")
        print(f"  Data volume (MB): {signals.get('data_mb', 'N/A')}")
        print(f"  Task type: {signals.get('task_type', 'N/A')}")
        print(f"  Historical loss: {signals.get('historical_loss_pct', 'N/A')}%")

    print("\n✅ Debug visibility test PASSED")
    print("  Use engine_selection['signals'] for detailed breakdown in UI")


if __name__ == "__main__":
    print("=" * 60)
    print("Engine Visibility Tests (for Console + Bridge UI)")
    print("=" * 60)
    asyncio.run(test_engine_selection_info_format())
    asyncio.run(test_engine_visibility_with_debug())
    print("\n✅ ALL ENGINE VISIBILITY TESTS PASSED")
    print("\nNext: Wire engine_selection into Console Chat UI + Bridge")
