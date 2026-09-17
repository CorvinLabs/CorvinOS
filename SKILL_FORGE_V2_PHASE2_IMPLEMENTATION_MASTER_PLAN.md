# Skill Forge v2.0 Phase 2: Master Implementation Plan

**Date:** 2026-09-17  
**Status:** 🚀 PHASE 2 EXECUTION START  
**Scope:** Learning Integration + Production Deployment + A/B Benchmarking

---

## 📋 Phase 2 Objectives

1. **Learning Integration (ADR-0693/0694)**
   - [ ] SkillLearningBridge class (execute → feedback → config update)
   - [ ] LearningOptimizer with bounds + convergence detection
   - [ ] Audit trail integration (all decisions logged)

2. **Production-Grade Deployment**
   - [ ] Systemd units for skill workers (external, not in repo)
   - [ ] Health checks + monitoring (Prometheus metrics)
   - [ ] Graceful shutdown + signal handling
   - [ ] State persistence (config snapshots)

3. **Configuration Testing Framework**
   - [ ] Real task generator (multi-tenant, diverse workloads)
   - [ ] Synthetic task generator (edge cases, failure modes)
   - [ ] Configuration matrix (16+ permutations)
   - [ ] Call-site level testing (not mocked)

4. **A/B Benchmarking Suite**
   - [ ] Baseline: Skill Forge v1.x performance
   - [ ] Variant A: Skill Forge v2.0 without learning
   - [ ] Variant B: Skill Forge v2.0 with learning
   - [ ] Metrics: throughput, latency, cost, quality, convergence
   - [ ] Statistical significance testing (t-test, effect size)

---

## 🏗️ Architecture Overview

```
┌─ Skill Execution ─────────────────────────────────┐
│                                                     │
│  Skill.execute(input)                             │
│    ↓                                               │
│  1. Execute skill logic (deterministic)           │
│    ↓                                               │
│  2. Emit skill_executed event → audit trail       │
│    ↓                                               │
│  3. SkillLearningBridge._async_feedback_loop()    │
│    ├─ Query feedback events (from TaskManager)    │
│    ├─ LearningOptimizer.process_feedback()        │
│    │  ├─ Validate PII (fail-closed)              │
│    │  ├─ Bounds check (±1σ)                      │
│    │  ├─ Convergence detect                      │
│    │  └─ Update config if safe                   │
│    └─ Emit skill_config_updated → audit trail    │
│    ↓                                               │
│  4. Return result to caller                       │
│    ↓                                               │
│ [Next execution uses learned config]             │
└─────────────────────────────────────────────────────┘
```

---

## 📂 Deliverables by Component

### 1. Learning Integration Module
**Files to Create:**
- `core/learning/skill_learning_bridge.py` (400+ LoC)
- `core/learning/skill_optimizer.py` (350+ LoC)
- `core/learning/convergence_detector.py` (200+ LoC)
- `core/learning/pii_scrubber.py` (150+ LoC)

**Key Classes:**
- `SkillLearningBridge` — Async feedback loop integration
- `LearningOptimizer` — Stateless parameter optimization
- `ConvergenceDetector` — Slope-based convergence + confidence
- `PIIScrubber` — Fail-closed PII validation

### 2. Production Deployment
**Files to Create (NOT in repo):**
- `/etc/systemd/user/corvin-skill-worker.service`
- `/etc/systemd/user/corvin-skill-worker.timer`
- `/usr/local/bin/corvin-skill-worker` (wrapper script)
- `~/.config/corvin-skill-worker/worker.conf`

**Key Features:**
- Auto-restart on failure
- Rate limiting (max 10 workers)
- CPU + memory limits
- Graceful shutdown (SIGTERM + 30s timeout)
- State snapshots (config backup every 1h)

### 3. Configuration Testing Framework
**Files to Create:**
- `tests/config/real_task_generator.py` (300+ LoC)
- `tests/config/synthetic_task_generator.py` (400+ LoC)
- `tests/config/configuration_matrix.py` (250+ LoC)
- `tests/config/test_all_configurations.py` (600+ LoC)

**Testing Matrix:**
- Skill Variants: 3 (B, C, D from model selector)
- Tenant Types: 2 (free, enterprise)
- Workload Types: 4 (simple, medium, complex, edge)
- Learning Enabled: 2 (on, off)
- Quota State: 2 (available, exhausted)
- **Total Configs:** 3 × 2 × 4 × 2 × 2 = **96 combinations**

### 4. A/B Benchmarking Suite
**Files to Create:**
- `tests/benchmarks/ab_benchmarking_suite.py` (800+ LoC)
- `tests/benchmarks/statistical_analyzer.py` (300+ LoC)
- `tests/benchmarks/metrics_collector.py` (250+ LoC)
- `benchmarks/reports/ab_test_results.json`

