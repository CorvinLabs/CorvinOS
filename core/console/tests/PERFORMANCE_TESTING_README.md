# Plugin Marketplace Performance Testing Suite

Comprehensive performance testing for the CorvinOS plugin marketplace (ADR-0249 Phase 4+), including baseline measurements, load testing, stress testing, regression detection, and SLO verification.

## Quick Start

### 1. Install Dependencies

```bash
# Install performance testing requirements
pip install -r requirements-perf-testing.txt
```

### 2. Run Baseline Tests (5 minutes)

```bash
# Run quick baseline latency tests
pytest test_plugin_marketplace_performance.py::TestBaselineLatency -v -s

# Expected output: Latency measurements with SLO compliance
```

### 3. Run Load Tests (10 minutes)

```bash
# Run concurrent user simulations
pytest test_plugin_marketplace_performance.py::TestLoadTesting -v -s
```

### 4. Run Stress Tests (15 minutes)

```bash
# Run stress and sustained load tests
pytest test_plugin_marketplace_performance.py::TestStressTesting -v -s
```

### 5. Run SLO Verification (5 minutes)

```bash
# Verify all SLOs are met
pytest test_plugin_marketplace_performance.py::TestSLOVerification -v -s
```

## Test Files Overview

### Core Performance Tests

#### `test_plugin_marketplace_performance.py` (Main test suite)

Comprehensive pytest-based performance testing with the following classes:

- **TestBaselineLatency** (7 tests)
  - Marketplace discovery with varying catalog sizes (10, 100, 1000 plugins)
  - Search and filter performance
  - Pagination performance across offsets
  - List/governance endpoints
  - Plugin upload latency by file size (1MB, 10MB, 50MB)

- **TestLoadTesting** (3 tests)
  - 10 concurrent users browsing marketplace
  - 10 concurrent plugin installations
  - 10 concurrent search operations

- **TestStressTesting** (2 tests)
  - Peak load: 100 concurrent users
  - Sustained load: 30s at 10 req/s

- **TestRegressionDetection** (1 test)
  - Compare against Phase 2 baseline
  - Flag regressions >20%

- **TestSLOVerification** (4 tests)
  - Marketplace discovery <100ms
  - Search <200ms
  - Plugin list <50ms
  - Installation flow <30s end-to-end

- **TestPerformanceIntegration** (1 test)
  - Generate comprehensive report

**Total: 18 tests covering 15+ performance scenarios**

### Load Testing Tools

#### `locustfile_plugin_marketplace.py` (Locust-based load testing)

Realistic user behavior simulation with customizable concurrent user count and duration.

**User Tasks:**
- 30% marketplace browsing
- 20% searching
- 20% category filtering
- 15% pagination
- 10% plugin installation
- 5% list installed plugins

**Usage:**
```bash
# Start web UI
locust -f locustfile_plugin_marketplace.py --host=http://localhost:8765

# Headless: 50 users, 5 spawn/sec, 5 minute test
locust -f locustfile_plugin_marketplace.py --host=http://localhost:8765 \
  -u 50 -r 5 -t 5m --headless
```

### Profiling Tools

#### `plugin_performance_profiler.py` (CPU & Memory profiling)

Profile hot paths and memory usage across workloads.

**Workloads:**
- `discovery` — 1000 marketplace requests
- `search` — 500 search operations
- `concurrent_installs` — 50 concurrent installations
- `sustained_load` — 30s sustained @ 10 req/s

**Usage:**
```bash
# CPU profiling for discovery
python plugin_performance_profiler.py --profile=cpu --workload=discovery

# Memory profiling for installations
python plugin_performance_profiler.py --profile=memory --workload=concurrent_installs

# Full profiling (CPU + memory + GC)
python plugin_performance_profiler.py --profile=all --workload=sustained_load
```

### Analysis Tools

#### `analyze_performance_results.py` (Results analysis & comparison)

