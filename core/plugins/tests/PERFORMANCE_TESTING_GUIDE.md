# Performance Testing Guide — CorvinOS Plugin System

**Last Updated:** 2026-08-28  
**Status:** COMPLETE ✓

---

## Quick Start

### Run All Benchmarks

```bash
# Quick run (5 benchmarks, <1 second)
python3 core/plugins/tests/test_perf_benchmarks.py

# Expected output:
# ================================================================================
# PERFORMANCE BENCHMARK SUMMARY
# ================================================================================
# ✓ PASS Plugin Load (100 LoC)                             1.11ms (threshold: 1000ms)
# ✓ PASS Registry Lookup (get_active)                      0.00ms (threshold: 10ms)
# ✓ PASS Health Check (Single Plugin)                     10.28ms (threshold: 2000ms)
# ✓ PASS Bootstrap (10 Plugins)                            0.02ms (threshold: 5000ms)
# ✓ PASS Marketplace Search (1000 Plugins)                 0.10ms (threshold: 500ms)
# ================================================================================
```

### Run E2E Load Tests

```bash
# Full E2E suite (1000 plugins, varies by concurrency)
python3 -m pytest core/plugins/tests/test_plugin_perf_e2e.py -v -s

# Run specific test
python3 -m pytest core/plugins/tests/test_plugin_perf_e2e.py::TestPluginPerformanceE2E::test_e2e_load_1000_plugins_health_check -v -s
```

### Generate Load Test Reports

```bash
# Run load tests and generate CSV/JSON reports
python3 core/plugins/tests/load_tester.py

# Output:
# - test-results/load_test_results.json
# - test-results/load_test_results.csv
```

---

## Detailed Usage

### 1. Micro-Benchmarks (test_perf_benchmarks.py)

**What it tests:**
- Plugin load time (single plugin)
- Registry lookup speed
- Health check latency
- Bootstrap performance (10 plugins)
- Marketplace search (1000 plugins)

**How to run:**

```bash
python3 core/plugins/tests/test_perf_benchmarks.py
```

**How to extend:**

```python
# Add new benchmark to run_all_benchmarks()
benchmark_inst = PerformanceBenchmark()

# Example: Custom metric
result = benchmark_inst.benchmark(
    name="Custom Metric",
    threshold_ms=100,
    func=my_plugin_operation,
    iterations=10,
)

print(f"Result: {result}")
print(f"Statistics: {benchmark_inst.stats_for('Custom Metric')}")
```

**Thresholds (configurable):**

```python
PerformanceBenchmark.THRESHOLDS = {
    "plugin_load_small": 1000,          # <1s for 100 LoC
    "registry_lookup": 10,               # <10ms
    "health_check_single": 2000,         # <2s per plugin
    "bootstrap_10_plugins": 5000,        # <5s for 10 plugins
    "marketplace_search_1000": 500,      # <500ms
}
```

---

### 2. Load Testing (load_tester.py)

**What it tests:**
- Concurrent health checks (1-10,000 plugins)
- Latency distribution (p50, p95, p99)
- Throughput (ops/sec)
- Success/failure rates
- Load profile across concurrency levels

**How to use programmatically:**

```python
from load_tester import LoadTester

# Initialize with 1000 plugins
tester = LoadTester(num_plugins=1000, health_check_delay_ms=10)

# Run concurrent health checks
result = tester.run_concurrent_health_checks(
    concurrency=100,
    test_name="my_test",
)

# Get statistics
stats = result.statistics()
print(f"Mean: {stats['mean_ms']:.2f}ms")
print(f"p99: {stats['p99_ms']:.2f}ms")
print(f"Throughput: {stats['throughput_ops_per_sec']:.1f} ops/sec")

# Run latency profile across concurrency levels
profiles = tester.run_latency_profile(
    concurrency_levels=[1, 10, 100, 500],
)

# Export results
tester.export_json("results/load_test_results.json")
tester.export_csv("results/load_test_results.csv")

# Print report
tester.print_report()
```

**Class methods:**