**Variants:**
- **A:** Skill Forge v1.x (baseline)
- **B:** v2.0 without learning
- **C:** v2.0 with learning (full)

**Metrics Collected:**
- Throughput (req/sec)
- Latency (p50, p95, p99)
- Success rate
- Cost (USD per task)
- Quality score (if applicable)
- Convergence time (for learning)
- Configuration memory usage

---

## 🎯 Execution Timeline (This Session)

| Phase | Duration | Deliverables |
|-------|----------|--------------|
| **Phase 2.1** | 1h | Learning Integration (bridge + optimizer) |
| **Phase 2.2** | 1h | Convergence Detection + PII Scrubbing |
| **Phase 2.3** | 1.5h | Production Deployment Setup |
| **Phase 2.4** | 1.5h | Configuration Testing Framework |
| **Phase 2.5** | 2h | A/B Benchmarking Suite + Analysis |
| **Phase 2.6** | 1.5h | Live Testing + Results |
| **TOTAL** | ~9h | Full Phase 2 Execution |

---

## 🔍 Quality Gates

### Phase 2.1: Learning Integration
- [ ] SkillLearningBridge reachability proof (E2E call site)
- [ ] All feedback events processed (audit trail shows processing)
- [ ] Config updates validated (before → after values match policy)
- [ ] Audit events logged (skill_config_updated present)

### Phase 2.2: Convergence & PII
- [ ] Convergence algorithm converges in <100 iterations (typical)
- [ ] Confidence scores increase as convergence nears (monotonic)
- [ ] PII scrubber rejects 100% of test PII patterns
- [ ] Fail-closed behavior verified (rejected events logged)

### Phase 2.3: Production Deployment
- [ ] Systemd unit starts/stops gracefully
- [ ] Health check passes (HTTP 200)
- [ ] Signal handling works (SIGTERM → 30s grace → exit)
- [ ] State snapshot created on shutdown
- [ ] Recovery works (restart from latest snapshot)

### Phase 2.4: Configuration Testing
- [ ] 96 configuration combinations tested
- [ ] Each config exercises real call site (not mocked)
- [ ] All edge cases covered (low quota, high latency, failures)
- [ ] Test report shows pass/fail per configuration

### Phase 2.5: A/B Benchmarking
- [ ] Baseline collected (v1.x or v2.0 without learning)
- [ ] Variant A, B, C all run same workload
- [ ] Metrics show statistically significant differences (p < 0.05)
- [ ] Learning variant converges within expected time
- [ ] Cost/quality tradeoff clear from results

---

## 📊 Success Criteria

**Learning Integration:**
- ✅ Skill configurations learn from feedback (success rates improve)
- ✅ Convergence detected and learning stops at plateau
- ✅ All optimization steps audited (no silent changes)

**Production Deployment:**
- ✅ Skill workers run continuously under Systemd
- ✅ Health checks passing (no manual intervention)
- ✅ Graceful degradation on resource limits

**Configuration Testing:**
- ✅ 96 config combinations all pass (edge cases included)
- ✅ No race conditions on primitive level
- ✅ All failures logged with full context

**A/B Benchmarking:**
- ✅ Learning variant shows improvement over baseline
- ✅ Convergence time < 10,000 tasks (typical)
- ✅ Cost per task remains stable (no runaway learning)
- ✅ Statistical significance confirmed (p < 0.05)

---

## ⚠️ Constraints & Guardrails

**Do NOT:**
- ❌ Mock skill execution (test real call sites only)
- ❌ Add race conditions at skill interface level
- ❌ Store Systemd units in repo (use ~/.config or /etc/systemd/user)
- ❌ Enable learning without bounds checking
- ❌ Skip PII scrubbing (fail-closed always)

**Do:**
- ✅ Handle all error modes (quota exhaustion, timeouts, PII)
- ✅ Log every decision (audit trail complete)
- ✅ Test convergence mathematically (not just eyeball)
- ✅ Benchmark with real workloads + synthetic edge cases
- ✅ Verify reachability on call site level

---

## 🚀 Ready to Execute

**Prerequisites:** ✅ Phase A complete, ADRs drafted, architecture locked

**Next Steps (This Turn):**
1. Implement Phase 2.1–2.2 (Learning Integration + Convergence)
2. Implement Phase 2.3 (Production Deployment)
3. Implement Phase 2.4 (Configuration Testing)
4. Implement Phase 2.5 (A/B Benchmarking)
5. Execute Phase 2.6 (Live Testing + Final Results)

**Output:** Skill Forge v2.0 Phase 2 complete, production-ready, benchmarked.

---

*Execution plan approved. Proceeding with Phase 2.1–2.6 implementation.*
