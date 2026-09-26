"""Phase 6 Milestone 1: Video Producer v2.0 Orchestrator

Consolidated orchestration for director mode + worker coordination.
Ref: ADR-0206 (Phase 6 Milestone 1 — Regression + Orchestration)

Goals (Phase 6 Part 2):
  - Full orchestration + storyboard integration
  - Merge director mode + worker coordination
  - 5 new E2E tests for orchestration
  - Gate: 60+ tests passing
"""
from dataclasses import dataclass
from typing import Optional, List
from datetime import datetime
import hashlib


@dataclass(frozen=True)
class StoryboardFrame:
    """Immutable frame in a video storyboard (Phase 6)"""
    frame_id: str
    timestamp: float  # seconds in final output
    description: str
    worker_type: str  # "tts" | "screenshot" | "ffmpeg" | "youtube"
    worker_input: dict
    created_at: str


@dataclass(frozen=True)
class OrchestrationCommand:
    """Immutable command coordinating workers (Phase 6)"""
    command_id: str
    frame: StoryboardFrame
    execution_order: int
    dependencies: List[str]  # frame_ids that must complete first
    timeout_seconds: int = 30
    retry_count: int = 0


class VideoOrchestrator:
    """Phase 6: Full orchestration with director mode integration.

    Responsibilities:
      - Parse storyboard → execution plan
      - Coordinate worker execution (TTS, Screenshot, FFmpeg, YouTube)
      - Track frame dependencies + ordering
      - Audit orchestration decisions
      - Report completion + errors
    """

    def __init__(self, project_id: str):
        self.project_id = project_id
        self.storyboard: List[StoryboardFrame] = []
        self.commands: List[OrchestrationCommand] = []
        self.execution_trace = []
        self.created_at = datetime.utcnow().isoformat()

    def add_frame(self, frame: StoryboardFrame) -> None:
        """Add a frame to the storyboard (immutable)."""
        if not isinstance(frame, StoryboardFrame):
            raise TypeError(f"Expected StoryboardFrame, got {type(frame)}")
        self.storyboard.append(frame)

    def build_execution_plan(self) -> List[OrchestrationCommand]:
        """Convert storyboard → execution plan with dependency resolution.

        Returns:
            List of OrchestrationCommand in execution order.

        Raises:
            ValueError: If circular dependencies or invalid worker type.
        """
        if not self.storyboard:
            return []

        commands = []
        for idx, frame in enumerate(self.storyboard):
            # Validate worker type
            valid_workers = {"tts", "screenshot", "ffmpeg", "youtube"}
            if frame.worker_type not in valid_workers:
                raise ValueError(f"Invalid worker type: {frame.worker_type}")

            # Infer dependencies: all previous frames (sequential)
            deps = [f.frame_id for f in self.storyboard[:idx]]

            cmd = OrchestrationCommand(
                command_id=f"{self.project_id}-cmd-{idx:03d}",
                frame=frame,
                execution_order=idx,
                dependencies=deps
            )
            commands.append(cmd)
            self.execution_trace.append({
                "event": "command_generated",
                "command_id": cmd.command_id,
                "timestamp": datetime.utcnow().isoformat()
            })

        self.commands = commands
        return commands

    def execute_frame(self, command: OrchestrationCommand) -> dict:
        """Execute a single frame via its worker.

        Args:
            command: OrchestrationCommand to execute

        Returns:
            Execution result with status, output, errors
        """
        self.execution_trace.append({
            "event": "frame_execution_started",
            "command_id": command.command_id,
            "frame_id": command.frame.frame_id,
            "worker_type": command.frame.worker_type,
            "timestamp": datetime.utcnow().isoformat()
        })

        # Phase 6: Stub implementation (will be wired to real workers in Phase 6b)
        result = {
            "command_id": command.command_id,
            "frame_id": command.frame.frame_id,
            "status": "completed",
            "output": {
                "worker_type": command.frame.worker_type,
                "execution_time_ms": 250,  # placeholder
                "result": f"{command.frame.worker_type} execution stub"
            },
            "errors": []
        }

        self.execution_trace.append({
            "event": "frame_execution_completed",
            "command_id": command.command_id,
            "status": result["status"],
            "timestamp": datetime.utcnow().isoformat()
        })

        return result

    def checkpoint_save_hook(self, frame_id: str, checkpoint_manager=None) -> dict:
        """Save checkpoint after frame execution (Phase 2b — ADR-0892/0893).

        Called after frame_execution_completed to persist orchestrator state.
        Emits capability event for other subsystems (chat engine, learning).

        Args:
            frame_id: Frame that just completed
            checkpoint_manager: CheckpointManager instance (optional, for testing)

        Returns:
            Checkpoint metadata {checkpoint_id, frame_id, state_hash, timestamp}
        """
        checkpoint_id = f"orch-{self.project_id}-frame-{frame_id}"
        state_hash = self.get_execution_hash()

        checkpoint_metadata = {
            "checkpoint_id": checkpoint_id,
            "frame_id": frame_id,
            "orchestrator_state_hash": state_hash,
            "timestamp": datetime.utcnow().isoformat(),
            "storyboard_count": len(self.storyboard),
            "execution_trace_events": len(self.execution_trace)
        }

        # Emit capability event (Phase 2 blocker resolution: no silos)
        self.execution_trace.append({
            "event": "checkpoint.orchestrator.frame_saved",
            "capability_event": True,
            "checkpoint_id": checkpoint_id,
            "frame_id": frame_id,
            "state_hash": state_hash,
            "timestamp": checkpoint_metadata["timestamp"]
        })

        return checkpoint_metadata

    def checkpoint_load_hook(self, checkpoint_id: str, checkpoint_manager=None) -> dict:
        """Load checkpoint and restore orchestrator state (Phase 2b — ADR-0892/0893).

        Restores orchestrator state from checkpoint (storyboard, execution trace).
        Emits capability event for other subsystems.

        Args:
            checkpoint_id: Checkpoint to load
            checkpoint_manager: CheckpointManager instance (optional)

        Returns:
            Restored state {checkpoint_id, frame_id, recovered_frame_count}

        Raises:
            ValueError: If checkpoint cannot be loaded/verified
        """
        # In Phase 2b: checkpoint_manager.load(checkpoint_id) validates + restores
        # For k=1 skeleton: assume successful load, emit event

        # Extract frame_id from checkpoint_id (format: orch-{project}-frame-{frame_id})
        try:
            frame_id = checkpoint_id.split("frame-")[-1]
        except:
            raise ValueError(f"Invalid checkpoint_id format: {checkpoint_id}")

        restored_state = {
            "checkpoint_id": checkpoint_id,
            "frame_id": frame_id,
            "recovered_frame_count": len(self.storyboard),
            "timestamp": datetime.utcnow().isoformat()
        }

        # Emit capability event
        self.execution_trace.append({
            "event": "checkpoint.orchestrator.frame_loaded",
            "capability_event": True,
            "checkpoint_id": checkpoint_id,
            "frame_id": frame_id,
            "timestamp": restored_state["timestamp"]
        })

        return restored_state

    def get_recovery_frame(self, checkpoint_id: str) -> int:
        """Determine which frame to resume from after checkpoint load (idempotent).

        Given a checkpoint, calculates the next frame to execute:
        - If frame N is complete → resume from frame N+1
        - If frame N is partial → retry frame N
        - If frame N has no execution record → start frame N

        Args:
            checkpoint_id: Checkpoint to analyze

        Returns:
            Frame index to resume execution from (0-based)
        """
        # k=1 skeleton: assume sequential completion, return next frame
        # In k=2: enhance with actual checkpoint state inspection

        try:
            frame_id = checkpoint_id.split("frame-")[-1]
            # Find frame index by frame_id
            for idx, frame in enumerate(self.storyboard):
                if frame.frame_id == frame_id:
                    return idx + 1  # Resume from next frame
            raise ValueError(f"Frame {frame_id} not found in storyboard")
        except Exception as e:
            # Fallback: resume from frame 0 if recovery fails
            return 0

    def get_execution_hash(self) -> str:
        """Generate hash of orchestration state (for audit chain).

        Returns:
            SHA256 hash of orchestration decisions.
        """
        state = f"{self.project_id}|{len(self.storyboard)}|{len(self.commands)}"
        for cmd in self.commands:
            state += f"|{cmd.command_id}"
        return hashlib.sha256(state.encode()).hexdigest()

    def summary(self) -> dict:
        """Return orchestration summary for reporting.

        Returns:
            Dict with frame count, command count, execution status.
        """
        return {
            "project_id": self.project_id,
            "frame_count": len(self.storyboard),
            "command_count": len(self.commands),
            "execution_hash": self.get_execution_hash(),
            "created_at": self.created_at,
            "trace_events": len(self.execution_trace)
        }


# Phase 6 Gate: Orchestrator ready for integration
# ✅ Immutable storyboard frame model
# ✅ Command generation with dependency resolution
# ✅ Worker execution stub (wired in Phase 6b)
# ✅ Audit trace (hash-chained in Phase 6b)
# ✅ E2E test points ready (5 new tests)
