# E2E Testing Gaps Summary: Visual Overview

## The Full Task Orchestration Pipeline

```
┌──────────────┐
│  USER INPUT  │
│ (Console UI) │
└──────┬───────┘
       │
       ▼
┌──────────────────────────────────┐
│   LAYER 1: FORM & VALIDATION    │ ← GAP #1: No form→API tests
│ ✅ Unit test: form components   │   No contract tests
│ ✅ Unit test: validation logic  │   Form validation can silently fail
│ ❌ E2E test: form → API call    │
└──────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────┐
│   LAYER 2: API ROUTING           │ ← GAP #2: Incomplete routing tests
│ ✅ Unit test: route handlers    │   Task might not reach backend
│ ⚠️  Integration test: basic flow │   Async queue might drop task
│ ❌ E2E test: async task queue   │
└──────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────┐
│ LAYER 3: BRAIN TASK SCHEDULING  │ ← GAP #3: No Brain selection tests
│ ✅ Unit test: agent selection   │   Wrong agent might be chosen
│ ❌ E2E test: real task schedule │   Concurrent tasks might deadlock
│ ❌ E2E test: async execution    │
└──────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────┐
│  LAYER 4: CONTEXT ENGINEERING   │ ← GAP #4: No context injection tests
│ ✅ Unit test: ADR retrieval     │   Wrong context might be injected
│ ✅ Unit test: memory search     │   Missing dependencies not detected
│ ❌ E2E test: context → agent    │   Conflicts not highlighted
│ ❌ E2E test: context size limit │
└──────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────┐
│  LAYER 5: AGENT EXECUTION       │ ← GAP #5: No execution flow tests
│ ✅ Unit test: agent logic       │   Execution might hang
│ ⚠️  Integration test: isolated   │   Errors might not propagate
│ ❌ E2E test: with real context  │   Timeouts not handled gracefully
└──────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────┐
│  LAYER 6: OUTPUT RENDERING       │ ← GAP #6: No output display tests
│ ❌ E2E test: output → Console   │   Results never appear in UI
│ ❌ E2E test: streaming output   │   Garbled or partial output
│ ❌ E2E test: error messages     │   User confused if task succeeded
└──────────────────────────────────┘
       │
       ▼
┌──────────────┐
│  CONSOLE UI  │
│ (Task Done)  │
└──────────────┘
```

---

## Gap Coverage Matrix

| Layer | Issue | Unit Tests | Integration | E2E Tests | Test Type | Fix Effort |
|-------|-------|----------|--------|-----------|-----------|-----------|
| 1: Form Input | Form→API | ✅ | ❌ | ❌ | Contract | 20h |
| 2: API Routing | Async queue | ✅ | ⚠️ | ❌ | Pipeline | 15h |
| 3: Brain | Task selection | ✅ | ❌ | ❌ | Pipeline | 25h |
| 4: Context | Injection | ✅ | ❌ | ❌ | Integration | 35h |
| 5: Execution | With context | ✅ | ⚠️ | ❌ | End-to-end | 30h |
| 6: Output | Rendering | ❌ | ❌ | ❌ | Browser E2E | 25h |
| **TOTAL** | | | | | | **~150h** |

**Legend:**
- ✅ = Good coverage
- ⚠️ = Partial coverage (subsystem-focused, not user-journey)
- ❌ = No coverage (critical gap)

---

## Missing Test Scenarios (36 Critical Tests)

### Form Input (4 tests)
- [ ] Valid form submission → API call successful
- [ ] Invalid input validation error shown
- [ ] Accessibility: form labels, ARIA
- [ ] Loading state: spinner shows during submission

### API Routing (4 tests)
- [ ] Task submitted → queued status
- [ ] Task progress: queued → running → complete
- [ ] Concurrent tasks (5 from same user)
- [ ] Task timeout + cancellation

### Brain Scheduling (5 tests)
- [ ] Brain selects correct agent for task type
- [ ] Context retrieved for selected agent
- [ ] Concurrent task scheduling (no deadlocks)
- [ ] Fallback agent when primary unavailable
- [ ] Incorrect agent selection detected + fixed

### Context Engineering (6 tests)
- [ ] Correct ADRs selected for task
- [ ] Memory search filters relevantly
- [ ] Context size stays within budget (<4K tokens)
- [ ] Missing dependency detected + warning shown
- [ ] Conflicting ADRs detected + resolution offered
- [ ] Confidence scoring (most relevant first)

### Execution (6 tests)
- [ ] Agent receives injected context
- [ ] Agent executes without errors
- [ ] Long-running task doesn't hang
- [ ] Timeout enforced (stops after N seconds)
- [ ] Execution logs captured for debugging
- [ ] Partial results saved (if interrupted)

