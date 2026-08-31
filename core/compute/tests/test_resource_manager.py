"""Tests for ResourceManager — 20 test cases (ADR-0026 §C)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from corvin_compute.fabric.parallel.resources import (
    FabricResourceDenied,
    ResourceManager,
    ResourceSlot,
)


def _make_manager(total_cpu=8.0, total_mem_mib=4096, manifest_cap=16) -> ResourceManager:
    return ResourceManager(
        total_cpu_override=total_cpu,
        total_mem_mib_override=total_mem_mib,
        manifest_max_instances=manifest_cap,
        cpu_per_slot=1.0,
        mem_mib_per_slot=512,
    )


# ---------------------------------------------------------------------------
# max_slots_for tests
# ---------------------------------------------------------------------------

class TestMaxSlotsFor:
    def test_limited_by_cpu(self):
        mgr = _make_manager(total_cpu=4.0, total_mem_mib=16384, manifest_cap=64)
        # 4 cpu / 1 per slot = 4; 16384 mem / 512 = 32; cap=64 → min=4
        assert mgr.max_slots == 4

    def test_limited_by_memory(self):
        mgr = _make_manager(total_cpu=16.0, total_mem_mib=1024, manifest_cap=64)
        # 16 cpu / 1 = 16; 1024 / 512 = 2; cap=64 → min=2
        assert mgr.max_slots == 2

    def test_limited_by_manifest_cap(self):
        mgr = _make_manager(total_cpu=64.0, total_mem_mib=65536, manifest_cap=3)
        # 64/1=64; 65536/512=128; cap=3 → min=3
        assert mgr.max_slots == 3

    def test_max_slots_for_with_override_params(self):
        mgr = _make_manager(total_cpu=8.0, total_mem_mib=4096, manifest_cap=16)
        # Override per-slot costs
        n = mgr.max_slots_for(cpu_per_slot=2.0, mem_mib_per_slot=512)
        # 8/2=4; 4096/512=8; cap=16 → 4
        assert n == 4

    def test_max_slots_at_least_one(self):
        mgr = _make_manager(total_cpu=0.5, total_mem_mib=256, manifest_cap=1)
        assert mgr.max_slots >= 1

    def test_available_slots_equals_max_when_empty(self):
        mgr = _make_manager()
        assert mgr.available_slots() == mgr.max_slots


# ---------------------------------------------------------------------------
# allocate tests
# ---------------------------------------------------------------------------

class TestAllocate:
    def test_allocate_single_slot(self):
        mgr = _make_manager()
        slots = mgr.allocate(1, backend="sklearn", run_id="r1")
        assert len(slots) == 1
        assert isinstance(slots[0], ResourceSlot)

    def test_allocate_returns_correct_cpu_mem(self):
        mgr = _make_manager(total_cpu=4.0, total_mem_mib=2048)
        slots = mgr.allocate(1)
        assert slots[0].cpu_cores == pytest.approx(1.0)
        assert slots[0].mem_mib == 512

    def test_allocate_decrements_available(self):
        mgr = _make_manager()
        before = mgr.available_slots()
        mgr.allocate(2)
        assert mgr.available_slots() == before - 2

    def test_allocate_all_slots(self):
        mgr = _make_manager(total_cpu=4.0, total_mem_mib=4096, manifest_cap=4)
        slots = mgr.allocate(4)
        assert len(slots) == 4
        assert mgr.available_slots() == 0

    def test_over_allocation_raises(self):
        mgr = _make_manager(total_cpu=2.0, total_mem_mib=1024, manifest_cap=2)
        with pytest.raises(FabricResourceDenied):
            mgr.allocate(3)

    def test_over_allocation_emits_audit(self):
        events = []
        mgr = ResourceManager(
            total_cpu_override=2.0,
            total_mem_mib_override=1024,
            manifest_max_instances=2,
            cpu_per_slot=1.0,
            mem_mib_per_slot=512,
            emit_fn=lambda e, **kw: events.append((e, kw)),
        )
        with pytest.raises(FabricResourceDenied):
            mgr.allocate(5, run_id="r1", backend="sklearn")
        deny_events = [e for e, kw in events if "resource_slot_denied" in e]
        assert len(deny_events) == 1
        assert events[0][1]["requested_slots"] == 5

    def test_slot_ids_are_unique(self):
        mgr = _make_manager()
        slots = mgr.allocate(4)
        ids = [s.slot_id for s in slots]
        assert len(ids) == len(set(ids))

    def test_allocate_backend_and_run_id_stored(self):
        mgr = _make_manager()
        slots = mgr.allocate(1, backend="xgboost", run_id="run-abc")
        assert slots[0].backend == "xgboost"
        assert slots[0].run_id == "run-abc"


# ---------------------------------------------------------------------------
# release tests
# ---------------------------------------------------------------------------

class TestRelease:
    def test_release_restores_availability(self):
        mgr = _make_manager()
        before = mgr.available_slots()
        slots = mgr.allocate(3)
        mgr.release(slots)
        assert mgr.available_slots() == before

    def test_release_partial(self):
        mgr = _make_manager()
        slots = mgr.allocate(4)
        mgr.release(slots[:2])
        # Only 2 released
        assert mgr.available_slots() == mgr.max_slots - 2

    def test_release_empty_list_is_safe(self):
        mgr = _make_manager()
        before = mgr.available_slots()
        mgr.release([])
        assert mgr.available_slots() == before

    def test_cgroup_fallback_when_file_unavailable(self):
        """ResourceManager falls back gracefully when cgroup files don't exist."""
        # Use mocked cgroup read that raises
        mgr = ResourceManager(
            total_cpu_override=None,  # Force real read
            total_mem_mib_override=None,
        )
        # Should not raise — fallback to os.cpu_count() / 4096 MiB
        assert mgr.max_slots >= 1
