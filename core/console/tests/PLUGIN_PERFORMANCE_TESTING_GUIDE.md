# Plugin Marketplace Performance Testing Guide (ADR-0249 Phase 4+)

## Overview

This guide provides comprehensive performance testing for the CorvinOS plugin marketplace, covering baseline measurements, load testing, stress testing, regression detection, and SLO verification.

## Test Suite Components

### 1. Baseline Latency Tests (`test_plugin_marketplace_performance.py`)

Measure latency for key operations under controlled conditions.

#### Endpoints Tested
- `GET /v1/vibe/plugins/marketplace` — Discovery/browsing
- `GET /v1/vibe/plugins/list` — List installed plugins
- `GET /v1/console/plugins/governance` — Governance UI
- `POST /v1/console/plugins/upload` — Plugin installation
- Search/filter operations with various queries
- Pagination across result sets

#### Running Baseline Tests

```bash
# Run all baseline tests
pytest test_plugin_marketplace_performance.py::TestBaselineLatency -v -s

# Run specific test
pytest test_plugin_marketplace_performance.py::TestBaselineLatency::test_marketplace_latency_by_catalog_size -v -s

# Run with coverage
pytest test_plugin_marketplace_performance.py --cov=corvin_console.routes.vibe_plugins_api -v -s
```

#### Expected Results

| Endpoint | Catalog Size | Target | Actual | Status |
|----------|--------------|--------|--------|--------|
| `/v1/vibe/plugins/marketplace` | 10 | <50ms | — | |
| `/v1/vibe/plugins/marketplace` | 100 | <100ms | — | |
| `/v1/vibe/plugins/marketplace` | 1000 | <200ms | — | |
| `/v1/vibe/plugins/list` | — | <50ms | — | |
| `/v1/console/plugins/governance` | — | <100ms | — | |
| Search (text + filter) | — | <200ms | — | |
| Pagination (offset +500) | — | <150ms | — | |
| Upload (1MB) | — | <5s | — | |
| Upload (10MB) | — | <15s | — | |
| Upload (50MB) | — | <60s | — | |

### 2. Load Testing (`test_plugin_marketplace_performance.py`)

Simulate concurrent users under realistic workloads.

#### Running Load Tests

```bash
# 10 concurrent users browsing marketplace
pytest test_plugin_marketplace_performance.py::TestLoadTesting::test_load_marketplace_concurrent_users -v -s

# 10 concurrent installations
pytest test_plugin_marketplace_performance.py::TestLoadTesting::test_load_concurrent_installations -v -s

# 10 concurrent searches
pytest test_plugin_marketplace_performance.py::TestLoadTesting::test_load_concurrent_searches -v -s

# All load tests
pytest test_plugin_marketplace_performance.py::TestLoadTesting -v -s -k "load"
```

#### Concurrent User Scenarios

- **Marketplace Browse** (10 users × 20 requests): Simulates users browsing marketplace
  - Mean latency: <200ms
  - Throughput: >10 req/s

- **Plugin Installation** (10 concurrent × 3 installs): Simulates concurrent uploads
  - Mean latency: <60s per install
  - Throughput: >0.5 installs/s

- **Search Sessions** (10 users × 15 searches): Simulates search-heavy users
  - Mean latency: <250ms
  - Throughput: >30 req/s

### 3. Stress Testing (`test_plugin_marketplace_performance.py`)

Test behavior at peak loads and limits.

#### Running Stress Tests

```bash
# Peak load: 100 concurrent users
pytest test_plugin_marketplace_performance.py::TestStressTesting::test_stress_peak_load_100_users -v -s

# Sustained load: 30min @ 10 req/s
pytest test_plugin_marketplace_performance.py::TestStressTesting::test_stress_sustained_load_30min -v -s

# All stress tests
pytest test_plugin_marketplace_performance.py::TestStressTesting -v -s
```

#### Stress Scenarios

- **Peak Load (100 users, 5 req each)**: Burst traffic from many concurrent users
  - Max mean latency: 500ms
  - Error rate: <5%

- **Sustained Load (10 req/s × 30s)**: Continuous traffic pattern
  - Error rate: <5%
  - Mean latency: <300ms
  - Consistent throughput

### 4. Locust Load Testing (`locustfile_plugin_marketplace.py`)

Continuous load testing with realistic user behavior patterns.

#### Installation

```bash
# Install locust
pip install locust

# Or from requirements
pip install -r requirements-perf-testing.txt
```

#### Running Locust Tests

