"""Plugin Marketplace Performance Profiler (ADR-0249 Phase 4+).

Provides CPU and memory profiling for marketplace operations:
- CPU profiling (cProfile) for hot path analysis
- Memory profiling (memory_profiler) for leaks and peaks
- Allocation tracking (tracemalloc) for allocation patterns
- GC analysis for collection cycles

Usage:
    python plugin_performance_profiler.py --profile=cpu --workload=discovery
    python plugin_performance_profiler.py --profile=memory --workload=concurrent_installs
    python plugin_performance_profiler.py --profile=all --workload=sustained_load
"""

import asyncio
import cProfile
import io
import json
import logging
import pstats
import sys
import time
import tracemalloc
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
import gc
import resource

try:
    from memory_profiler import profile
except ImportError:
    def profile(func):
        """Fallback if memory_profiler not installed."""
        return func

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# ─── Data Models ───────────────────────────────────────────────────────────

@dataclass
class CPUProfile:
    """CPU profiling results."""
    function: str
    cumulative_time_sec: float
    cumulative_time_pct: float
    local_time_sec: float
    local_time_pct: float
    call_count: int
    avg_call_time_ms: float


@dataclass
class MemorySnapshot:
    """Memory snapshot at a point in time."""
    timestamp: float
    current_mb: float
    peak_mb: float
    allocated_mb: float
    freed_mb: float
    gc_collections: int
    gc_collected: int


@dataclass
class AllocationSite:
    """Top allocation site."""
    filename: str
    lineno: int
    size_mb: float
    count: int
    allocation_rate_per_sec: float


@dataclass
class ProfileReport:
    """Complete profiling report."""
    workload_name: str
    duration_sec: float
    cpu_profile: Optional[Dict[str, Any]] = None
    memory_profile: Optional[Dict[str, Any]] = None
    allocation_sites: List[AllocationSite] = field(default_factory=list)
    gc_stats: Dict[str, Any] = field(default_factory=dict)
    peak_memory_mb: float = 0.0
    memory_growth_mb: float = 0.0


# ─── CPU Profiler ──────────────────────────────────────────────────────────

class CPUProfiler:
    """Profile CPU usage during marketplace operations."""

    def __init__(self):
        self.profiler = cProfile.Profile()
        self.results = []

    def start(self):
        """Start CPU profiling."""
        gc.disable()
        self.profiler.enable()

    def stop(self) -> List[CPUProfile]:
        """Stop profiling and collect results."""
        self.profiler.disable()
        gc.enable()

        # Get stats
        stats = pstats.Stats(self.profiler)
        stats.strip_dirs()

        # Capture top functions
        buffer = io.StringIO()
        stats.print_stats(20, file=buffer)
        output = buffer.getvalue()

        # Parse output and extract top functions
        results = []
        for line in output.split("\n"):
            if "\.py:" in line and "{" not in line:
                parts = line.split()
                if len(parts) >= 6:
                    try:
                        cum_time = float(parts[2])
                        local_time = float(parts[3])
                        calls = int(parts[4].split("/")[0])
                        func = " ".join(parts[5:])

                        results.append(CPUProfile(
                            function=func,
                            cumulative_time_sec=cum_time,
                            cumulative_time_pct=cum_time * 100 / max(1, sum(r.cumulative_time_sec for r in results) + cum_time),
                            local_time_sec=local_time,
                            local_time_pct=local_time * 100 / max(1, sum(r.local_time_sec for r in results) + local_time),
                            call_count=calls,
                            avg_call_time_ms=(local_time / calls * 1000) if calls > 0 else 0,
                        ))
                    except (ValueError, ZeroDivisionError):
                        pass

        return results[:20]  # Top 20 functions


# ─── Memory Profiler ───────────────────────────────────────────────────────

class MemoryProfiler:
    """Profile memory usage during marketplace operations."""

    def __init__(self):
        self.snapshots = []
        self.start_time = None
        self.start_memory = 0

    def start(self):
        """Start memory profiling."""
        tracemalloc.start()
        self.snapshots.clear()
        self.start_time = time.time()

        # Get initial state
        current, peak = tracemalloc.get_traced_memory()
        self.start_memory = current / (1024 * 1024)

        self.snapshots.append({
            "time": 0.0,
            "current_mb": self.start_memory,
            "peak_mb": peak / (1024 * 1024),
            "delta_mb": 0.0,
        })

    def snapshot(self) -> Dict[str, float]:
        """Take a memory snapshot."""
        if not self.start_time:
            return {}

        elapsed = time.time() - self.start_time
        current, peak = tracemalloc.get_traced_memory()
        current_mb = current / (1024 * 1024)
        peak_mb = peak / (1024 * 1024)

        snapshot = {
            "time": elapsed,
            "current_mb": current_mb,
            "peak_mb": peak_mb,
            "delta_mb": current_mb - self.start_memory,
        }
        self.snapshots.append(snapshot)
        return snapshot

    def stop(self) -> Dict[str, Any]:
        """Stop profiling and collect results."""
        current, peak = tracemalloc.get_traced_memory()
        current_mb = current / (1024 * 1024)
        peak_mb = peak / (1024 * 1024)

        # Get top allocation sites
        snapshot = tracemalloc.take_snapshot()
        top_stats = snapshot.statistics('lineno')

        allocations = []
        for stat in top_stats[:10]:
            allocations.append({
                "filename": stat.traceback[0].filename if stat.traceback else "unknown",
                "lineno": stat.traceback[0].lineno if stat.traceback else 0,
                "size_mb": stat.size / (1024 * 1024),
                "count": stat.count,
            })

        tracemalloc.stop()

        return {
            "current_mb": current_mb,
            "peak_mb": peak_mb,
            "growth_mb": current_mb - self.start_memory,
            "allocations": allocations,
            "snapshots": self.snapshots,
        }