Analyze results and detect regressions.

**Usage:**
```bash
# Compare baseline vs current
python analyze_performance_results.py \
  --baseline=phase2_baseline_sample.json \
  --current=phase4_results.json

# Generate HTML report
python analyze_performance_results.py \
  --baseline=baseline.json \
  --current=current.json \
  --output=report.html
```

## Test Execution Plans

### Plan A: Quick Smoke Test (5 minutes)
```bash
pytest test_plugin_marketplace_performance.py::TestBaselineLatency::test_marketplace_latency_by_catalog_size -v -s
```

### Plan B: Standard Test Run (20 minutes)
```bash
pytest test_plugin_marketplace_performance.py \
  -k "baseline or (load and not sustained) or (slo)" \
  -v -s --tb=short
```

### Plan C: Full Comprehensive Test (45+ minutes)
```bash
# Run all pytest tests
pytest test_plugin_marketplace_performance.py -v -s

# Run profiling
python plugin_performance_profiler.py --profile=all --workload=discovery

# Run Locust load test
locust -f locustfile_plugin_marketplace.py --host=http://localhost:8765 \
  -u 50 -r 5 -t 5m --headless
```

### Plan D: Regression Detection Pipeline
```bash
# Compare against Phase 2 baseline
pytest test_plugin_marketplace_performance.py::TestRegressionDetection -v -s

# Generate detailed comparison report
python analyze_performance_results.py \
  --baseline=phase2_baseline_sample.json \
  --current=phase4_results.json \
  --output=regression_report.html
```

## Expected Performance Targets

### Baseline Latencies
| Endpoint | Target | p95 | p99 |
|----------|--------|-----|-----|
| Discovery (marketplace) | <50ms | <100ms | <150ms |
| Search | <100ms | <200ms | <250ms |
| List plugins | <30ms | <50ms | <75ms |
| Governance UI | <60ms | <100ms | <150ms |

### Load Test Targets
| Scenario | Users | Max Latency | Error Rate |
|----------|-------|-------------|-----------|
| Browse | 10 | 200ms | <1% |
| Install | 10 | 60s | <1% |
| Search | 10 | 250ms | <1% |
| Peak (100 users) | 100 | 500ms | <5% |
| Sustained (10 req/s) | — | 300ms | <5% |

### SLO Verification
- Marketplace discovery mean: <50ms ✓
- Marketplace discovery p95: <100ms ✓
- Search p95: <200ms ✓
- Plugin list mean: <30ms ✓
- Installation end-to-end: <30s ✓
- Error rate: <1% ✓

## File Structure

```
tests/
├── test_plugin_marketplace_performance.py    # Main pytest suite (18 tests)
├── locustfile_plugin_marketplace.py          # Locust load testing
├── plugin_performance_profiler.py            # CPU/memory profiling
├── analyze_performance_results.py            # Results analysis
├── PLUGIN_PERFORMANCE_TESTING_GUIDE.md       # Detailed guide (this file)
├── PERFORMANCE_TESTING_README.md             # Quick reference (this file)
├── requirements-perf-testing.txt             # Dependencies
└── phase2_baseline_sample.json               # Example baseline results
```

## Key Metrics Collected

### Latency Metrics
- Mean, median, min, max
- Percentiles: p50, p95, p99
- Standard deviation
- Per-endpoint statistics

### Load Metrics
- Requests per second (RPS)
- Concurrent user count
- Error rate and distribution
- Status code breakdown
- Response time curves

### Resource Metrics
- Peak memory usage
- Memory growth over time
- CPU usage by function
- Garbage collection cycles
- Allocation hotspots

### SLO Metrics
- Discovery latency (mean, p95, p99)
- Search latency (p95, p99)
- Installation flow time
- List operations time
- Error rate compliance

## Interpreting Results

