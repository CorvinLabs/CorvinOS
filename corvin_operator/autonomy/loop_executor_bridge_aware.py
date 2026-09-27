"""
Autonome Loop Executor — Bridge-aware execution for /loop command.

Adapts loop behavior to the current bridge:
- Interactive (CLI/Web): use ScheduleWakeup for periodic reschedule
- Non-Interactive (Discord/Slack): run as background task (no reschedule)

Part of ADR-2083: Non-Interactive Bridge Autonomy Design.
"""

import time
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable, Any

from corvin_operator.bridges.autonomy_detector import (
    detect_autonomy_mode,
    BridgeAutonomyMode,
    get_bridge_type,
    is_rescheduling_available,
)


logger = logging.getLogger(__name__)


class LoopExecutionMode(Enum):
    """How the loop will be executed."""
    SCHEDULED = "scheduled"       # Interactive: ScheduleWakeup reschedule
    BACKGROUND = "background"     # Non-interactive: run-to-completion
    ONE_SHOT = "one_shot"         # No loop (testing)


@dataclass
class LoopConfig:
    """Configuration for autonome loop execution."""
    prompt: str                    # The loop prompt/instruction
    interval_seconds: int          # Delay between iterations (default 300)
    max_iterations: Optional[int]  # Max iterations before stopping (None = infinite)
    timeout_seconds: int           # Absolute timeout for background loops (safety)
    executor_fn: Callable          # Function to execute each iteration
    on_iteration_complete: Optional[Callable] = None  # Callback after each iteration
    on_loop_complete: Optional[Callable] = None       # Callback when loop finishes


class LoopExecutor:
    """
    Execute autonome loops, adapting to bridge type.

    **Contract:**
    - Interactive bridge: Returns immediately after scheduling; loop runs across multiple sessions
    - Non-interactive bridge: Runs in background, returns when timeout or iterations exhausted
    - All execution is audit-logged (autonomy_mode, bridge_type, iteration_count, reason_complete)
    """

    def __init__(self, config: LoopConfig):
        self.config = config
        self.execution_mode: Optional[LoopExecutionMode] = None
        self.iteration_count = 0
        self.start_time: Optional[float] = None
        self.audit_trail = []

    def run(self) -> dict:
        """
        Execute the loop, adapting to autonomy mode.

        Returns:
            dict with keys:
            - execution_mode: "scheduled" | "background" | "one_shot"
            - bridge_type: "discord" | "cli" | "web" | ...
            - iterations: number of iterations completed
            - reason_complete: "scheduled" | "timeout" | "iterations_exhausted" | "error"
            - duration_seconds: total elapsed time
        """
        self.start_time = time.time()
        autonomy_mode = detect_autonomy_mode()

        if autonomy_mode == BridgeAutonomyMode.INTERACTIVE:
            self.execution_mode = LoopExecutionMode.SCHEDULED
            return self._run_scheduled()
        else:
            # Non-interactive or headless → background execution
            self.execution_mode = LoopExecutionMode.BACKGROUND
            return self._run_background()

    def _run_scheduled(self) -> dict:
        """
        Interactive mode: schedule the loop for periodic execution.
        Returns immediately with scheduling confirmation.
        """
        # In interactive mode, the ScheduleWakeup tool will handle reschedule
        # This agent just logs the scheduling intent
        logger.info(f"Scheduling loop: prompt={self.config.prompt[:50]}..., interval={self.config.interval_seconds}s")

        self.audit_trail.append({
            "event": "loop_scheduled",
            "bridge": get_bridge_type().value,
            "autonomy_mode": "scheduled",
            "interval_seconds": self.config.interval_seconds,
        })

        return {
            "execution_mode": "scheduled",
            "bridge_type": get_bridge_type().value,
            "iterations": 0,
            "reason_complete": "scheduled",
            "duration_seconds": 0,
            "message": "Loop scheduled for periodic execution (ScheduleWakeup active)",
        }

    def _run_background(self) -> dict:
        """
        Non-interactive mode: run the loop in background until timeout or completion.
        Blocks until timeout or iterations exhausted.
        """
        logger.info(
            f"Running loop in background: "
            f"prompt={self.config.prompt[:50]}..., "
            f"interval={self.config.interval_seconds}s, "
            f"timeout={self.config.timeout_seconds}s"
        )

        reason_complete = "unknown"
        iteration_count = 0

        try:
            while True:
                # Check timeout
                elapsed = time.time() - self.start_time
                if elapsed > self.config.timeout_seconds:
                    reason_complete = "timeout"
                    logger.warning(f"Loop timeout reached ({self.config.timeout_seconds}s)")
                    break

                # Check iteration limit
                if self.config.max_iterations and iteration_count >= self.config.max_iterations:
                    reason_complete = "iterations_exhausted"
                    logger.info(f"Loop completed: {iteration_count} iterations")
                    break

                # Execute one iteration
                try:
                    logger.debug(f"Loop iteration {iteration_count + 1}")
                    result = self.config.executor_fn(self.config.prompt)
                    iteration_count += 1

                    # Call callback if provided
                    if self.config.on_iteration_complete:
                        self.config.on_iteration_complete(result)

                    # Audit log
                    self.audit_trail.append({
                        "event": "loop_iteration_complete",
                        "iteration": iteration_count,
                        "elapsed_seconds": elapsed,
                        "result_ok": result.get("success", True),
                    })

                except Exception as e:
                    logger.error(f"Loop iteration failed: {e}")
                    reason_complete = "error"
                    break

                # Sleep before next iteration
                time.sleep(self.config.interval_seconds)

        finally:
            # Call completion callback
            if self.config.on_loop_complete:
                self.config.on_loop_complete({
                    "iterations": iteration_count,
                    "reason": reason_complete,
                })

        elapsed = time.time() - self.start_time

        self.audit_trail.append({
            "event": "loop_complete",
            "bridge": get_bridge_type().value,
            "autonomy_mode": "background",
            "iterations": iteration_count,
            "reason": reason_complete,
            "duration_seconds": elapsed,
        })

        return {
            "execution_mode": "background",
            "bridge_type": get_bridge_type().value,
            "iterations": iteration_count,
            "reason_complete": reason_complete,
            "duration_seconds": elapsed,
            "message": f"Loop completed in background after {iteration_count} iterations ({elapsed:.1f}s)",
        }

    def get_audit_trail(self) -> list:
        """Return audit events for this loop execution."""
        return self.audit_trail.copy()
