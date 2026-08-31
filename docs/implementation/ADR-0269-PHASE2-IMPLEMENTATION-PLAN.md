# ADR-0269 Phase 2 — Implementation Plan

**Phase:** Graph Traversal + Skill Injection  
**Duration:** 3 weeks (Days 15-35)  
**Owner:** Claude Code (autonomous)  
**Success Gate:** Weekly measurement (120-task loop)

---

## Overview

Phase 2 extends Phase 1's Memory Lookup with:
1. **Graph Traversal** — Find related decisions by walking classifier graphs
2. **Skill Injection** — Embed recommended skills into task context
3. **Measurement** — 120-task weekly loop to validate improvements

**Expected Impact:** 40-60% skill adoption, +5-10% decision quality improvement

---

## Week 3: Graph Traversal (Days 15-21)

### Day 15: Planning + Scaffold (Today)

**Goal:** Design graph traversal algorithm, create skeleton

**Tasks:**
1. Analyze classifier graph structure (what edges exist?)
2. Design traversal algorithm (BFS vs DFS?)
3. Create `GraphTraversal` class skeleton
4. Plan test strategy

**Deliverables:**
- Design document (this plan)
- Class skeleton + docstrings
- Test fixture (mock classifier graphs)

**Success:** Skeleton builds, tests outline complete

---

### Day 16: Algorithm Implementation

**Goal:** Implement graph traversal with ranking

**Tasks:**
1. Implement graph traversal (walk edges)
2. Extract decision nodes from classifier
3. Implement relevance ranking
4. Add caching (same as Phase 1)

**Code Location:** `operator/context_engineering/graph_traversal.py` (300-400 LoC)

**Success:** Algorithm finds related decisions, ranks by relevance

---

### Day 17: Integration + Testing

**Goal:** Wire into TaskEngine, write tests

**Tasks:**
1. Extend `MemoryLookup` with `find_related_decisions()`
2. Update `RichTaskBrief` with `related_decisions` field
3. Integrate into TaskEngine Phase 5.5
4. Write 15+ tests (traversal, ranking, integration)

**Success:** 15/15 tests passing, no regressions on Phase 1

---

### Day 18-19: First Measurement

**Goal:** Measure adoption of graph traversal

**Tasks:**
1. Run 30-task measurement loop (Day 18)
2. Analyze results (Day 19)
3. Document findings

**Expected Output:** day18_metrics.json, day19_metrics.json

**Success:** 100% success rate, < 1 second P95 latency

---

## Week 4: Skill Injection (Days 20-27)

### Day 20-21: Skill Injection Implementation

**Goal:** Embed skills into task context

**Tasks:**
1. Create `SkillInjection` class
2. Map skills to decisions
3. Score skills by relevance + success rate
4. Embed top 3 into RichTaskBrief

**Code Location:** `operator/context_engineering/skill_injection.py` (250-350 LoC)

**Success:** Skills injected, ranked, logged

---

### Day 22-24: Integration + Testing + Measurement

**Goal:** Wire into agent, validate impact

**Tasks:**
1. Integrate into TaskEngine + agent pipeline
2. Write 15+ tests
3. Run 30-task measurement loop (Days 22-23)
4. Analyze adoption (Day 24)

**Success:** Skill adoption ≥ 60%, no latency regression

---

### Day 25-27: Production Readiness

**Goal:** Prepare for staged rollout

**Tasks:**
1. Health checks (like Phase 1 Day 8)
2. Rollback procedures
3. Monitoring dashboards
4. Feature flag configuration

**Success:** Production deployment checklist complete

---

## Week 5: Measurement + Rollout (Days 28-35)

### Days 28-31: Final Measurement + Analysis

**Goal:** Collect full week of data, analyze impact

**Tasks:**
1. Run 120-task measurement loop (Days 28-31, 30 tasks/day)
2. Compile results (Day 32)
3. Decision gate analysis (Day 33)