### Baseline Latency Tests
```
marketplace_discovery_by_catalog_size[10 plugins] — PASS
  Mean: 32.5ms (target: <50ms) ✓
  p95: 45.2ms (target: <100ms) ✓
  p99: 58.3ms (target: <150ms) ✓
```

### Load Test Results
```
10 Concurrent Users - Marketplace Browse:
  Total requests: 200
  Duration: 45.3s
  Throughput: 4.4 req/s
  Mean latency: 156.3ms (target: <200ms) ✓
```

### Stress Test Results
```
Stress Test - 100 Concurrent Users (peak burst):
  Total requests: 500
  Peak throughput: 145.2 req/s
  Mean latency: 412.3ms (target: <500ms) ✓
  Error rate: 1.2% (target: <5%) ✓
```

### Regression Detection
```
Regression Detection - Marketplace Discovery:
  Phase 2 baseline: 45.0ms
  Current: 46.2ms
  Change: +2.7% (within 20% threshold) ✓
```

## Common Issues & Troubleshooting

### High Latencies
1. Check system load (`top`, `htop`)
2. Verify no background processes
3. Run test again in isolation
4. Check for recent code changes
5. Review logs for errors

### High Error Rate
1. Verify server is running
2. Check network connectivity
3. Review application logs
4. Test with fewer concurrent users
5. Verify SSL/TLS certificates

### OOM or Memory Leak
1. Reduce concurrent user count
2. Run profiler to identify leaks
3. Review code for resource cleanup
4. Check for circular references
5. Monitor GC behavior

### Timeout Errors
1. Increase timeout threshold
2. Reduce payload size
3. Profile hot paths with cProfile
4. Check server resource availability
5. Review slow query logs

## Integration with CI/CD

### GitHub Actions
See `PLUGIN_PERFORMANCE_TESTING_GUIDE.md` for complete CI/CD integration.

### Local Pre-commit Hook
```bash
#!/bin/bash
# .git/hooks/pre-commit

# Run quick baseline tests before commit
pytest tests/test_plugin_marketplace_performance.py::TestBaselineLatency -q

if [ $? -ne 0 ]; then
  echo "Performance tests failed. Commit aborted."
  exit 1
fi
```

## Performance Optimization Workflow

1. **Measure** — Run baseline tests to identify bottlenecks
2. **Profile** — Use profiler to find hot spots
3. **Analyze** — Examine CPU/memory usage patterns
4. **Hypothesize** — Propose optimization
5. **Implement** — Make targeted changes
6. **Validate** — Rerun tests to verify improvement
7. **Regression Test** — Ensure no new regressions
8. **Document** — Record findings in ADR

## Related Documentation

- **ADR-0249** — Plugin Trust Anchor and Marketplace Discovery
- **ADR-0248** — Plugin Install Command
- **Layer-Plugins** — Plugin System Architecture
- **PLUGIN_PERFORMANCE_TESTING_GUIDE.md** — Detailed reference

## References

- [pytest documentation](https://docs.pytest.org/)
- [locust documentation](https://docs.locust.io/)
- [Python cProfile](https://docs.python.org/3/library/profile.html)
- [memory_profiler](https://github.com/pythonprofilers/memory_profiler)
- [FastAPI performance](https://fastapi.tiangolo.com/deployment/concepts/#performance)

## Contributing

When adding new performance tests:

1. Follow naming convention: `test_<component>_<scenario>`
2. Include docstring with test purpose and expected results
3. Use parametrized tests for multiple scenarios
4. Log meaningful metrics for analysis
5. Update this README with new tests
6. Add to CI/CD pipeline

## Support

For issues or questions:
1. Check this README and PLUGIN_PERFORMANCE_TESTING_GUIDE.md
2. Review test logs for error messages
3. Run profiler to identify bottlenecks
4. Create issue with: test name, environment, results
5. Include: `pytest --version`, Python version, OS

---

**Last Updated:** 2026-08-29  
**Maintainer:** Claude Code (Anthropic)  
**Status:** Production-Ready ✓
