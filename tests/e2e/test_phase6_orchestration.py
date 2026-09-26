"""Phase 6 Milestone 1: Video Producer Orchestration E2E Tests

Five new E2E tests for orchestrator integration.
Ref: ADR-0206 (Phase 6 Milestone 1)

Goal: Validate orchestration baseline (60+ tests target)
"""
import pytest
from core.skills.video_producer.orchestrator import (
    VideoOrchestrator, StoryboardFrame, OrchestrationCommand
)
from datetime import datetime


class TestPhase6Orchestration:
    """Phase 6 Orchestration E2E Tests"""

    @pytest.fixture
    def orchestrator(self):
        """Create orchestrator instance for each test."""
        return VideoOrchestrator("test-project-001")

    def test_phase6_orchestrator_init(self, orchestrator):
        """Test 1: Orchestrator initialization."""
        assert orchestrator.project_id == "test-project-001"
        assert orchestrator.storyboard == []
        assert orchestrator.commands == []
        assert orchestrator.execution_trace == []

    def test_phase6_add_storyboard_frame(self, orchestrator):
        """Test 2: Add storyboard frames (immutable)."""
        frame = StoryboardFrame(
            frame_id="frame-001",
            timestamp=0.0,
            description="Intro title card",
            worker_type="tts",
            worker_input={"text": "Welcome to the video"},
            created_at=datetime.utcnow().isoformat()
        )

        orchestrator.add_frame(frame)
        assert len(orchestrator.storyboard) == 1
        assert orchestrator.storyboard[0].frame_id == "frame-001"

    def test_phase6_build_execution_plan(self, orchestrator):
        """Test 3: Build execution plan from storyboard."""
        # Add multiple frames
        for i in range(3):
            frame = StoryboardFrame(
                frame_id=f"frame-{i:03d}",
                timestamp=float(i),
                description=f"Frame {i}",
                worker_type="tts" if i % 2 == 0 else "screenshot",
                worker_input={"index": i},
                created_at=datetime.utcnow().isoformat()
            )
            orchestrator.add_frame(frame)

        commands = orchestrator.build_execution_plan()

        assert len(commands) == 3
        assert commands[0].execution_order == 0
        assert commands[0].dependencies == []
        assert commands[1].dependencies == ["frame-000"]
        assert commands[2].dependencies == ["frame-000", "frame-001"]

    def test_phase6_invalid_worker_type(self, orchestrator):
        """Test 4: Reject invalid worker types."""
        frame = StoryboardFrame(
            frame_id="frame-bad",
            timestamp=0.0,
            description="Bad worker",
            worker_type="invalid_worker",
            worker_input={},
            created_at=datetime.utcnow().isoformat()
        )
        orchestrator.add_frame(frame)

        with pytest.raises(ValueError, match="Invalid worker type"):
            orchestrator.build_execution_plan()

    def test_phase6_execute_frame_and_audit(self, orchestrator):
        """Test 5: Execute frame with audit trace."""
        frame = StoryboardFrame(
            frame_id="frame-exec-001",
            timestamp=0.0,
            description="Test execution",
            worker_type="tts",
            worker_input={"text": "Test audio"},
            created_at=datetime.utcnow().isoformat()
        )
        orchestrator.add_frame(frame)
        commands = orchestrator.build_execution_plan()

        # Execute first command
        result = orchestrator.execute_frame(commands[0])

        assert result["status"] == "completed"
        assert result["frame_id"] == "frame-exec-001"
        assert len(orchestrator.execution_trace) > 0

        # Verify audit events
        events = [e for e in orchestrator.execution_trace if e["event"] == "frame_execution_completed"]
        assert len(events) >= 1

    def test_phase6_orchestration_summary(self, orchestrator):
        """Test 6: Orchestration summary with execution hash."""
        # Setup
        for i in range(2):
            frame = StoryboardFrame(
                frame_id=f"summary-{i}",
                timestamp=float(i),
                description=f"Frame {i}",
                worker_type="screenshot",
                worker_input={},
                created_at=datetime.utcnow().isoformat()
            )
            orchestrator.add_frame(frame)

        orchestrator.build_execution_plan()
        summary = orchestrator.summary()

        assert summary["project_id"] == "test-project-001"
        assert summary["frame_count"] == 2
        assert summary["command_count"] == 2
        assert len(summary["execution_hash"]) == 64  # SHA256 hex

    def test_phase6_storyboard_immutability(self, orchestrator):
        """Test 7: Verify StoryboardFrame immutability."""
        frame = StoryboardFrame(
            frame_id="immutable-frame",
            timestamp=0.0,
            description="Test immutable",
            worker_type="tts",
            worker_input={},
            created_at=datetime.utcnow().isoformat()
        )

        # Should raise AttributeError on modification attempt
        with pytest.raises(AttributeError):
            frame.frame_id = "modified"

    def test_phase6_worker_types_validation(self, orchestrator):
        """Test 8: All valid worker types are accepted."""
        valid_workers = ["tts", "screenshot", "ffmpeg", "youtube"]

        for i, worker_type in enumerate(valid_workers):
            frame = StoryboardFrame(
                frame_id=f"worker-{worker_type}",
                timestamp=float(i),
                description=f"Worker type: {worker_type}",
                worker_type=worker_type,
                worker_input={},
                created_at=datetime.utcnow().isoformat()
            )
            orchestrator.add_frame(frame)

        commands = orchestrator.build_execution_plan()
        assert len(commands) == len(valid_workers)

    def test_phase6_execution_with_dependencies(self, orchestrator):
        """Test 9: Execute with dependency order."""
        # Create a chain of frames
        frames = [
            StoryboardFrame(
                frame_id=f"dep-{i}",
                timestamp=float(i),
                description=f"Dependent frame {i}",
                worker_type="tts",
                worker_input={"index": i},
                created_at=datetime.utcnow().isoformat()
            )
            for i in range(3)
        ]

        for frame in frames:
            orchestrator.add_frame(frame)

        commands = orchestrator.build_execution_plan()

        # Execute in order
        results = []
        for cmd in commands:
            result = orchestrator.execute_frame(cmd)
            results.append(result)
            assert result["status"] == "completed"

        assert len(results) == 3

    def test_phase6_orchestration_empty_project(self, orchestrator):
        """Test 10: Handle empty orchestration gracefully."""
        commands = orchestrator.build_execution_plan()
        assert commands == []

        summary = orchestrator.summary()
        assert summary["frame_count"] == 0
        assert summary["command_count"] == 0


# Phase 6 Milestone 1 Test Summary
# ================================
# ✅ Test 1: Initialization
# ✅ Test 2: Add frames
# ✅ Test 3: Build execution plan
# ✅ Test 4: Invalid worker validation
# ✅ Test 5: Frame execution + audit
# ✅ Test 6: Orchestration summary (NEW)
# ✅ Test 7: Immutability (NEW)
# ✅ Test 8: Worker type validation (NEW)
# ✅ Test 9: Dependency execution (NEW)
# ✅ Test 10: Empty project handling (NEW)
#
# Total: 10 tests (5 new for Phase 6 orchestration)
# Gate: 60+ baseline tests expected from Phase 5 + 10 new = 65+ tests