```bash
# Start Locust web UI (http://localhost:8089)
locust -f locustfile_plugin_marketplace.py --host=http://localhost:8765

# Headless mode: 10 concurrent users, 1 user spawned per second, 30 seconds
locust -f locustfile_plugin_marketplace.py --host=http://localhost:8765 \
  -u 10 -r 1 -t 30s --headless

# Peak test: 100 concurrent users, spawn 5/sec, 5 minute test
locust -f locustfile_plugin_marketplace.py --host=http://localhost:8765 \
  -u 100 -r 5 -t 5m --headless -c 100

# Long-running: 50 users sustained for 15 minutes
locust -f locustfile_plugin_marketplace.py --host=http://localhost:8765 \
  -u 50 -r 2 -t 15m --headless
```

#### Locust User Behavior

The simulated user performs:
- 30% marketplace browsing
- 20% searching
- 20% category filtering
- 15% pagination
- 10% plugin installation
- 5% list installed plugins

#### Interpreting Locust Results

- **Response Times**: Check p95/p99 for tail latencies
- **Failure Rate**: Should be <1% for normal load
- **RPS**: Peak requests per second
- **Charts**: Export for trends and comparisons

### 5. Performance Profiling (`plugin_performance_profiler.py`)

CPU and memory profiling for hot path analysis.

#### Installation

```bash
pip install memory-profiler psutil
```

#### Running Profiler

```bash
# CPU profiling for discovery workload (100 requests)
python plugin_performance_profiler.py --profile=cpu --workload=discovery

# Memory profiling for concurrent installations
python plugin_performance_profiler.py --profile=memory --workload=concurrent_installs

# Full profiling (CPU + memory + GC) for sustained load
python plugin_performance_profiler.py --profile=all --workload=sustained_load

# Save to custom output file
python plugin_performance_profiler.py --profile=all --workload=discovery \
  --output=/tmp/profile_discovery.json
```

#### Workload Options

- `discovery` — Simulate marketplace browsing (1000 requests)
- `search` — Simulate searching (500 searches)
- `concurrent_installs` — Simulate 10 concurrent installations
- `sustained_load` — Sustained 10 req/s for 30 seconds

#### Profiling Output

Reports include:
- **CPU**: Top 10 functions by cumulative time
- **Memory**: Peak memory, growth, top allocation sites
- **GC**: Collection counts per generation
- **Recommendations**: Optimization opportunities

### 6. Regression Detection (`test_plugin_marketplace_performance.py`)

Compare against Phase 2 baseline to detect regressions.

```bash
# Run regression tests
pytest test_plugin_marketplace_performance.py::TestRegressionDetection -v -s
```

#### Baseline Comparison

- Phase 2 baseline (discovery): 45ms mean, 75ms p95, 95ms p99
- Regression threshold: 20% increase
- Alert on: Mean >54ms, p95 >90ms, p99 >114ms

### 7. SLO Verification (`test_plugin_marketplace_performance.py`)

Verify Service Level Objectives are met.

```bash
# Run all SLO tests
pytest test_plugin_marketplace_performance.py::TestSLOVerification -v -s

# Run specific SLO
pytest test_plugin_marketplace_performance.py::TestSLOVerification::test_slo_marketplace_discovery_latency -v -s
```

#### SLO Targets

| Metric | Target | Measured | Status |
|--------|--------|----------|--------|
| Marketplace Discovery (mean) | <50ms | — | |
| Marketplace Discovery (p95) | <100ms | — | |
| Marketplace Discovery (p99) | <150ms | — | |
| Search (p95) | <200ms | — | |
| Plugin List (mean) | <30ms | — | |
| Installation Flow (max) | <30s | — | |
| Registry Operations (mean) | <50ms | — | |
| Error Rate | <1% | — | |

## Running Complete Performance Test Suite

### Quick Test (5 minutes)

```bash
# Baseline + load + stress + SLO verification
pytest test_plugin_marketplace_performance.py \
  -k "baseline or (load and not sustained)" \
  -v -s
```

### Full Test (30+ minutes)

```bash
# All tests including sustained stress test
pytest test_plugin_marketplace_performance.py -v -s --tb=short
```

### Performance-Focused Pipeline

```bash
#!/bin/bash
set -e

echo "=== Phase 1: Baseline Latencies ==="
pytest test_plugin_marketplace_performance.py::TestBaselineLatency -v -s

echo "=== Phase 2: Load Testing ==="
pytest test_plugin_marketplace_performance.py::TestLoadTesting -v -s

echo "=== Phase 3: Stress Testing ==="
pytest test_plugin_marketplace_performance.py::TestStressTesting -v -s

echo "=== Phase 4: SLO Verification ==="
pytest test_plugin_marketplace_performance.py::TestSLOVerification -v -s

echo "=== Phase 5: Regression Detection ==="
pytest test_plugin_marketplace_performance.py::TestRegressionDetection -v -s

echo "=== Phase 6: Profiling ==="
python plugin_performance_profiler.py --profile=all --workload=discovery

echo "=== Phase 7: Locust Load Test ==="
locust -f locustfile_plugin_marketplace.py --host=http://localhost:8765 \
  -u 50 -r 5 -t 5m --headless -c 100

echo "All tests completed!"
```

