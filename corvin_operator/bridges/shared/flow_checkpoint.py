"""flow_checkpoint.py — CorvinFlow M5: Human-Approval Checkpoint.

When a FlowDefinition step declares ``checkpoint: human_approval``, the
FlowRunner calls FlowCheckpoint.pause() before executing that step.  This:
  1. Writes ``mesh_flow.checkpoint_paused`` to the FlowRun manifest.
  2. Creates a sentinel file <run_id>.checkpoint in the checkpoint_dir.
  3. Raises FlowCheckpointPaused so the caller can surface the pause to the user.

The user approves via the /go command (or /flow-approve <run_id>).  The
adapter / dispatcher writes <run_id>.checkpoint.go, which FlowCheckpointStore
detects on resume().  The FlowRunner then continues past the checkpoint.

File paths (mode 0600):
  <corvin_home>/tenants/<tid>/global/flows/checkpoints/<run_id>.checkpoint
  <corvin_home>/tenants/<tid>/global/flows/checkpoints/<run_id>.checkpoint.go
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

_RUN_ID_RE = re.compile(r"^fr_[A-Za-z0-9_\-]{1,60}$")


class FlowCheckpointPaused(Exception):
    """Raised by FlowRunner when a human_approval checkpoint is hit."""

    def __init__(self, run_id: str, step_id: str, checkpoint_dir: Path) -> None:
        self.run_id = run_id
        self.step_id = step_id
        self.checkpoint_dir = checkpoint_dir
        super().__init__(
            f"Flow '{run_id}' paused at checkpoint '{step_id}'. "
            f"Approve with: /go  (or /flow-approve {run_id})"
        )


class FlowCheckpointStore:
    """File-based checkpoint state — pause and approve operations."""

    def __init__(self, checkpoint_dir: Path) -> None:
        self._dir = checkpoint_dir
        checkpoint_dir.mkdir(parents=True, exist_ok=True)

    # ── path helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _validate_run_id(run_id: str) -> None:
        """Guard against path traversal — run_id must match fr_<alnum/dash/underscore>."""
        if not _RUN_ID_RE.fullmatch(run_id):
            raise ValueError(f"Invalid run_id format: {run_id!r}")

    def _pause_path(self, run_id: str) -> Path:
        return self._dir / f"{run_id}.checkpoint"

    def _go_path(self, run_id: str) -> Path:
        return self._dir / f"{run_id}.checkpoint.go"

    # ── public API ────────────────────────────────────────────────────────────

    def pause(self, run_id: str, step_id: str) -> None:
        """Create the pause sentinel file (mode 0600)."""
        self._validate_run_id(run_id)
        p = self._pause_path(run_id)
        p.write_text(json.dumps({"run_id": run_id, "step_id": step_id, "ts": time.time()}) + "\n")
        os.chmod(p, 0o600)

    def approve(self, run_id: str) -> None:
        """Write the approval sentinel file (mode 0600)."""
        self._validate_run_id(run_id)
        p = self._go_path(run_id)
        p.write_text(json.dumps({"run_id": run_id, "approved_at": time.time()}) + "\n")
        os.chmod(p, 0o600)

    def is_approved(self, run_id: str) -> bool:
        """True once /go has been received for this run."""
        self._validate_run_id(run_id)
        return self._go_path(run_id).exists()

    def is_paused(self, run_id: str) -> bool:
        self._validate_run_id(run_id)
        return self._pause_path(run_id).exists() and not self.is_approved(run_id)

    def consume_approval(self, run_id: str) -> None:
        """Remove both sentinel files after a successful resume."""
        self._validate_run_id(run_id)
        self._pause_path(run_id).unlink(missing_ok=True)
        self._go_path(run_id).unlink(missing_ok=True)

    def list_pending(self) -> list[str]:
        """Return run_ids that are paused and waiting for approval."""
        # p.stem of "fr_123.checkpoint" is already "fr_123" (.stem strips last ext).
        # The go-file is "<run_id>.checkpoint.go", not "<run_id>.go".
        return [
            p.stem
            for p in self._dir.glob("*.checkpoint")
            if not (self._dir / f"{p.stem}.checkpoint.go").exists()
        ]

    def wait_for_approval(self, run_id: str, timeout_s: float = 300.0) -> bool:
        """Block until approved or timeout. Returns True if approved."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.is_approved(run_id):
                return True
            time.sleep(0.5)
        return False