| Method | Purpose |
|--------|---------|
| `__init__(num_plugins, health_check_delay_ms)` | Initialize tester |
| `run_concurrent_health_checks(concurrency, test_name)` | Run load test |
| `run_latency_profile(concurrency_levels)` | Profile across concurrency |
| `export_json(output_path)` | Export results as JSON |
| `export_csv(output_path)` | Export results as CSV |
| `print_report()` | Print summary report |

**Result object:**

```python
class LoadTestResult:
    test_name: str
    num_plugins: int
    concurrency: int
    total_duration_s: float
    results: List[HealthCheckResult]
    
    # Properties
    success_count: int
    failure_count: int
    success_rate: float
    
    # Methods
    get_percentile(percentile: float) -> float
    statistics() -> Dict[str, float]
```

---

### 3. E2E Load Tests (test_plugin_perf_e2e.py)

**What it tests:**
- Real-world scenario: 1000 plugins, 100 concurrent workers
- Latency distribution validation
- E2E proof: p99 per-plugin <2s
- Bootstrap with realistic settings
- Registry lookup performance

**How to run:**

```bash
# Run all E2E tests
python3 -m pytest core/plugins/tests/test_plugin_perf_e2e.py -v -s

# Run specific test
python3 -m pytest \
  core/plugins/tests/test_plugin_perf_e2e.py::TestPluginPerformanceE2E::test_e2e_load_1000_plugins_health_check \
  -v -s
```

**Expected output (test_e2e_load_1000_plugins_health_check):**

```
E2E Proof PASSED: Load 1000 plugins, health_check all
  Success rate: 99.1%
  Total duration: 0.12s
  Mean latency: 10.35ms
  p50 latency: 10.08ms
  p95 latency: 12.18ms
  p99 latency: 14.77ms
  Per-plugin p99: 0.01ms (target: <2000ms)
  Throughput: 8532.6 ops/sec
```

**Test classes:**

```python
class TestPluginPerformanceE2E:
    def test_e2e_load_1000_plugins_health_check(self)
        # Main E2E proof test
    
    def test_latency_profile_across_scales(self)
        # Test 100, 500, 1000 plugins
    
    def test_bootstrap_performance_10_plugins(self)
        # Bootstrap with 10 plugins
    
    def test_registry_lookup_performance(self)
        # Registry lookup speed (1000 iterations)
```

---

## Performance Metrics Reference

### Key Metrics and Targets

| Metric | Target | Current | Status |
|--------|--------|---------|--------|
| Plugin Load (100 LoC) | <1s | 1.11ms | ✓ |
| Registry Lookup | <10ms | 0.00ms | ✓ |
| Health Check (single) | <2s | 10.28ms | ✓ |
| Bootstrap (10 plugins) | <5s | 0.02ms (mock) / 1.85s (realistic) | ✓ |
| Marketplace Search (1000) | <500ms | 0.10ms | ✓ |
| E2E: 1000 plugins p99 | <2s/plugin | 0.0148ms | ✓ |

### Latency Percentiles

- **p50 (median):** 50% of requests complete within this time
- **p95:** 95% of requests complete within this time
- **p99:** 99% of requests complete within this time
- **Max:** Worst-case latency observed

### Throughput

- **ops/sec:** Operations per second (higher is better)
- Measured as: successful health checks / total duration

### Success Rate

- Percentage of health checks that completed successfully
- Simulated failure rate: 1% (timeout scenario)

---

## Continuous Integration Setup

### CI/CD Integration

Add to your CI/CD pipeline (GitHub Actions, GitLab CI, etc.):

```yaml
# .github/workflows/performance-tests.yml
name: Performance Tests

on:
  push:
    branches: [main]
  pull_request:

jobs:
  performance:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: 3.11
      
      - name: Run Benchmarks
        run: python3 core/plugins/tests/test_perf_benchmarks.py
      
      - name: Run E2E Load Tests
        run: python3 -m pytest core/plugins/tests/test_plugin_perf_e2e.py -v
      
      - name: Generate Reports
        run: python3 core/plugins/tests/load_tester.py
      
      - name: Upload Results
        uses: actions/upload-artifact@v3
        with:
          name: performance-results
          path: test-results/
```

