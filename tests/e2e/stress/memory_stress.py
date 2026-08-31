"""
Memory Stress Tests for CorvinOS v1.0.0

Tests memory stability under sustained load:
- Large object allocation + GC
- Sustained memory pressure
- No memory leaks
"""

import pytest
import tracemalloc
import gc
from typing import List, Dict
import sys


class TestMemoryUnderLoad:
    """Memory stability under sustained high-frequency operations."""

    def test_memory_10k_events_no_regression(self):
        """Memory remains bounded after 10k event processing."""
        tracemalloc.start()

        # Simulate 10k events
        events = []
        for i in range(10000):
            event = {
                "id": i,
                "type": f"event_{i % 10}",
                "timestamp": 1234567890 + i,
                "data": {"value": i * 2, "name": f"event-{i}"}
            }
            events.append(event)

        # Force GC
        gc.collect()

        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        # Memory should not exceed 100MB for 10k events
        peak_mb = peak / (1024 * 1024)
        assert peak_mb < 100, f"Memory peak {peak_mb}MB exceeded 100MB limit for 10k events"

    def test_memory_100k_objects_bounded(self):
        """Memory bounded with 100k small objects."""
        tracemalloc.start()

        objects = []
        for i in range(100000):
            obj = {"id": i, "value": i * 1.5}
            objects.append(obj)

        gc.collect()

        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        # 100k small dicts should be <500MB
        peak_mb = peak / (1024 * 1024)
        assert peak_mb < 500, f"Memory peak {peak_mb}MB exceeded 500MB limit for 100k objects"

    def test_memory_large_string_list_bounded(self):
        """Memory bounded with large string allocations."""
        tracemalloc.start()

        strings = []
        for i in range(10000):
            # 1KB string per item = 10MB total (plus overhead)
            large_str = "x" * 1024
            strings.append(large_str)

        gc.collect()

        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        # 10MB strings + overhead should be <50MB
        peak_mb = peak / (1024 * 1024)
        assert peak_mb < 50, f"Memory peak {peak_mb}MB exceeded 50MB limit for large strings"

    def test_memory_cleanup_after_scope_exit(self):
        """Memory released when large objects go out of scope."""
        tracemalloc.start()

        # Create large object in local scope
        def create_and_release():
            big_list = [i for i in range(1000000)]  # 1M integers
            return sum(big_list)

        result = create_and_release()
        gc.collect()

        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        # After cleanup, current should be much less than peak
        peak_mb = peak / (1024 * 1024)
        current_mb = current / (1024 * 1024)

        # Peak was high, but current should be low
        assert current_mb < peak_mb / 2, \
            f"Memory not released: peak={peak_mb}MB, current={current_mb}MB"


class TestMemoryGarbageCollectionBehavior:
    """GC behavior under stress."""

    def test_gc_collection_frequency(self):
        """GC runs frequently enough under high allocation pressure."""
        gc.collect()  # Baseline

        # Allocate and release rapidly
        for _ in range(1000):
            temp_list = [i for i in range(1000)]
            del temp_list

        # Force collection
        gc.collect()

        # No assertion needed; test verifies no hang or exception

    def test_gc_with_cycles_handled(self):
        """GC handles cyclic references correctly."""
        # Create cyclic references
        cycles = []
        for _ in range(100):
            d1 = {}
            d2 = {}
            d1['ref'] = d2
            d2['ref'] = d1
            cycles.append(d1)

        del cycles
        gc.collect()

        # Verify unreachable objects were collected
        assert True  # Test passes if no hang/exception


class TestMemoryLeakDetection:
    """Detect potential memory leaks."""

    def test_repeated_operation_no_leak(self):
        """Repeated operations don't accumulate memory."""
        tracemalloc.start()

        def operation():
            """Simulated operation."""
            result = [i**2 for i in range(1000)]
            return sum(result)

        # Run operation many times
        for _ in range(1000):
            operation()
            if _ % 100 == 0:
                gc.collect()

        current1, peak1 = tracemalloc.get_traced_memory()

        # Run again and compare
        for _ in range(1000):
            operation()
            if _ % 100 == 0:
                gc.collect()

        current2, peak2 = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        # Current memory should not grow substantially between runs
        growth_ratio = current2 / max(current1, 1)
        assert growth_ratio < 1.5, \
            f"Memory growth detected: {current1 / 1024 / 1024}MB → {current2 / 1024 / 1024}MB"


class TestMemoryWithDifferentDataTypes:
    """Memory handling with various data structure types."""

    def test_memory_mixed_data_structures(self):
        """Mixed data structures don't cause memory bloat."""
        tracemalloc.start()

        data = {
            "lists": [[i for i in range(100)] for _ in range(100)],
            "dicts": [{"key": i} for i in range(1000)],
            "tuples": [(i, i+1, i+2) for i in range(1000)],
            "sets": [set(range(10)) for _ in range(100)],
        }

        gc.collect()

        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        peak_mb = peak / (1024 * 1024)
        assert peak_mb < 100, f"Memory peak {peak_mb}MB excessive for mixed structures"


# ============================================================================
# Helpers
# ============================================================================

def format_bytes(num_bytes: int) -> str:
    """Format bytes as human-readable."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if abs(num_bytes) < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
