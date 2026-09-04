"""Phase 4, Layer 3: Rare-Block Preservation (frequency < 1/400 immune)."""

from typing import Dict


class FrequencyDatabase:
    """Track block frequency to preserve rare blocks (fail-closed)."""

    RARE_THRESHOLD = 1.0 / 400.0  # 1/400, conservative

    def __init__(self):
        self._frequencies: Dict[str, float] = {}
        self._total_tasks = 0

    def get_frequency(self, block_hash: str) -> float:
        """Get frequency: count / total (0.0–1.0)."""
        return self._frequencies.get(block_hash, 0.0)

    def is_rare(self, block_hash: str) -> bool:
        """Blocks with frequency < 1/400 are rare (immune to dedup)."""
        return self.get_frequency(block_hash) <= self.RARE_THRESHOLD

    def record_block(self, block_hash: str) -> None:
        """Record block in this task."""
        self._frequencies[block_hash] = self._frequencies.get(block_hash, 0.0) + 1.0

    def finalize_task(self) -> None:
        """Call after task complete to normalize frequencies."""
        self._total_tasks += 1
        if self._total_tasks > 0:
            for k in self._frequencies:
                self._frequencies[k] /= self._total_tasks

    def reset(self) -> None:
        """Reset database."""
        self._frequencies.clear()
        self._total_tasks = 0