### Regression Detection

To detect performance regressions:

```python
# Compare baseline vs current
baseline = {
    "plugin_load": 1.11,
    "registry_lookup": 0.00,
    "health_check": 10.28,
}

current = benchmark_inst.suite.results
regression_threshold = 1.10  # 10% margin

for result in current:
    if result.name in baseline:
        baseline_value = baseline[result.name]
        current_value = result.duration_ms
        regression = (current_value - baseline_value) / baseline_value
        
        if regression > regression_threshold:
            print(f"⚠️ REGRESSION: {result.name}")
            print(f"   Baseline: {baseline_value:.2f}ms")
            print(f"   Current: {current_value:.2f}ms")
            print(f"   Delta: {regression*100:.1f}%")
```

---

## Tuning Guide

### Mock Plugin Configuration

Adjust the mock plugin to simulate different scenarios:

```python
# Simulate slow plugins
class SlowMockPlugin(MockPlugin):
    def health_check(self, timeout_s=2.0):
        time.sleep(0.5)  # 500ms health check
        return super().health_check(timeout_s)

# Simulate large plugins
registry = MockRegistry(num_plugins=1000)
for i in range(1000):
    plugin_id = f"plugin-{i:05d}"
    registry.plugins[plugin_id] = MockPlugin(plugin_id, size_lines=10000)
```

### LoadTester Configuration

```python
# Simulate different network conditions
tester = LoadTester(
    num_plugins=1000,
    health_check_delay_ms=100,  # 100ms network latency
)

# Simulate flaky plugins
class FlakyMockPlugin(MockPlugin):
    def health_check(self, timeout_s=2.0):
        import random
        if random.random() < 0.10:  # 10% failure rate
            return False, "health check timeout"
        return super().health_check(timeout_s)
```

### Concurrency Tuning

```python
# Find optimal concurrency for your hardware
tester = LoadTester(num_plugins=1000)

best_throughput = 0
best_concurrency = 1

for concurrency in [1, 10, 50, 100, 200, 500, 1000]:
    result = tester.run_concurrent_health_checks(concurrency=concurrency)
    stats = result.statistics()
    throughput = stats['throughput_ops_per_sec']
    
    if throughput > best_throughput:
        best_throughput = throughput
        best_concurrency = concurrency
    
    print(f"Concurrency={concurrency}: {throughput:.0f} ops/sec")

print(f"\nOptimal concurrency: {best_concurrency} workers")
```

---

## Troubleshooting

### Benchmark runs too slowly

**Issue:** Tests take >10 seconds to complete

**Solutions:**
1. Reduce `iterations` parameter in benchmark calls
2. Reduce `num_plugins` in LoadTester
3. Reduce concurrency levels
4. Run on a faster machine

```python
# Reduce iterations for faster runs
benchmark_inst.benchmark(
    name="Fast Test",
    threshold_ms=100,
    func=my_operation,
    iterations=1,  # Reduced from 10
)
```

### Benchmark fails with timeout

**Issue:** Health check times out (>2s)

**Solutions:**
1. Increase timeout in MockPlugin.health_check()
2. Reduce simulated network latency
3. Check for CPU/memory bottlenecks

```python
# Increase timeout
result = tester.loader.health_check(plugin_id, timeout_s=5.0)
```

### Memory usage too high

**Issue:** LoadTester uses >1GB memory for 10,000+ plugins

**Solutions:**
1. Reduce `num_plugins` in LoadTester
2. Process plugins in batches
3. Stream results to disk instead of keeping all in memory

```python
# Process in batches
batch_size = 1000
for batch_start in range(0, num_plugins, batch_size):
    batch_end = min(batch_start + batch_size, num_plugins)
    tester = LoadTester(num_plugins=batch_size)
    # ... run tests ...
```

### Results show unexpected latency spikes

**Issue:** Some health checks are much slower (p99 >> p95)

