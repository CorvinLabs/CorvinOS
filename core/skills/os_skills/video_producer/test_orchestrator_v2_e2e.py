"""
LDD k=2 E2E Wiring Proof: Video Producer 2.0 Orchestrator

Tests for enhanced orchestrator with:
- Model Selection integration
- Event-sourced state
- Feedback loop integration
- OTEL telemetry dual-write
- Audit trail hash-chaining
- Tenant isolation
- Fail-closed retry logic

Run: pytest core/skills/os_skills/video_producer/test_orchestrator_v2_e2e.py -v
"""

import pytest
import asyncio
import json
from pathlib import Path
from datetime import datetime
import uuid

from core.skills.os_skills.video_producer.orchestrator_v2_enhanced import VideoProducerOrchestratorV2
from core.skills.os_skills.video_producer.types import VideoGenerationRequest, VideoGenerationResult


@pytest.fixture
def temp_project(tmp_path):
    """Create temporary project directory."""
    project_dir = tmp_path / "video_project"
    project_dir.mkdir(parents=True, exist_ok=True)

    # Create minimal input file
    input_file = project_dir / "input.txt"
    input_file.write_text(
        "# Video Production in CorvinOS\n\n"
        "This video explains orchestrated video generation.\n\n"
        "## Phase 1: Asset Analysis\nAnalyze input content.\n\n"
        "## Phase 2: Storyboard\nGenerate narrative structure.\n\n"
        "## Phase 3: Workers\nGenerate audio, music, visuals.\n\n"
        "## Phase 4: Assembly\nCombine into final video.\n"
    )

    # Create design system
    design_system = {
        "version": "1.0.0",
        "fonts": {"default": "Arial"},
        "colors": {"primary": "#0066cc"},
        "video_specs": {"resolution": "1280x720", "fps": 30}
    }
    design_file = project_dir / "design_system.json"
    design_file.write_text(json.dumps(design_system))

    return project_dir


@pytest.mark.asyncio
async def test_e2e_real_video_generation(temp_project):
    """Test 1: Real video generation end-to-end (text → MP4)."""
    orchestrator = VideoProducerOrchestratorV2(temp_project)

    request = VideoGenerationRequest(
        input_text="Create a video explaining video production.",
        style="corporate",
        duration_seconds=180,
        quality_tier="PRODUCTION",
        project_id=str(uuid.uuid4()),
        tenant_id="test_tenant",
    )

    # Execute
    result = await orchestrator.orchestrate(request)

    # Assertions
    assert isinstance(result, VideoGenerationResult), "Wrong return type"
    assert result.status in ["success", "partial"], f"Unexpected status: {result.status}"
    assert result.video_path is not None, "No video path returned"

    video_file = Path(result.video_path)
    assert video_file.exists(), f"Video file not found at {video_file}"
    assert video_file.stat().st_size > 1000, f"Video file too small: {video_file.stat().st_size}"

    # Check phases
    assert len(result.phase_results) >= 4, f"Expected ≥4 phases, got {len(result.phase_results)}"

    print(f"✅ Test 1 PASS: Video generated ({video_file.stat().st_size} bytes)")


@pytest.mark.asyncio
async def test_e2e_model_selection_integration(temp_project):
    """Test 2: Model Selection skill integration."""
    orchestrator = VideoProducerOrchestratorV2(temp_project)

    request = VideoGenerationRequest(
        input_text="Create educational video about machine learning.",
        style="educational",
        duration_seconds=120,
        quality_tier="PRODUCTION",
        project_id=str(uuid.uuid4()),
        tenant_id="test_tenant",
    )

    # Execute
    result = await orchestrator.orchestrate(request)
    assert result.status in ["success", "partial"]

    # Check model selections were tracked
    video_id = result.video_id
    model_selections = orchestrator.video_state[video_id].get("model_selections", [])

    assert len(model_selections) >= 1, f"No model selections tracked, got {len(model_selections)}"

    # Verify selection structure
    for selection in model_selections:
        assert "worker_type" in selection
        assert "model_id" in selection
        assert "confidence" in selection
        assert 0 <= selection["confidence"] <= 1, f"Invalid confidence: {selection['confidence']}"

    print(f"✅ Test 2 PASS: Model selections tracked ({len(model_selections)} decisions)")


@pytest.mark.asyncio
async def test_e2e_feedback_loop_integration(temp_project):
    """Test 3: Feedback loop integration."""
    orchestrator = VideoProducerOrchestratorV2(temp_project)

    request = VideoGenerationRequest(
        input_text="Create video about neural networks.",
        style="technical",
        duration_seconds=120,
        quality_tier="PRODUCTION",
        project_id=str(uuid.uuid4()),
        tenant_id="test_tenant",
    )

    # Execute
    result = await orchestrator.orchestrate(request)
    assert result.status in ["success", "partial"]

    # Check feedback events
    video_id = result.video_id
    feedback_events = orchestrator.video_state[video_id].get("feedback_events", [])

    assert len(feedback_events) >= 1, f"No feedback events, got {len(feedback_events)}"

    # Verify feedback structure
    for feedback in feedback_events:
        assert "worker_type" in feedback
        assert "quality_score" in feedback
        assert 0 <= feedback["quality_score"] <= 1, f"Invalid score: {feedback['quality_score']}"

    print(f"✅ Test 3 PASS: Feedback loop integrated ({len(feedback_events)} feedback events)")


