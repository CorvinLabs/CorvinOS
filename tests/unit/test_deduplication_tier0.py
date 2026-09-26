"""Tier 0: Deduplication (Layer 1, Phase 3) — Exact-match deduplication tests."""

import hashlib
import json
import pytest

from core.context.deduplication.deduplicator import (
    DedupeResult,
    deduplicate_exact,
    ContextDeduplicator,
)


class TestDeduplicateExact:
    """Phase 3: Exact-match deduplication (bitwise identical blocks)."""

    def test_empty_input(self):
        """Empty block list returns empty kept/removed."""
        result = deduplicate_exact([])
        assert result.blocks_kept == ()
        assert result.blocks_removed == ()
        assert result.token_savings_estimated == 0

    def test_no_duplicates(self):
        """Unique blocks are all kept."""
        blocks = [
            {"id": "a", "text": "first"},
            {"id": "b", "text": "second"},
            {"id": "c", "text": "third"},
        ]
        result = deduplicate_exact(blocks)
        assert len(result.blocks_kept) == 3
        assert len(result.blocks_removed) == 0
        assert result.token_savings_estimated == 0

    def test_exact_duplicate_dicts(self):
        """Bitwise identical dict is removed (first kept, second removed)."""
        block_a = {"id": "x", "value": "same"}
        block_b = {"id": "x", "value": "same"}
        block_c = {"id": "y", "value": "different"}
        
        result = deduplicate_exact([block_a, block_b, block_c])
        
        assert len(result.blocks_kept) == 2
        assert len(result.blocks_removed) == 1
        # Token savings: ~256 per removed block (heuristic)
        assert result.token_savings_estimated == 256

    def test_exact_duplicate_strings(self):
        """Identical strings are deduplicated."""
        blocks = ["hello", "world", "hello", "hello", "world"]
        result = deduplicate_exact(blocks)
        
        assert len(result.blocks_kept) == 2
        assert len(result.blocks_removed) == 3
        assert result.token_savings_estimated == 3 * 256

    def test_order_preserved(self):
        """Kept blocks maintain input order (first occurrence kept)."""
        blocks = [
            {"seq": 1},
            {"seq": 2},
            {"seq": 1},  # duplicate of first
            {"seq": 3},
            {"seq": 2},  # duplicate of second
        ]
        result = deduplicate_exact(blocks)
        
        assert result.blocks_kept == (
            {"seq": 1},
            {"seq": 2},
            {"seq": 3},
        )

    def test_completeness_checksum(self):
        """Completeness checksum is deterministic and changes with input."""
        blocks1 = [{"a": 1}, {"b": 2}]
        blocks2 = [{"a": 1}, {"b": 2}]
        blocks3 = [{"a": 1}, {"b": 3}]
        
        result1 = deduplicate_exact(blocks1)
        result2 = deduplicate_exact(blocks2)
        result3 = deduplicate_exact(blocks3)
        
        # Same input → same checksum
        assert result1.completeness_checksum == result2.completeness_checksum
        
        # Different input → different checksum
        assert result1.completeness_checksum != result3.completeness_checksum

    def test_mixed_types(self):
        """Mixed block types (dict, str, int) are deduplicated correctly."""
        blocks = [
            {"key": "value"},
            "string_block",
            123,
            {"key": "value"},  # duplicate dict
            "string_block",  # duplicate string
            456,
        ]
        result = deduplicate_exact(blocks)
        
        # First 4 unique (dict, str, 123, 456), then 2 removed (dict dup, str dup)
        assert len(result.blocks_kept) == 4
        assert len(result.blocks_removed) == 2

    def test_deduped_result_is_frozen_dataclass(self):
        """DedupeResult is immutable."""
        result = deduplicate_exact([{"a": 1}])
        
        with pytest.raises(AttributeError):
            result.blocks_kept = ()