# ─── GC Analysis ──────────────────────────────────────────────────────────

class GCAnalyzer:
    """Analyze garbage collection behavior."""

    def __init__(self):
        self.initial_counts = gc.get_count()
        self.initial_stats = gc.get_stats()

    def get_stats(self) -> Dict[str, Any]:
        """Get GC statistics."""
        current_counts = gc.get_count()
        current_stats = gc.get_stats()

        return {
            "collections": {
                "gen0": current_counts[0],
                "gen1": current_counts[1],
                "gen2": current_counts[2],
            },
            "collection_counts": {
                "gen0": current_counts[0] - self.initial_counts[0],
                "gen1": current_counts[1] - self.initial_counts[1],
                "gen2": current_counts[2] - self.initial_counts[2],
            },
        }


# ─── Marketplace Workloads ─────────────────────────────────────────────────

class Workloads:
    """Pre-defined workload scenarios."""

    @staticmethod
    async def discovery_workload(num_requests: int = 100):
        """Simulate marketplace discovery (list/search)."""
        from unittest.mock import AsyncMock

        for i in range(num_requests):
            # Simulate API call
            data = _generate_plugin_list(num_plugins=100)
            # Simulate filtering
            for plugin in data:
                _ = plugin.get("plugin_id")
                _ = plugin.get("rating")

    @staticmethod
    async def search_workload(num_searches: int = 50):
        """Simulate search operations."""
        queries = ["authentication", "database", "security", "monitoring", "terraform"]

        for i in range(num_searches):
            query = queries[i % len(queries)]
            # Simulate search
            plugins = _generate_plugin_list(num_plugins=100)
            results = [
                p for p in plugins
                if query.lower() in p.get("name", "").lower() or
                   query.lower() in p.get("description", "").lower()
            ]

    @staticmethod
    async def concurrent_installs(num_installs: int = 10):
        """Simulate concurrent installations."""
        async def install():
            # Simulate tarball extraction and manifest parsing
            manifest = {
                "plugin_id": "test-plugin",
                "version": "1.0.0",
                "files": [f"file-{i}" for i in range(100)]
            }

            # Simulate installation steps
            for step in ["extract", "verify", "install", "enable"]:
                await asyncio.sleep(0.01)

        tasks = [install() for _ in range(num_installs)]
        await asyncio.gather(*tasks)

    @staticmethod
    async def sustained_load(duration_sec: int = 30, target_rps: int = 10):
        """Simulate sustained load."""
        start = time.time()
        request_count = 0
        request_interval = 1.0 / target_rps

        while time.time() - start < duration_sec:
            # Simulate request
            plugins = _generate_plugin_list(num_plugins=random_choice([10, 50, 100]))
            request_count += 1

            # Rate limiting
            elapsed = time.time() - start
            expected_count = elapsed / request_interval
            if request_count >= expected_count:
                await asyncio.sleep(0.001)


# ─── Profiling Runner ──────────────────────────────────────────────────────

class ProfilingRunner:
    """Run profiling on marketplace workloads."""

    def __init__(self, workload_name: str):
        self.workload_name = workload_name
        self.cpu_profiler = CPUProfiler()
        self.memory_profiler = MemoryProfiler()
        self.gc_analyzer = GCAnalyzer()

    async def run_workload(self, workload_func: Callable, *args, **kwargs):
        """Run a workload with profiling."""
        logger.info(f"Starting profiling for workload: {self.workload_name}")

        self.cpu_profiler.start()
        self.memory_profiler.start()

        start_time = time.time()

        try:
            if asyncio.iscoroutinefunction(workload_func):
                await workload_func(*args, **kwargs)
            else:
                workload_func(*args, **kwargs)
        finally:
            duration = time.time() - start_time

            cpu_results = self.cpu_profiler.stop()
            memory_results = self.memory_profiler.stop()
            gc_stats = self.gc_analyzer.get_stats()

            logger.info(f"Profiling completed in {duration:.2f}s")

            return self._generate_report(
                duration,
                cpu_results,
                memory_results,
                gc_stats
            )

    def _generate_report(
        self,
        duration: float,
        cpu_results: List[CPUProfile],
        memory_results: Dict[str, Any],
        gc_stats: Dict[str, Any],
    ) -> ProfileReport:
        """Generate profiling report."""
        report = ProfileReport(
            workload_name=self.workload_name,
            duration_sec=duration,
            peak_memory_mb=memory_results.get("peak_mb", 0.0),
            memory_growth_mb=memory_results.get("growth_mb", 0.0),
            gc_stats=gc_stats,
        )

        # Format CPU profile
        if cpu_results:
            report.cpu_profile = {
                "top_functions": [asdict(r) for r in cpu_results[:10]],
                "total_functions": len(cpu_results),
            }

        # Format memory profile
        if memory_results:
            report.memory_profile = {
                "current_mb": memory_results.get("current_mb", 0.0),
                "peak_mb": memory_results.get("peak_mb", 0.0),
                "growth_mb": memory_results.get("growth_mb", 0.0),
                "top_allocations": memory_results.get("allocations", [])[:5],
            }

        return report