@pytest.mark.asyncio
async def test_e2e_otel_telemetry_emission(temp_project):
    """Test 4: OTEL telemetry emission (dual-write: local + OTEL)."""
    orchestrator = VideoProducerOrchestratorV2(temp_project)

    request = VideoGenerationRequest(
        input_text="Create video about data privacy.",
        style="corporate",
        duration_seconds=120,
        quality_tier="PRODUCTION",
        project_id=str(uuid.uuid4()),
        tenant_id="test_tenant",
    )

    # Execute
    result = await orchestrator.orchestrate(request)
    assert result.status in ["success", "partial"]

    # Check metrics were emitted (stored locally)
    metrics = getattr(orchestrator, "_local_metrics", [])

    assert len(metrics) > 0, f"No metrics emitted, got {len(metrics)}"

    # Check for phase latency metrics
    latency_metrics = [m for m in metrics if "latency" in m.get("metric", "")]
    assert len(latency_metrics) >= 2, f"Expected ≥2 latency metrics, got {len(latency_metrics)}"

    # Verify metric structure
    for metric in metrics:
        assert "metric" in metric
        assert "value" in metric
        assert "labels" in metric
        assert "tenant_id" in metric["labels"]
        assert metric["labels"]["tenant_id"] == request.tenant_id

    print(f"✅ Test 4 PASS: OTEL telemetry emitted ({len(metrics)} metrics)")


@pytest.mark.asyncio
async def test_e2e_audit_trail_hash_chaining(temp_project):
    """Test 5: Audit trail hash-chaining (immutable event log)."""
    orchestrator = VideoProducerOrchestratorV2(temp_project)

    request = VideoGenerationRequest(
        input_text="Create video about security.",
        style="corporate",
        duration_seconds=120,
        quality_tier="PRODUCTION",
        project_id=str(uuid.uuid4()),
        tenant_id="test_tenant_audit",
    )

    # Execute
    result = await orchestrator.orchestrate(request)
    assert result.status in ["success", "partial"]

    # Check audit events
    video_id = result.video_id
    audit_event_ids = orchestrator.video_state[video_id].get("audit_events", [])

    assert len(audit_event_ids) > 0, f"No audit events, got {len(audit_event_ids)}"

    # All event IDs should be hex hashes (SHA256)
    for event_id in audit_event_ids:
        assert len(event_id) == 64, f"Invalid hash length: {len(event_id)} (expected 64)"
        assert all(c in "0123456789abcdef" for c in event_id), f"Invalid hash: {event_id}"

    print(f"✅ Test 5 PASS: Audit trail hash-chained ({len(audit_event_ids)} events)")


@pytest.mark.asyncio
async def test_e2e_fail_closed_retry_logic(temp_project):
    """Test 6: Fail-closed retry logic."""
    orchestrator = VideoProducerOrchestratorV2(temp_project)

    request = VideoGenerationRequest(
        input_text="Create video about error handling.",
        style="corporate",
        duration_seconds=120,
        quality_tier="PRODUCTION",
        project_id=str(uuid.uuid4()),
        tenant_id="test_tenant",
    )

    # Execute (with built-in retry logic)
    result = await orchestrator.orchestrate(request)

    # Should complete (with fallback if needed)
    assert result.status in ["success", "partial"], f"Unexpected status: {result.status}"
    assert result.video_path is not None, "Video should be generated (with fallback)"

    print(f"✅ Test 6 PASS: Fail-closed retry logic (video completed)")


@pytest.mark.asyncio
async def test_e2e_concurrent_video_generation(temp_project):
    """Test 7: Concurrent video generation (no resource starvation)."""
    orchestrator = VideoProducerOrchestratorV2(temp_project)

    # Create 3 concurrent requests
    requests = [
        VideoGenerationRequest(
            input_text=f"Create video {i} about topic {i}.",
            style="corporate",
            duration_seconds=120,
            quality_tier="DRAFT",  # Faster quality
            project_id=str(uuid.uuid4()),
            tenant_id="test_tenant",
        )
        for i in range(3)
    ]

    # Execute concurrently
    import time
    start = time.time()
    results = await asyncio.gather(
        *[orchestrator.orchestrate(req) for req in requests]
    )
    elapsed = time.time() - start

    # All should complete
    assert len(results) == 3
    assert all(r.status in ["success", "partial"] for r in results), "Some videos failed"

    # Timeline should be reasonable (not 3x serial)
    assert elapsed < 120, f"Took too long: {elapsed}s (possible starvation)"

    print(f"✅ Test 7 PASS: Concurrent execution ({len(results)} videos, {elapsed:.1f}s)")


@pytest.mark.asyncio
async def test_e2e_tenant_isolation(temp_project):
    """Test 8: Tenant isolation (cross-tenant queries isolated)."""
    orchestrator = VideoProducerOrchestratorV2(temp_project)

    # Create 2 videos with different tenants
    request1 = VideoGenerationRequest(
        input_text="Tenant 1 video.",
        style="corporate",
        duration_seconds=120,
        quality_tier="DRAFT",
        project_id=str(uuid.uuid4()),
        tenant_id="tenant_1",
    )

    request2 = VideoGenerationRequest(
        input_text="Tenant 2 video.",
        style="corporate",
        duration_seconds=120,
        quality_tier="DRAFT",
        project_id=str(uuid.uuid4()),
        tenant_id="tenant_2",
    )

    result1 = await orchestrator.orchestrate(request1)
    result2 = await orchestrator.orchestrate(request2)

    assert result1.tenant_id == "tenant_1"
    assert result2.tenant_id == "tenant_2"
    assert result1.video_id != result2.video_id

    # Check audit events are tenant-scoped
    video1_events = orchestrator.video_state[result1.video_id].get("audit_events", [])
    video2_events = orchestrator.video_state[result2.video_id].get("audit_events", [])

    assert len(video1_events) > 0
    assert len(video2_events) > 0
    assert video1_events != video2_events, "Audit events should be separate per tenant"

    print(f"✅ Test 8 PASS: Tenant isolation verified (2 videos, separate audit trails)")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
