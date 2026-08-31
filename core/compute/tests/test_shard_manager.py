"""Tests for ShardManager — 25 test cases (ADR-0026 §C)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from corvin_compute.fabric.parallel.shard import ShardCursor, ShardManager, ShardPlan
from corvin_compute.fabric.parallel.resources import FabricResourceDenied, ResourceManager


def _dict_cursor(n=20) -> dict:
    return {
        "X": list(range(n)),
        "y": [i % 2 for i in range(n)],
    }


def _list_cursor(n=20) -> list:
    return list(range(n))


# ---------------------------------------------------------------------------
# ShardManager.plan() tests
# ---------------------------------------------------------------------------

class TestShardManagerPlan:
    def setup_method(self):
        self.mgr = ShardManager()

    def test_plan_compatible_true_returns_n_shards(self):
        plan = self.mgr.plan(
            _dict_cursor(), n_shards=4, strategy="hash",
            backend_inter_job_compatible=True
        )
        assert plan.n_shards == 4
        assert plan.single_shard is False

    def test_plan_compatible_false_returns_single_shard(self):
        plan = self.mgr.plan(
            _dict_cursor(), n_shards=4,
            backend_inter_job_compatible=False
        )
        assert plan.single_shard is True
        assert plan.n_shards == 1

    def test_plan_default_strategy_is_hash(self):
        plan = self.mgr.plan(_dict_cursor(), n_shards=3)
        assert plan.strategy == "hash"

    def test_plan_preserves_strategy(self):
        plan = self.mgr.plan(_dict_cursor(), n_shards=2, strategy="range")
        assert plan.strategy == "range"

    def test_plan_preserves_stratify_col(self):
        plan = self.mgr.plan(_dict_cursor(), n_shards=2, stratify_col="y")
        assert plan.stratify_col == "y"

    def test_plan_preserves_time_col(self):
        plan = self.mgr.plan(
            _dict_cursor(), n_shards=2,
            time_col="created_at", window_size_days=14,
        )
        assert plan.time_col == "created_at"
        assert plan.window_size_days == 14


# ---------------------------------------------------------------------------
# ShardManager.execute() tests
# ---------------------------------------------------------------------------

class TestShardManagerExecute:
    def setup_method(self):
        self.mgr = ShardManager()

    def test_execute_returns_n_shard_cursors(self):
        plan = self.mgr.plan(_dict_cursor(), n_shards=3, backend_inter_job_compatible=True)
        shards = self.mgr.execute(_dict_cursor(), plan)
        assert len(shards) == 3

    def test_execute_single_shard_wraps_full_cursor(self):
        plan = ShardPlan(strategy="hash", n_shards=1, single_shard=True)
        shards = self.mgr.execute(_dict_cursor(), plan)
        assert len(shards) == 1
        assert isinstance(shards[0], ShardCursor)

    def test_execute_hash_shards_disjoint(self):
        n = 24
        cursor = _dict_cursor(n)
        plan = self.mgr.plan(cursor, n_shards=4, strategy="hash",
                             backend_inter_job_compatible=True)
        shards = self.mgr.execute(cursor, plan)
        seen = set()
        for shard in shards:
            rows = shard._build_rows()
            for r in rows:
                assert r not in seen
                seen.add(r)

    def test_execute_range_shards_disjoint(self):
        n = 20
        cursor = _dict_cursor(n)
        plan = self.mgr.plan(cursor, n_shards=4, strategy="range",
                             backend_inter_job_compatible=True)
        shards = self.mgr.execute(cursor, plan)
        seen = set()
        for shard in shards:
            rows = shard._build_rows()
            for r in rows:
                assert r not in seen
                seen.add(r)

    def test_execute_stratified_shards_disjoint(self):
        cursor = _list_cursor(12)
        plan = self.mgr.plan(cursor, n_shards=3, strategy="stratified",
                             backend_inter_job_compatible=True)
        shards = self.mgr.execute(cursor, plan)
        seen = set()
        for shard in shards:
            rows = shard._build_rows()
            for r in rows:
                assert r not in seen
                seen.add(r)

    def test_execute_time_window_shards_disjoint(self):
        cursor = _dict_cursor(15)
        plan = self.mgr.plan(cursor, n_shards=3, strategy="time_window",
                             backend_inter_job_compatible=True)
        shards = self.mgr.execute(cursor, plan)
        seen = set()
        for shard in shards:
            rows = shard._build_rows()
            for r in rows:
                assert r not in seen
                seen.add(r)

    def test_execute_hash_covers_all_rows(self):
        n = 18
        cursor = _dict_cursor(n)
        plan = self.mgr.plan(cursor, n_shards=3, strategy="hash")
        shards = self.mgr.execute(cursor, plan)
        all_rows = set()
        for shard in shards:
            all_rows.update(shard._build_rows())
        assert len(all_rows) == n

    def test_execute_range_covers_all_rows(self):
        n = 12
        cursor = _dict_cursor(n)
        plan = self.mgr.plan(cursor, n_shards=4, strategy="range")
        shards = self.mgr.execute(cursor, plan)
        all_rows = set()
        for shard in shards:
            all_rows.update(shard._build_rows())
        assert len(all_rows) == n

    def test_shard_indices_correct(self):
        cursor = _dict_cursor()
        plan = self.mgr.plan(cursor, n_shards=3)
        shards = self.mgr.execute(cursor, plan)
        for i, shard in enumerate(shards):
            assert shard.shard_index == i
            assert shard.n_shards == 3


# ---------------------------------------------------------------------------
# ResourceManager integration with ShardManager
# ---------------------------------------------------------------------------

class TestResourceSlotWithShards:
    def test_slots_allocated_per_shard(self):
        mgr = ResourceManager(
            total_cpu_override=8.0,
            total_mem_mib_override=4096,
        )
        n_shards = 3
        slots = mgr.allocate(n_shards, run_id="r1", backend="sklearn")
        assert len(slots) == n_shards
        mgr.release(slots)
        assert mgr.available_slots() == mgr.max_slots

    def test_slots_released_on_error(self):
        mgr = ResourceManager(
            total_cpu_override=4.0,
            total_mem_mib_override=2048,
            manifest_max_instances=4,
        )
        before = mgr.available_slots()
        slots = mgr.allocate(2)
        try:
            raise RuntimeError("simulated error")
        except RuntimeError:
            mgr.release(slots)
        assert mgr.available_slots() == before

    def test_over_capacity_denied(self):
        mgr = ResourceManager(
            total_cpu_override=2.0,
            total_mem_mib_override=1024,
            manifest_max_instances=2,
        )
        with pytest.raises(FabricResourceDenied):
            mgr.allocate(10)

    def test_sequential_allocations_within_limit(self):
        mgr = ResourceManager(
            total_cpu_override=4.0,
            total_mem_mib_override=4096,
        )
        s1 = mgr.allocate(2)
        s2 = mgr.allocate(2)
        assert mgr.available_slots() == 0
        mgr.release(s1)
        mgr.release(s2)
        assert mgr.available_slots() == mgr.max_slots