# ─── Report Generation ────────────────────────────────────────────────────

def print_profiling_report(report: ProfileReport):
    """Print profiling report to console."""
    print("\n" + "="*80)
    print(f"PROFILING REPORT: {report.workload_name}")
    print("="*80)

    print(f"\nDuration: {report.duration_sec:.2f}s")
    print(f"Peak Memory: {report.peak_memory_mb:.2f} MB")
    print(f"Memory Growth: {report.memory_growth_mb:.2f} MB")

    # CPU Profile
    if report.cpu_profile:
        print("\nTop CPU Functions:")
        print(f"{'Function':<40} {'Time %':<8} {'Calls':<8}")
        print("-" * 60)

        for func in report.cpu_profile.get("top_functions", [])[:10]:
            print(f"{func['function'][:40]:<40} "
                  f"{func['cumulative_time_pct']:>6.2f}% "
                  f"{func['call_count']:>8}")

    # Memory Profile
    if report.memory_profile:
        print("\nTop Memory Allocations:")
        print(f"{'File:Line':<40} {'Size MB':<10} {'Count':<8}")
        print("-" * 60)

        for alloc in report.memory_profile.get("top_allocations", []):
            location = f"{Path(alloc['filename']).name}:{alloc['lineno']}"
            print(f"{location:<40} {alloc['size_mb']:>8.2f} MB {alloc['count']:>8}")

    # GC Stats
    if report.gc_stats:
        print("\nGarbage Collection:")
        for gen, count in report.gc_stats.get("collection_counts", {}).items():
            print(f"  Generation {gen}: {count} collections")

    print("="*80 + "\n")


def save_profiling_report(report: ProfileReport, output_path: Path):
    """Save profiling report to JSON."""
    data = {
        "workload_name": report.workload_name,
        "duration_sec": report.duration_sec,
        "peak_memory_mb": report.peak_memory_mb,
        "memory_growth_mb": report.memory_growth_mb,
        "cpu_profile": report.cpu_profile,
        "memory_profile": report.memory_profile,
        "gc_stats": report.gc_stats,
    }

    with open(output_path, "w") as f:
        json.dump(data, f, indent=2, default=str)

    logger.info(f"Report saved to {output_path}")


# ─── Helper Functions ──────────────────────────────────────────────────────

def _generate_plugin_list(num_plugins: int = 100) -> List[Dict[str, Any]]:
    """Generate mock plugin list."""
    return [
        {
            "plugin_id": f"plugin-{i}",
            "name": f"Plugin {i}",
            "version": "1.0.0",
            "category": ["Authentication", "Analytics", "Database", "Security"][i % 4],
            "origin": ["vetted", "community"][i % 2],
            "author": "Test Author",
            "description": f"Test plugin {i} with comprehensive description",
            "rating": 4.0 + (i % 5) * 0.1,
            "rating_count": 10 + i,
            "download_count": 100 + i * 10,
        }
        for i in range(num_plugins)
    ]


def random_choice(items: List) -> Any:
    """Randomly choose an item."""
    import random
    return random.choice(items)


# ─── CLI Interface ────────────────────────────────────────────────────────

async def main():
    """Run profiling from command line."""
    import argparse

    parser = argparse.ArgumentParser(description="Plugin Marketplace Performance Profiler")
    parser.add_argument("--profile", choices=["cpu", "memory", "all"], default="all",
                       help="Type of profiling to run")
    parser.add_argument("--workload", choices=["discovery", "search", "concurrent_installs", "sustained_load"],
                       default="discovery", help="Workload to profile")
    parser.add_argument("--output", type=Path, default=Path("profiling_report.json"),
                       help="Output file for profiling report")

    args = parser.parse_args()

    runner = ProfilingRunner(args.workload)

    # Select workload
    workload_map = {
        "discovery": (Workloads.discovery_workload, {"num_requests": 1000}),
        "search": (Workloads.search_workload, {"num_searches": 500}),
        "concurrent_installs": (Workloads.concurrent_installs, {"num_installs": 50}),
        "sustained_load": (Workloads.sustained_load, {"duration_sec": 30, "target_rps": 50}),
    }

    workload_func, workload_args = workload_map[args.workload]

    # Run profiling
    report = await runner.run_workload(workload_func, **workload_args)

    # Print and save results
    print_profiling_report(report)
    save_profiling_report(report, args.output)

    logger.info(f"Report saved to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