class TestContextDeduplicator:
    """ContextDeduplicator wrapper class."""

    def test_enabled_deduplicator(self):
        """Enabled deduplicator removes duplicates."""
        dedup = ContextDeduplicator()
        blocks = [{"a": 1}, {"b": 2}, {"a": 1}]
        
        result = dedup.deduplicate(blocks)
        
        assert len(result.blocks_kept) == 2
        assert len(result.blocks_removed) == 1

    def test_disabled_deduplicator(self):
        """Disabled deduplicator passes through all blocks."""
        dedup = ContextDeduplicator()
        dedup._exact_dedup_enabled = False
        
        blocks = [{"a": 1}, {"b": 2}, {"a": 1}]
        result = dedup.deduplicate(blocks)
        
        # All blocks kept when disabled
        assert len(result.blocks_kept) == 3
        assert len(result.blocks_removed) == 0
        assert result.completeness_checksum == hashlib.sha256(b'').hexdigest()


class TestDeduplicationIntegration:
    """Integration: Deduplication in realistic context (Layer 1 only)."""

    def test_large_block_list_with_duplicates(self):
        """Large input with many duplicates is efficiently deduplicated."""
        # Simulate blocks: first 300 identical + 1 different + last 700 identical
        block = {"content": "x" * 100}  # Large-ish block
        blocks = [block] * 300 + [{"content": "different"}] + [block] * 700
        
        result = deduplicate_exact(blocks)
        
        # Result: 2 unique blocks kept (original block + different)
        # 999 duplicates removed (300-1=299 from first, 700 from last = 999)
        assert len(result.blocks_kept) == 2
        assert len(result.blocks_removed) == 999

    def test_json_dict_equivalence(self):
        """Dicts with same content but different creation order are identical."""
        block_a = {"z": 3, "a": 1, "m": 2}
        block_b = {"a": 1, "m": 2, "z": 3}
        block_c = {"z": 3, "a": 1, "m": 2}
        
        result = deduplicate_exact([block_a, block_b, block_c])
        
        # All three should be identical (dicts with same keys/values)
        assert len(result.blocks_kept) == 1
        assert len(result.blocks_removed) == 2

    def test_empty_dict_deduplication(self):
        """Empty dicts are deduplicated."""
        blocks = [{}, {}, {"key": "value"}, {}]
        result = deduplicate_exact(blocks)
        
        assert len(result.blocks_kept) == 2  # One empty, one with content
        assert len(result.blocks_removed) == 2

    def test_nested_dict_equivalence(self):
        """Nested dicts with same structure are deduplicated."""
        block_a = {"outer": {"inner": {"value": 1}}}
        block_b = {"outer": {"inner": {"value": 1}}}
        block_c = {"outer": {"inner": {"value": 2}}}
        
        result = deduplicate_exact([block_a, block_b, block_c])
        
        assert len(result.blocks_kept) == 2
        assert len(result.blocks_removed) == 1


class TestDeduplicationGate:
    """E2E: Deduplication as a wiring gate (fail-closed, always safe)."""

    def test_completeness_after_dedup(self):
        """Completeness checksum proves nothing was lost (only duplicates removed)."""
        blocks = [
            {"id": 1, "data": "a"},
            {"id": 2, "data": "b"},
            {"id": 1, "data": "a"},  # duplicate
        ]
        
        result = deduplicate_exact(blocks)
        
        # Checksum of kept blocks matches what was kept
        kept_hashes = [
            hashlib.sha256(
                json.dumps(b, sort_keys=True).encode('utf-8')
            ).hexdigest()
            for b in result.blocks_kept
        ]
        expected_checksum = hashlib.sha256(
            '\n'.join(kept_hashes).encode('utf-8')
        ).hexdigest()
        
        assert result.completeness_checksum == expected_checksum

    def test_deduplication_is_idempotent(self):
        """Deduplicating twice gives same result as deduplicating once."""
        blocks = [{"a": 1}, {"b": 2}, {"a": 1}]
        
        result1 = deduplicate_exact(blocks)
        result2 = deduplicate_exact(list(result1.blocks_kept))
        
        assert result1.blocks_kept == result2.blocks_kept
        assert result1.completeness_checksum == result2.completeness_checksum
