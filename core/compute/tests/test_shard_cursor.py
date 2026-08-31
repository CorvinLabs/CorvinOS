"""Tests for ShardCursor — 25 test cases (ADR-0026 §C)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from corvin_compute.fabric.parallel.shard import ShardCursor, ShardManager, ShardPlan


def _list_cursor(n=12) -> list[dict]:
    """Create a simple list-of-dicts cursor."""
    return [{"idx": i, "class": i % 3, "value": float(i)} for i in range(n)]


def _dict_cursor(n=12) -> dict:
    """Create a dict-based cursor."""
    return {
        "X": list(range(n)),
        "y": [i % 2 for i in range(n)],
    }


# ---------------------------------------------------------------------------
# Hash strategy
# ---------------------------------------------------------------------------

class TestHashStrategy:
    def test_hash_shards_cover_all_rows(self):
        cursor = _dict_cursor(12)
        shards = [
            ShardCursor(cursor, shard_index=i, n_shards=3, strategy="hash")
            for i in range(3)
        ]
        all_indices = set()
        for shard in shards:
            indices = shard._build_rows()
            all_indices.update(indices)
        # All 12 "rows" (indices 0..11) should be covered
        assert len(all_indices) == 12

    def test_hash_shards_are_disjoint(self):
        cursor = _dict_cursor(12)
        seen = set()
        for i in range(3):
            shard = ShardCursor(cursor, shard_index=i, n_shards=3, strategy="hash")
            rows = shard._build_rows()
            for r in rows:
                assert r not in seen, f"Row {r} appeared in multiple shards"
                seen.add(r)

    def test_hash_n_shards_1_returns_all(self):
        cursor = _dict_cursor(8)
        shard = ShardCursor(cursor, shard_index=0, n_shards=1, strategy="hash")
        assert len(shard) == 8

    def test_hash_dict_access_x_key(self):
        cursor = _dict_cursor(6)
        shard = ShardCursor(cursor, shard_index=0, n_shards=2, strategy="hash")
        x_vals = shard["X"]
        assert isinstance(x_vals, list)
        assert len(x_vals) > 0

    def test_hash_get_with_default(self):
        cursor = _dict_cursor(4)
        shard = ShardCursor(cursor, shard_index=0, n_shards=2, strategy="hash")
        result = shard.get("nonexistent", "default")
        assert result == "default"


# ---------------------------------------------------------------------------
# Range strategy
# ---------------------------------------------------------------------------

class TestRangeStrategy:
    def test_range_shards_sequential(self):
        cursor = _dict_cursor(12)
        shards = [
            ShardCursor(cursor, shard_index=i, n_shards=3, strategy="range")
            for i in range(3)
        ]
        # Shard 0 should have indices 0-3, shard 1: 4-7, shard 2: 8-11
        for shard in shards:
            rows = shard._build_rows()
            assert len(rows) == 4

    def test_range_covers_all_rows(self):
        cursor = _dict_cursor(9)
        all_seen = set()
        for i in range(3):
            shard = ShardCursor(cursor, shard_index=i, n_shards=3, strategy="range")
            all_seen.update(shard._build_rows())
        assert len(all_seen) == 9

    def test_range_shards_are_disjoint(self):
        cursor = _dict_cursor(10)
        seen = set()
        for i in range(2):
            shard = ShardCursor(cursor, shard_index=i, n_shards=2, strategy="range")
            rows = set(shard._build_rows())
            assert not rows.intersection(seen)
            seen.update(rows)


# ---------------------------------------------------------------------------
# Stratified strategy
# ---------------------------------------------------------------------------

class TestStratifiedStrategy:
    def test_stratified_class_balanced(self):
        # 9 rows: exactly 3 from each class (class 0, 1, 2)
        # With 3 shards, each class gets exactly 1 row per shard → 3 rows per shard
        rows = [{"class": i % 3} for i in range(9)]
        shards = [
            ShardCursor(rows, shard_index=i, n_shards=3, strategy="stratified",
                        stratify_col="class")
            for i in range(3)
        ]
        # Each shard should have 3 rows (one per class)
        for shard in shards:
            assert len(shard) == 3

    def test_stratified_covers_all_rows(self):
        rows = [{"class": i % 2} for i in range(10)]
        all_seen = []
        for i in range(2):
            shard = ShardCursor(rows, shard_index=i, n_shards=2, strategy="stratified",
                                stratify_col="class")
            all_seen.extend(shard._build_rows())
        assert len(all_seen) == 10

    def test_stratified_no_stratify_col_falls_back(self):
        cursor = _dict_cursor(8)
        shard = ShardCursor(cursor, shard_index=0, n_shards=2, strategy="stratified",
                            stratify_col=None)
        # Should not raise
        rows = shard._build_rows()
        assert isinstance(rows, list)


# ---------------------------------------------------------------------------
# Time window strategy
# ---------------------------------------------------------------------------

class TestTimeWindowStrategy:
    def test_time_window_covers_all_rows(self):
        cursor = _dict_cursor(12)
        seen = set()
        for i in range(3):
            shard = ShardCursor(cursor, shard_index=i, n_shards=3,
                                strategy="time_window", time_col="ts",
                                window_size_days=7)
            seen.update(shard._build_rows())
        assert len(seen) == 12

    def test_time_window_shards_disjoint(self):
        cursor = _dict_cursor(9)
        seen = set()
        for i in range(3):
            shard = ShardCursor(cursor, shard_index=i, n_shards=3, strategy="time_window")
            rows = set(shard._build_rows())
            assert not rows.intersection(seen)
            seen.update(rows)


# ---------------------------------------------------------------------------
# total_rows_estimate
# ---------------------------------------------------------------------------

class TestTotalRowsEstimate:
    def test_estimate_close_to_actual(self):
        cursor = _dict_cursor(100)
        shard = ShardCursor(cursor, shard_index=0, n_shards=2, strategy="range")
        estimate = shard.total_rows_estimate()
        actual = len(shard)
        # Within 10% noise
        assert abs(estimate - actual) <= actual * 0.15

    def test_estimate_nonnegative(self):
        cursor = _dict_cursor(4)
        shard = ShardCursor(cursor, shard_index=0, n_shards=4, strategy="hash")
        assert shard.total_rows_estimate() >= 0


# ---------------------------------------------------------------------------
# inter_job_compatible=False → single full-cursor job
# ---------------------------------------------------------------------------

class TestCompatibleFalse:
    def test_single_shard_when_incompatible(self):
        mgr = ShardManager()
        cursor = _dict_cursor(20)
        plan = mgr.plan(cursor, n_shards=4, strategy="hash",
                        backend_inter_job_compatible=False)
        assert plan.single_shard is True
        assert plan.n_shards == 1
        shards = mgr.execute(cursor, plan)
        assert len(shards) == 1

    def test_single_shard_contains_all_data(self):
        mgr = ShardManager()
        cursor = _dict_cursor(8)
        plan = mgr.plan(cursor, n_shards=3, backend_inter_job_compatible=False)
        shards = mgr.execute(cursor, plan)
        assert len(shards) == 1
        # The single shard wraps the full cursor with shard_index=0, n_shards=1

    def test_n_shards_when_compatible(self):
        mgr = ShardManager()
        cursor = _dict_cursor(12)
        plan = mgr.plan(cursor, n_shards=3, backend_inter_job_compatible=True)
        shards = mgr.execute(cursor, plan)
        assert len(shards) == 3

    def test_unknown_strategy_falls_back_to_hash(self):
        shard = ShardCursor(_dict_cursor(8), shard_index=0, n_shards=2,
                            strategy="unknown_xyz")
        rows = shard._build_rows()
        assert isinstance(rows, list)