**Success Criteria:**
- Graph accuracy ≥85%
- Skill adoption ≥60%
- Decision quality +5-10%
- P95 latency <1 second

---

### Days 32-35: Staged Rollout + Monitoring

**Goal:** Deploy to production with gradual adoption

**Tasks:**
1. Day 32: 10% rollout (monitoring)
2. Day 33: 50% rollout (if metrics stable)
3. Day 34-35: 100% rollout + monitoring

**Success:** Full rollout complete, metrics stable

---

## Success Criteria

| Criterion | Target | Measurement Method |
|---|---|---|
| Graph Accuracy | ≥85% | Manual inspection + agent feedback |
| Skill Adoption | ≥60% | Metrics logging (did agent use skill?) |
| Decision Quality | +5-10% | Success rate improvement measurement |
| Latency | <1s P95 | Phase timer recording |
| Tests | 100% passing | Test suite validation |
| Uptime | 99.5%+ | Monitoring during rollout |

---

## Technical Design

### Graph Traversal

```python
class GraphTraversal:
    def find_related_decisions(self, task, depth=2, top_n=3):
        """Walk classifier graphs to find related decisions."""
        # 1. Extract decision nodes from classifier output
        # 2. BFS to depth=2
        # 3. Score by relevance + task similarity
        # 4. Return top 3
        pass
```

### Skill Injection

```python
class SkillInjection:
    def recommend_skills(self, task, related_decisions, top_n=3):
        """Map decisions to skills, rank by relevance."""
        # 1. For each related decision, find associated skills
        # 2. Score by: relevance + success_rate + recency
        # 3. Return top 3 skills
        # 4. Log whether agent uses them
        pass
```

### RichTaskBrief Extension

```python
@dataclass
class RichTaskBrief:
    # Phase 1 fields
    raw_input: str
    enriched_task: object
    memory_context: MemoryContext
    
    # Phase 2 fields (NEW)
    related_decisions: List[RelatedDecision] = field(default_factory=list)
    recommended_skills: List[RecommendedSkill] = field(default_factory=list)
```

---

## Risk Mitigation

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Graph traversal is slow | Low | Medium | Cache results, limit depth |
| Skills don't improve decisions | Medium | Medium | Measurement loop validates quickly |
| Agent doesn't use skills | Low | Low | Logging + monitoring |
| Latency regression | Low | High | Feature flag, easy rollback |

**Fallback:** If metrics don't improve, disable via feature flag (no code revert needed)

---

## Dependencies

- Phase 1 CEL (MemoryLookup, RichTaskBrief, TaskEngine Phase 5.5) ✅ Complete
- Classifier graph access (already used in Phase 1) ✅ Available
- Skill registry (assumed available) ⚠️ Verify Day 15

---

## Commit Strategy

```
Day 15: scaffold(phase2): graph traversal skeleton
Day 16: feat(graph_traversal): algorithm + ranking
Day 17: test(graph_traversal): integration + 15 tests
Day 18-19: chore(measurement): Day 18-19 results
Day 20-21: feat(skill_injection): implementation + tests
Day 22-24: chore(measurement): Day 22-24 results
Day 25-27: chore(production): health checks + rollback
Day 28-31: chore(measurement): final week (4 days)
Day 32-35: deploy(phase2): staged rollout 10→50→100%
```

---

## Success Metrics Dashboard

By Day 35, we should see:

```
Phase 1 Baseline:
- Success rate: 100%
- P95 latency: 0.20ms
- Memory matches: 0 (dev limitation)

Phase 2 Results:
- Success rate: 100% (maintained)
- P95 latency: <1s (still <700ms target with graph + skills)
- Related decisions found: ≥3 per task
- Skill adoption: ≥60% of tasks
- Decision quality: +5-10% improvement
```

---

## Next Action

**Day 15 (Today):** Verify skill registry access, create skeleton, outline tests

Ready to proceed? 🚀