## Analyzing Results

### Performance Report Template

```markdown
# Performance Test Results — [Date]

## Summary
- Test Duration: XXXs
- Total Requests: XXX
- Successful: XXX (XX%)
- Failed: XX (X%)
- Peak Throughput: XX req/s

## Baseline Latencies
| Endpoint | Mean | p95 | p99 | Target | Status |
|----------|------|-----|-----|--------|--------|
| Discovery | XXms | XXms | XXms | <100ms | ✓ PASS |
| Search | XXms | XXms | XXms | <200ms | ✓ PASS |
| List | XXms | XXms | XXms | <50ms | ✓ PASS |

## Load Test Results
| Scenario | Users | Mean | p95 | Error % | Status |
|----------|-------|------|-----|---------|--------|
| Browse | 10 | XXms | XXms | X% | ✓ PASS |
| Install | 10 | XXs | XXs | X% | ✓ PASS |
| Search | 10 | XXms | XXms | X% | ✓ PASS |

## Stress Test Results
| Scenario | Peak Load | Duration | Mean | Error % | Status |
|----------|-----------|----------|------|---------|--------|
| Peak 100 users | 100 | 30s | XXms | X% | ✓ PASS |
| Sustained 30min | 10 req/s | 30s | XXms | X% | ✓ PASS |

## SLO Verification
- Marketplace Discovery: ✓ PASS (XXms < 100ms)
- Search: ✓ PASS (XXms < 200ms)
- Installation: ✓ PASS (XXs < 30s)
- All endpoints: ✓ PASS

## Regression Analysis
- vs Phase 2: ✓ No regression detected
- Mean increase: X.X% (target: <20%)
- p95 increase: X.X% (target: <20%)

## Profiling Insights
- CPU hotspots: [list top functions]
- Memory peak: XX MB
- GC collections: XX
- Optimization recommendations: [list]

## Recommendations
1. [Action item]
2. [Action item]
3. [Action item]

## Next Steps
- [ ] Share results with team
- [ ] Create ADR for optimization decisions
- [ ] Schedule follow-up test
```

## Best Practices

1. **Run in Isolated Environment**: Minimize external load
2. **Warm Up**: Run requests before measuring (JIT compilation, cache warmup)
3. **Multiple Runs**: Execute tests 3+ times for consistency
4. **Compare Baselines**: Always compare against known good baseline
5. **Monitor System**: Watch CPU, memory, disk I/O during tests
6. **Log Details**: Capture timing, error messages, GC pauses
7. **Analyze Tail Latencies**: p95/p99 matter more than mean
8. **Document Deviations**: Note anomalies and investigate

## Troubleshooting

### Test Failures

#### "Mean latency exceeds target"
- Check system load (`top`, `iostat`)
- Run again in isolation
- Verify no background services interfering
- Check code for recent changes

#### "High error rate"
- Review logs for error messages
- Check network connectivity
- Verify server health
- Run with fewer concurrent users

#### "OOM or high memory usage"
- Reduce concurrent user count
- Check for memory leaks in code
- Review garbage collection stats
- Profile with `memory_profiler`

#### "Timeout errors"
- Increase timeout threshold
- Reduce payload size
- Check server resources
- Profile hot paths with cProfile

## Performance Optimization Workflow

1. **Measure** — Run baseline tests to identify bottlenecks
2. **Profile** — Use profiler to find hot spots
3. **Hypothesize** — Propose optimization
4. **Implement** — Make changes with minimal scope
5. **Validate** — Run tests again to verify improvement
6. **Regression Test** — Ensure no new regressions
7. **Document** — Record ADR and results

## Integration with CI/CD

### GitHub Actions Workflow

```yaml
name: Performance Tests
on: [push, pull_request]

jobs:
  performance:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          pip install pytest pytest-asyncio locust memory-profiler
      
      - name: Run baseline latency tests
        run: |
          pytest test_plugin_marketplace_performance.py::TestBaselineLatency -v
      
      - name: Run SLO tests
        run: |
          pytest test_plugin_marketplace_performance.py::TestSLOVerification -v
      
      - name: Upload results
        if: always()
        uses: actions/upload-artifact@v3
        with:
          name: performance-results
          path: test-results/
```

## Related Documentation

- ADR-0249: Plugin Trust Anchor and Marketplace
- ADR-0248: Plugin Install Command
- Layer-Plugins: Plugin System Architecture
- compliance-baseline.md: Security and compliance

## References

- pytest documentation: https://docs.pytest.org/
- locust documentation: https://docs.locust.io/
- Python cProfile: https://docs.python.org/3/library/profile.html
- memory_profiler: https://github.com/pythonprofilers/memory_profiler