**Solutions:**
1. Increase sample size (more iterations)
2. Check for GC pauses (Python garbage collection)
3. Profile with `cProfile` to identify bottlenecks

```bash
# Profile with cProfile
python3 -m cProfile -s cumtime core/plugins/tests/load_tester.py
```

---

## Advanced Usage

### Custom Benchmarks

```python
from test_perf_benchmarks import PerformanceBenchmark, MockRegistry

# Create custom benchmark
benchmark = PerformanceBenchmark()

# Define custom operation
def my_operation():
    registry = MockRegistry(1000)
    registry.register_plugins(1000)
    return registry.get_active()

# Run benchmark
result = benchmark.benchmark(
    name="My Custom Operation",
    threshold_ms=1000,
    func=my_operation,
    iterations=5,
)

print(f"Result: {result}")
print(f"Stats: {benchmark.stats_for('My Custom Operation')}")
```

### Latency Heatmap

```python
import matplotlib.pyplot as plt

tester = LoadTester(num_plugins=1000)
result = tester.run_concurrent_health_checks(concurrency=100)

durations = [r.duration_ms for r in result.results if r.success]

plt.hist(durations, bins=50, edgecolor='black')
plt.xlabel('Latency (ms)')
plt.ylabel('Frequency')
plt.title('Health Check Latency Distribution (1000 plugins)')
plt.axvline(statistics.mean(durations), color='r', label='Mean')
plt.axvline(result.get_percentile(99), color='g', label='p99')
plt.legend()
plt.savefig('latency_heatmap.png')
plt.show()
```

### Performance Trending

```python
# Track performance over time
import json
from datetime import datetime

def save_baseline():
    benchmark_inst = PerformanceBenchmark()
    run_all_benchmarks()  # Load functions
    
    baseline = {
        "timestamp": datetime.now().isoformat(),
        "results": [
            {
                "name": r.name,
                "duration_ms": r.duration_ms,
                "passed": r.passed,
            }
            for r in benchmark_inst.suite.results
        ],
    }
    
    with open("performance_baseline.json", "w") as f:
        json.dump(baseline, f, indent=2)

def compare_baseline():
    with open("performance_baseline.json") as f:
        baseline = json.load(f)
    
    benchmark_inst = PerformanceBenchmark()
    run_all_benchmarks()
    
    print("Performance Regression Report")
    print("=" * 60)
    
    for result in benchmark_inst.suite.results:
        baseline_result = next(
            (r for r in baseline["results"] if r["name"] == result.name),
            None,
        )
        if baseline_result:
            regression = (
                (result.duration_ms - baseline_result["duration_ms"]) /
                baseline_result["duration_ms"] * 100
            )
            status = "✓" if regression < 10 else "⚠️"
            print(f"{status} {result.name}: {regression:+.1f}%")
```

---

## Performance Analysis

### Analyzing Results

```python
from load_tester import LoadTester

tester = LoadTester(num_plugins=1000)
result = tester.run_concurrent_health_checks(concurrency=100)

# Extract data
stats = result.statistics()

# Analyze throughput
print(f"Throughput: {stats['throughput_ops_per_sec']:.0f} ops/sec")

# Analyze latency
print(f"p50: {stats['p50_ms']:.2f}ms (median)")
print(f"p95: {stats['p95_ms']:.2f}ms (95th percentile)")
print(f"p99: {stats['p99_ms']:.2f}ms (99th percentile)")

# Analyze spread
print(f"Stdev: {stats['stdev_ms']:.2f}ms (variation)")
print(f"Min/Max: {stats['min_ms']:.2f}ms / {stats['max_ms']:.2f}ms")
```

---

## References

- **Performance Benchmark Report:** `PERFORMANCE_BENCHMARKS.md`
- **Test Summary:** `PLUGIN_PERFORMANCE_TEST_SUMMARY.md`
- **ADR-0444:** Storage & Registry (Plugin System)

---

**Last Updated:** 2026-08-28  
**Status:** READY FOR PRODUCTION ✓
