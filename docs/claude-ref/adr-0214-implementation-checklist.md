# ADR-0214 Implementation Checklist

## Phase 1: Core Components (Pre-ADR-0210 Integration)

### RobustEngineDetector
- [ ] Signal 1: Parallelization ratio computation
- [ ] Signal 2: Data volume + complexity scoring
- [ ] Signal 3: Task type classification + confidence weighting
- [ ] Signal 4: Historical loss tracking (in-session)
- [ ] Signal 5: Context availability check
- [ ] Softmax ensemble + probability normalization
- [ ] Unit tests (15+ scenarios)

### L34DelegationGate
- [ ] Integration with L34 classifier (existing)
- [ ] `can_delegate_step()` with data classification
- [ ] `sanitize_snapshot()` filtering
- [ ] Unit tests (10+ edge cases)

### LossProfileTracker (In-Session)
- [ ] History storage (dict, FIFO 1000 max)
- [ ] `record_delegation_result()`
- [ ] `estimate_loss_for_task_type()`
- [ ] Signal 4 feedback integration
- [ ] Unit tests (8+ tests)

### Slash Command Parser
- [ ] Parse `/use-engine <name>`
- [ ] Parse `/engine-auto`
- [ ] Parse `/debug-engine`
- [ ] Validation + error handling
- [ ] CLI + Bridge compatibility
- [ ] Unit tests (10+ patterns)

---

## Phase 2: Engine Integration (After ADR-0210 Phase 1)

### EngineRegistry
- [ ] Registration of 3 engines (claude_code, acs, tiered_delegation)
- [ ] Plugin interface for detectors
- [ ] Detector loading (config-based)

### AdaptiveDelegationExecutor
- [ ] Integration with RobustEngineDetector
- [ ] L34Gate invocation
- [ ] Parallel batch execution
- [ ] Streaming path auto-detection (>1GB)
- [ ] Unit tests (12+ tests)

### send() Flow Integration
- [ ] Slash command parsing (before InitialAnalysis)
- [ ] Engine detection (after InitialAnalysis)
- [ ] Engine selection logic
- [ ] Loss-recording (post-execution)
- [ ] E2E tests (5+ scenarios)

---

## Phase 3: Streaming Path (Optional, Phase 2+)

- [ ] Data volume estimation
- [ ] Stream-based L34 filtering
- [ ] Remote delegation with streams
- [ ] Backpressure handling

---

## Testing

### Unit Tests (60+ total)
- `test_robust_detector.py` (15 tests)
- `test_l34_gate.py` (10 tests)
- `test_loss_tracker.py` (8 tests)
- `test_slash_commands.py` (10 tests)
- `test_ensemble_scoring.py` (12 tests)
- `test_adaptive_executor.py` (12 tests)

### E2E Tests (10+ scenarios)
- [ ] Coding task → TDE auto-detected
- [ ] Reasoning task → ACS auto-detected
- [ ] Simple task → Claude Code auto-detected
- [ ] Slash command override
- [ ] Large data auto-streaming
- [ ] Loss adaptation over time
- [ ] Fallback on error
- [ ] Loss-profile FIFO eviction
- [ ] Detector plugin loading

### Adversarial Tests (from review)
- [ ] TBD (after review)

---

## Dependencies & Blockers

| Blocker | Status | Impact |
|---|---|---|
| ADR-0210 Phase 1 Integration | ⏳ Pending | TDE Phase 1.5 detection needs InitialAnalysis |
| L34 Classifier Access | ✅ Available | L34DelegationGate can import |
| EngineRegistry in L22 | ⏳ Pending | Need send() hookpoint |

---

## Success Criteria

- ✅ 60+ unit tests, all green
- ✅ 10+ E2E scenarios passing
- ✅ Zero data-leakage incidents (L34 verified)
- ✅ Auto-detection confidence > 75% (after 50 tasks in session)
- ✅ Loss estimation within 5% of actual (after 20 samples per task_type)
- ✅ Adversarial review findings resolved

