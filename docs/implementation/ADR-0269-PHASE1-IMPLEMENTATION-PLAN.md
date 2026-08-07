# ADR-0269 Phase 1 Lite — Implementation Plan

**Scope:** Memory Lookup only (50% effort, 2 weeks)  
**Timeline:** 2026-08-07 → 2026-08-20  
**Owner:** Claude Code (autonomous)  
**Success Gate:** Week 3 (2026-08-20) Decision on Phase 2  

---

## Overview

**What We're Building:**
- Memory Lookup module (search memory files, rank by relevance, confidence score)
- Integration into TaskEngine Phase 5.5 (between Phase 4 Enrich and Phase 5 Delegate)
- Logging for adoption metrics (does agent use memory context?)
- Baseline measurement (current agent success rate)

**What We're NOT Building (Phase 2+):**
- ❌ Approach Synthesis (subjective, defer)
- ❌ Blocker Identification (scope creep, defer)
- ❌ Skill Injection (premature, defer)
- ❌ Graph Traversal (use existing Classifier graphs, don't re-walk)

---

## Week 1: Memory Lookup Implementation (2026-08-07 → 2026-08-13)

### Day 1 (Wednesday, Aug 7)

**Goal:** Skeleton + tests structure

**Files to Create:**
```
operator/context_engineering/
├── __init__.py                  (public API)
├── memory_lookup.py             (core implementation)
├── rich_task_brief.py           (RichTaskBrief struct)
└── tests/
    ├── __init__.py
    ├── test_memory_lookup.py    (unit tests)
    └── conftest.py              (fixtures)
```

**Tasks:**

1. **Create `operator/context_engineering/__init__.py`** (50 LoC)
   ```python
   from .memory_lookup import MemoryLookup
   from .rich_task_brief import RichTaskBrief, MemoryMatch
   
   __all__ = ["MemoryLookup", "RichTaskBrief", "MemoryMatch"]
   __version__ = "0.1.0"
   ```

2. **Create `operator/context_engineering/rich_task_brief.py`** (80 LoC)
   - `@dataclass MemoryMatch`: match, relevance_score (0.0-1.0), source_file, timestamp
   - `@dataclass RichTaskBrief`: enriched_task, memory_context, timestamp, version

3. **Create `operator/context_engineering/memory_lookup.py`** (skeleton, 100 LoC)
   - `class MemoryLookup`
   - `def search(keywords: List[str]) -> List[MemoryMatch]` (stub)
   - `def rank(matches: List[MemoryMatch]) -> List[MemoryMatch]` (stub)

4. **Create `operator/context_engineering/tests/conftest.py`** (50 LoC)
   - Fixture: `memory_dir` (temp directory with sample memory files)
   - Fixture: `lookupengine` (MemoryLookup instance)

5. **Create initial test file** `test_memory_lookup.py` (80 LoC)
   - Test: `test_memory_lookup_finds_files`
   - Test: `test_memory_lookup_ranks_by_relevance`
   - Test: `test_memory_lookup_returns_confidence_scores`

**Success Criteria:**
- ✅ All 5 files created
- ✅ 3 tests written (not yet passing, OK)
- ✅ Project structure clear
- ✅ Commit: "feat(context_engineering): scaffold Phase 1 Lite"

---

### Day 2 (Thursday, Aug 8)

**Goal:** Memory search implementation

**Tasks:**

1. **Implement `MemoryLookup.search()`** (150 LoC)
   ```python
   def search(self, keywords: List[str], max_results: int = 10) -> List[MemoryMatch]:
       """Search memory files by keywords."""
       # 1. Scan memory directory (~/.claude/projects/CorvinOS/memory/)
       # 2. For each .md file:
       #    - Read content
       #    - Extract title, description, metadata
       #    - Calculate relevance score (TF-IDF simplified: keyword match %)
       # 3. Filter: score >= 0.3
       # 4. Deduplicate: if same file matches twice, keep highest score
       # 5. Return top max_results, sorted by score descending
   ```

2. **Implement relevance scoring** (100 LoC)
   - Simple TF-IDF: keyword in title = 2x weight, keyword in body = 1x weight
   - Score clamped to [0.0, 1.0]
   - Penalize: if file timestamp > 30 days old (decay to 0.7x score)

3. **Add caching** (50 LoC)
   ```python
   # LRU cache: {query_hash: (results, timestamp)}
   # TTL: 30 minutes
   # Invalidate on memory directory change
   ```

4. **Write tests** (80 LoC)
   - Test: `test_search_finds_relevant_memory_files`
   - Test: `test_search_returns_confidence_scores`
   - Test: `test_search_caches_results`
   - Test: `test_search_penalizes_old_files`

**Success Criteria:**
- ✅ MemoryLookup.search() working
- ✅ Confidence scores in [0.0, 1.0]
- ✅ 4 tests passing
- ✅ Cache working
- ✅ Commit: "feat(context_engineering): implement memory search"

---

### Day 3 (Friday, Aug 9)

**Goal:** Ranking + RichTaskBrief integration

**Tasks:**

1. **Implement `MemoryLookup.rank()`** (80 LoC)
   ```python
   def rank(self, matches: List[MemoryMatch]) -> List[MemoryMatch]:
       """Re-rank matches by recency + relevance."""
       # Sort by: score * decay_factor(age)
       # Stable sort: ties broken by file name (consistency)
   ```

2. **Implement `MemoryLookup.enrich_task()`** (100 LoC)
   ```python
   def enrich_task(self, enriched_task: EnrichedTask) -> RichTaskBrief:
       """Transform EnrichedTask → RichTaskBrief with memory context."""
       keywords = extract_keywords(enriched_task)
       matches = self.search(keywords, max_results=5)
       ranked = self.rank(matches)
       
       return RichTaskBrief(
           enriched_task=enriched_task,
           memory_context=MemoryContext(
               matches=ranked,
               search_queries=keywords,
               confidence=mean([m.relevance_score for m in ranked])
           ),
           timestamp=datetime.now(),
           version="0.1"
       )
   ```

3. **Write integration tests** (120 LoC)
   - Test: `test_enrich_task_returns_rich_brief`
   - Test: `test_enrich_task_extracts_keywords`
   - Test: `test_enrich_task_confidence_score_calculated`
   - Test: `test_enrich_task_empty_memory_graceful`

**Success Criteria:**
- ✅ MemoryLookup.enrich_task() working
- ✅ RichTaskBrief struct valid
- ✅ 4 integration tests passing
- ✅ Commit: "feat(context_engineering): add ranking + RichTaskBrief"

---

### Day 4 (Saturday, Aug 10)

**Goal:** TaskEngine integration + logging

**Tasks:**

1. **Update `operator/task_analysis/engine.py`** (50 LoC)
   ```python
   # Add import
   from operator.context_engineering import MemoryLookup
   
   class TaskEngine:
       def __init__(self, ..., enable_context_engineering: bool = True):
           ...
           self.cel = MemoryLookup() if enable_context_engineering else None
       
       def route_task(self, raw_task: str) -> TaskResult:
           ...
           enriched = self.enricher.enrich(validated)
           
           # Phase 5.5: Context Enrichment (new)
           rich_brief = None
           if self.cel:
               rich_brief = self.cel.enrich_task(enriched)
               self.metrics.record_memory_confidence(rich_brief.memory_context.confidence)
           
           decision = self.router.route(enriched)
           
           return TaskResult(
               decision=decision,
               rich_task_brief=rich_brief
           )
   ```

2. **Add logging** (100 LoC)
   ```python
   # operator/context_engineering/logging.py
   - Log: memory search queries
   - Log: number of matches found
   - Log: avg confidence score
   - Log: memory_lookup latency (ms)
   - Aggregate to: logs/staging/context_engineering.log
   ```

3. **Add Prometheus metrics** (80 LoC)
   ```python
   # operator/context_engineering/metrics.py
   cel_search_latency_seconds (histogram)
   cel_matches_found (counter)
   cel_avg_confidence (gauge)
   cel_cache_hit_rate (gauge)
   ```

4. **Write E2E test** (100 LoC)
   - Test: `test_taskengine_integrates_cel`
   - Verify: RichTaskBrief in result
   - Verify: Latency < 1s total

**Success Criteria:**
- ✅ TaskEngine accepts rich_brief
- ✅ Logging working
- ✅ Prometheus metrics registered
- ✅ E2E test passing
- ✅ Commit: "feat(context_engineering): integrate into TaskEngine"

---

### Day 5 (Sunday, Aug 11)

**Goal:** Performance + edge cases

**Tasks:**

1. **Performance profiling** (80 LoC in test)
   ```python
   # benchmark_memory_lookup.py
   - Measure: search latency for 1, 5, 10, 50 queries
   - Target: P95 < 200ms
   - Report: results + bottlenecks
   ```

2. **Edge cases** (100 LoC tests)
   - Empty memory directory
   - Malformed memory files (bad YAML frontmatter)
   - Very large memory files (10MB+)
   - Unicode + special characters in memory files
   - Concurrent searches (thread-safety)

3. **Caching edge cases** (50 LoC tests)
   - Cache invalidation when memory dir changes
   - Cache TTL expiry
   - Stale cache detection

**Success Criteria:**
- ✅ P95 latency < 200ms
- ✅ All edge cases handled
- ✅ Thread-safe confirmed
- ✅ Commit: "test(context_engineering): add performance + edge cases"

---

### Day 6 (Monday, Aug 12)

**Goal:** Documentation + code review

**Tasks:**

1. **Write `operator/context_engineering/README.md`** (150 LoC)
   - API overview
   - Usage examples
   - Performance characteristics
   - Known limitations

2. **Code review checklist**
   - [ ] All tests passing (pytest operator/context_engineering/tests/)
   - [ ] Coverage ≥ 85%
   - [ ] No type errors (mypy)
   - [ ] No lint errors (ruff)
   - [ ] Docstrings complete
   - [ ] Edge cases handled
   - [ ] Logging comprehensive

3. **Self-review + fix**
   - Run full suite
   - Address any issues

**Success Criteria:**
- ✅ 100% tests passing
- ✅ Coverage ≥ 85%
- ✅ No type/lint errors
- ✅ Documentation complete
- ✅ Commit: "docs(context_engineering): add README + improve docs"

---

### Day 7 (Tuesday, Aug 13)

**Goal:** Final testing + baseline measurement prep

**Tasks:**

1. **Run full integration test suite** (operator/task_analysis/tests + operator/context_engineering/tests)
   - All 220 existing tests still passing?
   - No regressions?

2. **Prepare baseline measurement**
   - Create script: `scripts/measure_baseline.py`
   - Measure: current agent success rate (without CEL)
   - Run on sample of 50 real tasks
   - Output: baseline_metrics.json

3. **Final documentation**
   - Update `docs/layer-manifest.yaml` to include CEL
   - Update ADR-0269 commits field

4. **Create Week 2 readiness checklist**

**Success Criteria:**
- ✅ All tests passing (no regressions)
- ✅ Baseline metrics collected
- ✅ Code ready for integration
- ✅ Commit: "chore(context_engineering): finalize Phase 1 Lite"

---

## Week 2: Integration + Measurement (2026-08-14 → 2026-08-20)

### Day 8 (Wednesday, Aug 14)

**Goal:** Deploy to staging, enable for all tasks

**Tasks:**

1. **Deploy CEL to staging environment**
   - Update CorvinOS/.env: `CONTEXT_ENGINEERING_ENABLED=true`
   - Restart TaskEngine server

2. **Verify:**
   - TaskEngine health check passes
   - Memory lookup working
   - Prometheus metrics populated

3. **Start collecting data**
   - Every task → RichTaskBrief → logged
   - Metrics: memory_confidence, search_latency, matches_found

**Success Criteria:**
- ✅ CEL live in staging
- ✅ 50 tasks processed through CEL
- ✅ Metrics flowing to Prometheus
- ✅ Commit: "deploy(context_engineering): enable Phase 1 Lite in staging"

---

### Day 9-12 (Thursday Aug 15 — Sunday Aug 18)

**Goal:** Continuous measurement (4 days)

**Tasks (per day):**

1. **Run 50 agent tasks through TaskEngine**
   - Tasks from staging/test_data.json (already prepared)
   - Measure: agent success rate
   - Log: which memory matches did agent find useful?
   - Collect: Prometheus metrics

2. **Daily analysis**
   - CEL latency trend
   - Memory confidence distribution
   - Cache hit rate
   - Any crashes/errors?

3. **Weekly report (accumulate)**
   - Total tasks: 200+ by Day 12
   - Baseline (no CEL): X%
   - With CEL: Y%
   - Improvement: +/- Z%

**Success Criteria:**
- ✅ 200+ tasks measured
- ✅ No crashes
- ✅ Latency acceptable (P95 < 700ms)
- ✅ Memory confidence calibrated
- ✅ Daily reports generated

---

### Day 13 (Monday, Aug 19)

**Goal:** Final analysis + decision gate prep

**Tasks:**

1. **Compile final metrics**
   - Success rate improvement (target ≥ 10%)
   - Latency impact (target P95 < 700ms)
   - Memory quality (high-conf prediction accuracy)
   - Agent adoption (how often used memory context?)

2. **Generate decision brief**
   ```
   Success Criteria | Target | Actual | Pass/Fail
   ---|---|---|---
   Success rate improvement | ≥ 10% | ??? | ✅/❌
   P95 latency | < 700ms | ??? | ✅/❌
   Memory adoption rate | ≥ 60% | ??? | ✅/❌
   High-conf accuracy | ≥ 90% | ??? | ✅/❌
   ```

3. **Prepare recommendation**
   - Phase 2? (YES if all gates pass)
   - Abandon? (YES if ≥ 2 gates fail)
   - Extend pilot? (YES if 1 gate close)

**Success Criteria:**
- ✅ All metrics calculated
- ✅ Decision brief clear
- ✅ Recommendation ready

---

### Day 14 (Tuesday, Aug 20) — DECISION GATE

**Goal:** Make go/no-go decision for Phase 2

**Decision Logic:**

```python
if success_rate_improvement >= 0.10 and \
   latency_p95 < 700 and \
   memory_adoption_rate >= 0.60 and \
   high_conf_accuracy >= 0.90:
    print("🟢 PHASE 1 LITE SUCCESS — Proceed to Phase 2 (Skills)")
    
elif success_rate_improvement >= 0.05 and \
     all_other_gates_pass:
    print("🟡 MARGINAL SUCCESS — Extend pilot 1 week, investigate")
    
else:
    print("🔴 PHASE 1 LITE FAILED — Abandon ADR-0269")
    print("Lessons learned:")
    print("- Memory lookup did not improve agent success")
    print("- Document findings in ADR-0269 Operator Notes")
    print("- Archive CEL code (don't delete)")
```

**Outputs:**

- **If 🟢 (success):**
  - ✅ Approve Phase 2 (Skills Injection)
  - ✅ Update ADR-0269 status: "in-progress"
  - ✅ Commit: "adr(0269): Phase 1 Lite SUCCESS, proceeding to Phase 2"
  - ✅ Start Phase 2 planning (Week 3+)

- **If 🟡 (marginal):**
  - ⏳ Extend pilot 1 week (Aug 21-27)
  - Investigate: why did memory not help more?
  - Adjust: confidence thresholds? memory quality?
  - Re-measure

- **If 🔴 (failure):**
  - ❌ Abandon ADR-0269
  - Document: "Memory lookup did not improve agent success"
  - Archive CEL code in branch
  - Add to ADR-0269 Operator Notes: findings + learnings
  - Commit: "adr(0269): Phase 1 Lite FAILED, lessons documented"

---

## Testing Strategy

### Unit Tests (Day 1-5)
- MemoryLookup search, rank, enrich
- RichTaskBrief struct validation
- Edge cases (empty, malformed, concurrent)
- Caching (hit/miss, TTL expiry)

### Integration Tests (Day 4-5)
- TaskEngine + CEL integration
- Full pipeline: Task → Normalize → Classify → Filter → Validate → Enrich → **CEL** → Delegate

### Performance Tests (Day 5)
- Latency: P95 < 200ms memory search
- Memory: no leaks (cache size bounded)
- Concurrency: thread-safe

### End-to-End Tests (Week 2)
- 50+ real tasks through CEL
- Agent success rate measured
- Memory confidence calibrated

---

## File Checklist

### New Files (Week 1)
```
operator/context_engineering/
├── __init__.py                              (50 LoC)
├── memory_lookup.py                         (400 LoC)
├── rich_task_brief.py                       (100 LoC)
├── logging.py                               (80 LoC)
├── metrics.py                               (80 LoC)
├── README.md                                (150 LoC)
└── tests/
    ├── __init__.py                          (5 LoC)
    ├── conftest.py                          (60 LoC)
    ├── test_memory_lookup.py                (400 LoC)
    ├── test_rich_task_brief.py              (100 LoC)
    ├── test_integration.py                  (150 LoC)
    └── benchmark_memory_lookup.py           (80 LoC)
```

### Modified Files
```
operator/task_analysis/engine.py             (+50 LoC)
operator/task_analysis/__init__.py           (+5 exports)
docs/layer-manifest.yaml                     (+CEL entry)
```

### Scripts (Week 2)
```
scripts/measure_baseline.py                  (100 LoC)
scripts/measure_with_cel.py                  (100 LoC)
```

---

## Commits (Daily)

| Day | Commit | LoC |
|-----|--------|-----|
| 1 | `feat(context_engineering): scaffold Phase 1 Lite` | +350 |
| 2 | `feat(context_engineering): implement memory search` | +400 |
| 3 | `feat(context_engineering): add ranking + RichTaskBrief` | +350 |
| 4 | `feat(context_engineering): integrate into TaskEngine` | +350 |
| 5 | `test(context_engineering): add performance + edge cases` | +300 |
| 6 | `docs(context_engineering): add README + improve docs` | +250 |
| 7 | `chore(context_engineering): finalize Phase 1 Lite` | +100 |
| 8 | `deploy(context_engineering): enable Phase 1 Lite in staging` | +50 |
| 14 | Decision commit (success/failure) | ±5 |

**Total Phase 1 Lite:** ~2100 LoC (tests + source)

---

## Success Criteria Summary

### Week 1 (Dev)
- ✅ All tests passing (220+ existing + 300+ new)
- ✅ Coverage ≥ 85%
- ✅ No type/lint errors
- ✅ Code review clean
- ✅ Baseline metrics collected

### Week 2 (Measurement)
- ✅ 200+ tasks measured
- ✅ No regressions (latency acceptable)
- ✅ Memory confidence calibrated
- ✅ Decision gate criteria clear

### Decision Gate (Day 14)
- ✅ Success rate improvement ≥ 10% → Phase 2
- ⚠️ 5–10% improvement → Extend pilot
- ❌ < 5% improvement → Abandon

---

## Rollback Plan

**If things go wrong (Day 8+):**

1. **Disable CEL** (immediate):
   ```bash
   export CONTEXT_ENGINEERING_ENABLED=false
   systemctl restart taskengine-server
   ```

2. **Revert code** (if needed):
   ```bash
   git revert commits-from-week1
   ```

3. **Post-mortem:**
   - Document what failed
   - Why did it fail?
   - Add learnings to ADR-0269 Operator Notes

**RTO (Recovery Time Objective):** < 5 minutes (just flip feature flag)

---

## Success Looks Like

**Day 1:** ✅ Code structure in place, tests running  
**Day 4:** ✅ CEL integrated, logging working  
**Day 7:** ✅ All tests passing, baseline measured  
**Day 8:** ✅ CEL live in staging, metrics flowing  
**Day 14:** 🎯 Decision gate: Phase 2 approval or graceful exit  

---

## Owner + Handoff

**Author:** Claude Code (autonomous)  
**Measurement:** Autonomous Agent + Human Review  
**Decision:** User + Claude consensus (Day 14)  

