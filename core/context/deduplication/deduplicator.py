"""Phase 3: Exact-Match Deduplication (Layer 1, always safe)."""

import hashlib
import json
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class DedupeResult:
    """Result of deduplication."""
    blocks_kept: tuple
    blocks_removed: tuple
    completeness_checksum: str
    token_savings_estimated: int = 0


def deduplicate_exact(blocks: list) -> DedupeResult:
    """Remove bitwise-identical blocks (fail-closed, always safe)."""
    seen = {}
    kept = []
    removed = []

    for block in blocks:
        # Compute hash of block content
        if isinstance(block, dict):
            block_content = json.dumps(block, sort_keys=True).encode('utf-8')
        else:
            block_content = str(block).encode('utf-8')

        block_hash = hashlib.sha256(block_content).hexdigest()

        if block_hash not in seen:
            seen[block_hash] = True
            kept.append(block)
        else:
            removed.append(block_hash)

    # Compute completeness checksum (proof-of-what-was-kept)
    kept_hashes = [hashlib.sha256(
        json.dumps(b, sort_keys=True).encode('utf-8') if isinstance(b, dict)
        else str(b).encode('utf-8')
    ).hexdigest() for b in kept]
    completeness_checksum = hashlib.sha256(
        '\n'.join(kept_hashes).encode('utf-8')
    ).hexdigest()

    return DedupeResult(
        blocks_kept=tuple(kept),
        blocks_removed=tuple(removed),
        completeness_checksum=completeness_checksum,
        token_savings_estimated=len(removed) * 256
    )


class ContextDeduplicator:
    """Phase 3-4 deduplicator: Exact + Semantic + Rare-Block layers."""

    def __init__(self):
        self._exact_dedup_enabled = True

    def deduplicate(self, blocks: list) -> DedupeResult:
        """Apply deduplication layers (Exact only in Phase 3)."""
        if not self._exact_dedup_enabled:
            return DedupeResult(
                blocks_kept=tuple(blocks),
                blocks_removed=(),
                completeness_checksum=hashlib.sha256(b'').hexdigest(),
                token_savings_estimated=0
            )

        return deduplicate_exact(blocks)
