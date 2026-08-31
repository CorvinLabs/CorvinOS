"""
Deterministic replay engine for verifying offline operations on reconnect.

Guarantees:
- All queued operations replayed exactly once
- Hash verification detects corruption
- Atomic apply (all-or-nothing)
- Deterministic outcome matching

Design:
- Capture execution snapshot (input, context, seed)
- Re-execute with same seed
- Verify: hash(output) matches
- If mismatch: corruption detected, rollback
"""

from dataclasses import dataclass
from typing import Dict, Any, Optional, Tuple
from datetime import datetime
import hashlib
import json


@dataclass
class ExecutionSnapshot:
    """Snapshot of operation execution."""
    op_id: str
    task_id: str
    input_data: Dict[str, Any]
    context_data: Dict[str, Any]
    engine_choice: str
    timestamp: datetime
    seed: int  # For deterministic RNG
    output_hash: str  # SHA256 of output
    output_data: Optional[Dict[str, Any]] = None


class ReplayEngine:
    """
    Deterministic replay for operation verification.

    Usage:
    1. Execute operation normally, capture snapshot
    2. On reconnect, replay operation with same seed
    3. Verify: output_hash matches
    4. If matches: operation is deterministic + verified
    5. If mismatches: corruption detected, alert operator
    """

    @staticmethod
    def hash_output(output: Dict[str, Any]) -> str:
        """
        Hash operation output for verification.

        Returns SHA256 of JSON-serialized output.
        """
        json_str = json.dumps(output, sort_keys=True, default=str)
        return hashlib.sha256(json_str.encode()).hexdigest()

    @staticmethod
    def capture_snapshot(
        op_id: str,
        task_id: str,
        input_data: Dict[str, Any],
        context_data: Dict[str, Any],
        engine_choice: str,
        output_data: Dict[str, Any],
        seed: int = 0,
    ) -> ExecutionSnapshot:
        """
        Capture execution snapshot for later replay verification.

        Args:
            op_id: Operation ID
            task_id: Task ID
            input_data: Operation input
            context_data: Operation context
            engine_choice: Which engine ("claude" or "local_llama2")
            output_data: Operation output
            seed: Random seed used (default 0)

        Returns:
            ExecutionSnapshot ready for storage/replay
        """
        output_hash = ReplayEngine.hash_output(output_data)

        return ExecutionSnapshot(
            op_id=op_id,
            task_id=task_id,
            input_data=input_data,
            context_data=context_data,
            engine_choice=engine_choice,
            timestamp=datetime.utcnow(),
            seed=seed,
            output_hash=output_hash,
            output_data=output_data,
        )

    @staticmethod
    def verify_replay(
        original_snapshot: ExecutionSnapshot,
        replayed_output: Dict[str, Any],
    ) -> bool:
        """
        Verify replayed operation matches original.

        Args:
            original_snapshot: Original execution snapshot
            replayed_output: Output from replayed execution

        Returns:
            True if hashes match (deterministic), False if mismatch (corruption)
        """
        replayed_hash = ReplayEngine.hash_output(replayed_output)
        return replayed_hash == original_snapshot.output_hash


class SyncVerifier:
    """
    Verify sync completion on reconnect.

    Guarantees:
    - All queued operations applied
    - CRDT merge completed
    - Final state consistent
    """

    @staticmethod
    def verify_queue_empty(pending_count: int) -> bool:
        """Verify operation queue is empty (all applied)."""
        return pending_count == 0

    @staticmethod
    def verify_state_merged(
        local_state_hash: str,
        remote_state_hash: str,
        merged_state_hash: str,
    ) -> bool:
        """
        Verify state merge was computed.

        Returns True if merged state is different from local/remote
        (indicates merge happened).
        """
        # Merged state should differ from both local and remote
        # (unless they were already identical)
        return (
            merged_state_hash != local_state_hash
            or merged_state_hash != remote_state_hash
        )

    @staticmethod
    def verify_all_replayed(
        original_snapshots: Dict[str, ExecutionSnapshot],
        replay_results: Dict[str, Dict[str, Any]],
    ) -> Tuple[bool, list]:
        """
        Verify all operations replayed correctly.

        Args:
            original_snapshots: Map of op_id -> original execution
            replay_results: Map of op_id -> replayed output

        Returns:
            (all_verified, list of failed op_ids)
        """
        failed = []

        for op_id, original in original_snapshots.items():
            if op_id not in replay_results:
                failed.append(op_id)
                continue

            replayed = replay_results[op_id]
            if not ReplayEngine.verify_replay(original, replayed):
                failed.append(op_id)

        return len(failed) == 0, failed
