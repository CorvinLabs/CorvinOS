# Performance SLOs — Tenant-Native Skills v0.3

**Status:** Specification (Phase 0.3)  

---

## SLO Targets

| Component | Operation | SLO | Measurement |
|-----------|-----------|-----|---|
| **Resolver** | Resolve 1000 skills | <100ms | time to resolve transitive deps |
| **Exporter** | Export 500 skills | <5min | tarball creation + push start |
| **UI** | Load Skills Page | <2s | page-load to interactive |
| **CLI** | skill list | <1s | output all skills |
| **CLI** | skill-sync --push | <5min for 500 skills | total time (network dependent) |
| **Audit** | Write event | <10ms | disk write + fsync |
| **Audit** | Daily validation | <100ms for 10k entries | hash-chain validation |

---

## Measurement Methods

```python
# resolver_benchmark.py
@pytest.mark.benchmark
def test_resolver_performance_1000_skills(benchmark):
    """Resolver must handle 1000 skills in <100ms"""
    resolver = SkillDependencyResolver("_default")
    
    # Create 1000 test skills (no deps for worst-case)
    for i in range(1000):
        create_test_skill(f"skill_{i}")
    
    # Benchmark
    result = benchmark(resolver.resolve, "skill_500")
    assert result.duration < 100  # ms

# exporter_benchmark.py
@pytest.mark.benchmark
def test_exporter_performance_500_skills(benchmark):
    """Exporter must tarball 500 skills in <5min"""
    exporter = GitHubExporter("github:test/repo", "main")
    
    # Create 500 test skills
    for i in range(500):
        create_test_skill_with_files(f"skill_{i}")
    
    # Benchmark (network call mocked)
    result = benchmark(exporter.export_shared_skills, "_default")
    assert result.duration < 300  # seconds
```

---

## Monitoring

Post-launch monitoring (CloudWatch / Prometheus):

- `skill_resolver_latency_p99` — target <150ms
- `skill_exporter_duration_p99` — target <6min
- `skill_ui_page_load_p99` — target <2.5s
- `audit_write_latency_p99` — target <15ms

---

## Thresholds

| Metric | Green | Yellow | Red |
|--------|-------|--------|-----|
| Resolver | <100ms | 100–150ms | >150ms |
| Exporter | <5min | 5–8min | >8min |
| UI | <2s | 2–3s | >3s |
| Audit | <10ms | 10–20ms | >20ms |

---