### Output Rendering (6 tests)
- [ ] Output format validated (matches schema)
- [ ] Output rendered in Console panel
- [ ] Real-time streaming output appears live
- [ ] Error output formatted clearly
- [ ] Large results paginated
- [ ] Metadata (duration, agent, cost) displayed

### Error Recovery (5 tests)
- [ ] Task timeout → clear error message
- [ ] Brain unavailable → fallback agent
- [ ] Context missing → partial context + warning
- [ ] Execution crash → error logged + user notified
- [ ] Network failure → retry with backoff

---

## Test Coverage Heat Map

```
Current E2E Coverage Across Pipeline:

Form Input       ████░░░░░░░░░ 40%  (form tests exist, no E2E)
API Routing      ██████░░░░░░░ 50%  (basic tests, no async)
Brain Scheduling ████░░░░░░░░░ 35%  (unit only, no E2E)
Context Eng.     ██████░░░░░░░ 50%  (ADR tests scattered, no pipeline)
Execution        ███░░░░░░░░░░ 25%  (isolated tests only)
Output Rendering ░░░░░░░░░░░░░ 0%   (ZERO coverage)
Error Recovery   ███░░░░░░░░░░ 25%  (partial, no E2E)

OVERALL: ████░░░░░░░░░░░░░ 30% E2E COVERAGE
         ▲                   ▲
      Below acceptable   Need 80%+
```

---

## Where Tests Fail Silently (High Risk)

| Scenario | What Happens | Why Undetected | Impact |
|----------|--------------|----------------|--------|
| **User submits form with invalid data** | Form validation passes, API rejects | No form→API contract test | User sees error after 10s delay |
| **Brain picks wrong agent** | Task executes but gives wrong output | No Brain scheduling test | User gets incorrect answer |
| **Context missing ADR** | Agent uses generic pattern | No context injection test | Quality degrades, user blames agent |
| **Task times out silently** | No error message, task appears stuck | No timeout test | User closes Console, tries again |
| **Output never renders** | All backend work done, result invisible | No output rendering test | User thinks task failed |
| **Concurrent tasks deadlock** | Two users' tasks block each other | No concurrency test | Service unavailable, no clear error |

---

## Implementation Priority

### Phase 1: High Risk (Week 1-2, ~40h)
1. **Form→API contract tests** (Playwright) — Prevents silent API mismatches
2. **Output rendering tests** (Playwright) — Prevents invisible results
3. **Brain scheduling tests** (pytest) — Prevents wrong agent selection

### Phase 2: Medium Risk (Week 3-4, ~60h)
4. **Context injection tests** (pytest) — Ensures quality context
5. **Error recovery tests** (pytest) — Graceful degradation
6. **Async pipeline tests** (pytest) — No task drops

### Phase 3: Full Coverage (Week 5-6, ~50h)
7. **End-to-end browser tests** (Playwright) — Full user journey
8. **Performance benchmarks** (Playwright) — Detect regressions
9. **Load tests** (pytest) — Concurrent users

---

## Quick Reference: What to Test First

```
Day 1: Form submission → API call (Playwright)
  → Catches: silent API errors, validation failures

Day 2-3: Brain task scheduling (pytest)
  → Catches: wrong agent selection, task drops, deadlocks

Day 4-5: Context retrieval & injection (pytest)
  → Catches: missing ADRs, conflicts, size limits

Day 6-7: Output rendering (Playwright)
  → Catches: invisible results, garbled text, pagination fails

Week 2: Error scenarios (pytest + Playwright)
  → Catches: timeouts, fallbacks, network issues

Week 3+: Full E2E flows (Playwright)
  → Catches: integration issues across all layers
```

---

## Recommended Testing Tools

| Tool | Purpose | Why |
|------|---------|-----|
| **Playwright** | Browser E2E (form→API→output) | Fast, reliable, screenshots on failure |
| **pytest** | Backend integration (pipeline tests) | Fast, async support, parameterized tests |
| **Pytest-asyncio** | Async task tests | Required for Brain scheduling |
| **Docker Compose** | Test environment (backend+frontend) | Reproducible, isolated from local state |
| **Coverage.py** | Code coverage | Identify uncovered paths (target ≥80%) |
| **GitHub Actions** | CI/CD automation | Run tests on every commit, PR |

---

## Success Criteria for Full Coverage

✅ All 36 critical tests written + passing  
✅ Contract tests cover 100% of API endpoints  
✅ Pipeline tests cover 5 major task flows  
✅ Error tests cover 20 failure scenarios  
✅ E2E tests cover full user journey  
✅ Code coverage ≥80%  
✅ All tests run in CI/CD (no manual testing)  
✅ Tests complete in <5 mins (fast feedback)  

---

**Document:** E2E Testing Gaps Summary  
**Purpose:** Quick reference for what's missing + where to start  
**Read Time:** 5 minutes  
**Implementation:** See console-e2e-testing-strategy.md for detailed roadmap
