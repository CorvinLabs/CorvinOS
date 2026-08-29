# Console E2E Testing Strategy: Frontend-Backend Integration & Task Orchestration Pipeline

**Status:** STRATEGY DESIGN (2026-08-29)  
**Scope:** Identify gaps + design comprehensive testing for the full task pipeline  
**Related:** ADR-0048, ADR-0062, ADR-0400 (Plugin Builder Frontend), ADR-0125/0126, ADR-0262/0263  

---

## Executive Summary

**Current State:**
- ✅ Backend unit tests: ~200+ tests (routes, plugins, compliance, etc.)
- ✅ Frontend unit tests: ~600+ tests (components, hooks, utilities)
- ⚠️ Backend integration tests: scattered, incomplete
- ❌ **GAP: Frontend-Backend E2E tests** — no unified flow from Console input → task completion
- ❌ **GAP: Task orchestration E2E** — Brain → Context → Execution not end-to-end tested

**Problem:**
A user opens Console, submits a task (e.g., "Analyze this data"), and it completes. This entire flow (Frontend UI → Backend routing → Brain subsystems → Context engineering → Execution → Output) is NOT tested end-to-end. Missing links:
1. Console form input → API call
2. API call → Brain task scheduling
3. Brain decision → Context retrieval & injection
4. Context → Agent execution  
5. Execution output → Console display

---

## Part 1: Current E2E Test Coverage (Inventory)

### Backend E2E Tests (Existing)

| Test File | Coverage | Gaps |
|---|---|---|
| `test_task_api_http.py` | HTTP task API endpoints | Doesn't test Console UI → API interaction |
| `test_chat_turns_execution_context.py` | ExecutionContext lifecycle | Missing async channel flow |
| `test_learning_e2e_console.py` | Learning events in Console | Isolated, not full task flow |
| `test_tde_measurement_integration.py` | TDE measurement pipeline | Doesn't test user-facing features |
| `tests/integration/*.py` | Various subsystems | Decoupled, no unified orchestration test |

**Verdict:** Tests exist but are **subsystem-focused**, not **user-journey-focused**

### Frontend E2E Tests (Existing)

| Test File | Coverage | Gaps |
|---|---|---|
| `capabilities-gating.test.ts` | Feature flags + panel visibility | No actual panel data flow |
| `useVoicePlayback.test.tsx` | Voice output hook | No voice-to-panel integration |
| `panel-nav-wiring.test.ts` | Navigation routing | No actual panel execution |
| `integration/phase2-critical-flows.test.tsx` | "Critical flows" (vague) | Incomplete, scope unclear |

**Verdict:** Component tests exist but **no task-execution flows** tested in browser

### Missing E2E Test Categories

| Category | Why Critical | Current Coverage |
|---|---|---|
| **Form → Submission → Execution** | User's primary journey | ❌ None |
| **Brain task scheduling** | Core orchestration | ⚠️ Unit tests only |
| **Context injection** | Agent decision quality | ⚠️ Isolated tests |
| **Result rendering** | User sees output | ❌ None |
| **Error recovery** | Resilience | ⚠️ Partial |
| **Long-running task** | Background work | ❌ None |
| **Cross-panel communication** | Panel integration | ❌ None |
| **Permission/capability gating** | Security | ⚠️ Flag tests only |

---

## Part 2: Gap Analysis — Where Breaks Likely Hide

### 1. Console Form → API Call (UI Layer Gap)

**User action:** Fill form in Console panel, click [Submit]  
**Current test coverage:** ❌ None

**Missing tests:**
- Form validation (client-side)
- Form → API payload serialization
- Error states (form shows errors)
- Loading spinner during submission
- Accessibility (form labels, ARIA)

**Risk:** Silently invalid payloads sent to backend, form UX broken, disabled users can't operate.

### 2. API Call → Brain Task Scheduling (Routing Gap)

**Backend receives:** POST /api/v2/task/submit with {input, context, user_id, ...}  
**Brain should:** Schedule task, create ExecutionContext, notify frontend  
**Current test coverage:** ⚠️ Partial (test_task_api_http has basic tests)

**Missing tests:**
- Async task lifecycle (submit → queued → running → complete)
- Brain subsystem selection (which brain agent handles this task?)
- Concurrent task handling (5 tasks from different users)
- Task timeout + cancellation
- Brain's internal decision (why was THIS agent chosen?)

**Risk:** Tasks silently dropped, wrong agent chosen, concurrent tasks deadlock.

### 3. Brain → Context Engineering (Decision Gap)

**Brain decides:** "Use LoopEngineer + ADR-context"  
**Context should:** Inject ADRs + memories + skills  
**Current test coverage:** ⚠️ Partial (ADR tests exist, but not in task context)

**Missing tests:**
- Context retrieval for a specific task (what's relevant for THIS task?)
- Context size validation (limit ~4K tokens)
- Confidence scoring (which context items are most relevant?)
- Missing dependency detection (task asks for ADR-X but it's not found)
- Conflict detection (ADR-X contradicts ADR-Y)

**Risk:** Wrong context injected, agent makes decisions on incomplete info, contradictions cause failures.

### 4. Execution → Output Rendering (Display Gap)

**Agent produces:** Structured output {type: "result", content: "...", metadata: {...}}  
**Console should:** Render output, update panel, show success  
**Current test coverage:** ❌ None

**Missing tests:**
- Output format validation (matches expected schema?)
- Output → Console component mapping (which panel renders this?)
- Real-time streaming output (SSE or WebSocket)
- Error output formatting (failure messages clear?)
- Output pagination (large results split into pages)

**Risk:** Output never appears in Console, garbled text, crashed panels, user unsure if task succeeded.

### 5. End-to-End Task Orchestration (Orchestration Gap)

**Full flow:** User submits → Brain schedules → Context loads → Agent executes → Output renders  
**Current test coverage:** ❌ NONE (no single test covers this)

**Missing test:**
```
test_full_task_orchestration_e2e():
  # 1. User submits task via Console UI
  # 2. Verify task appears in /task/status (running)
  # 3. Verify Brain selected correct agent
  # 4. Verify Context injected (check agent's logs for "Loaded 3 ADRs")
  # 5. Wait for execution complete
  # 6. Verify output rendered in Console panel
  # 7. Verify task status = complete
```

**Risk:** Task silently fails mid-pipeline, user has no idea what happened.

---

## Part 3: Comprehensive E2E Testing Strategy

### Layer 1: Frontend-to-Backend Contract Tests

**Goal:** Ensure Console UI correctly calls backend APIs

**Test type:** Playwright (browser automation) + Backend API mock  
**Duration:** 5 mins, can run on every commit

```typescript
// tests/e2e/console-api-contract.test.ts

describe('Console → Backend API contract', () => {
  test('Task submission form → POST /api/v2/task/submit', async ({ page }) => {
    await page.goto('http://localhost:8765/console/app/tasks');
    
    // Fill form
    await page.fill('input[name="taskInput"]', 'Analyze this data');
    await page.selectOption('select[name="taskType"]', 'analysis');
    
    // Intercept API call
    const requestPromise = page.waitForResponse(resp => 
      resp.url().includes('/api/v2/task/submit') && resp.status() === 200
    );
    
    await page.click('button:has-text("Submit")');
    
    // Verify payload
    const response = await requestPromise;
    const data = await response.json();
    
    expect(data).toMatchSchema({
      task_id: expect.any(String),
      status: 'queued',
      submitted_at: expect.any(String),
    });
  });

  test('Task result rendering → GET /api/v2/task/{id}/output', async ({ page }) => {
    // Mock API to return result
    await page.route('/api/v2/task/*/output', async route => {
      await route.abort('blockedbyresponse'); // Start fresh
      await route.continue({
        status: 200,
        body: JSON.stringify({
          task_id: 'task-123',
          output: 'Analysis complete: ...',
          status: 'complete',
          metadata: { duration: 5.2 },
        }),
      });
    });
    
    await page.goto('http://localhost:8765/console/app/task-details/task-123');
    
    // Verify output rendered
    await expect(page.locator('text=Analysis complete')).toBeVisible();
  });
});
```

**Tests needed:** 10+ contract tests (one per major form/endpoint)

### Layer 2: Backend Task Pipeline Integration Tests

**Goal:** Verify task flows through Brain → Context → Execution

**Test type:** Python pytest (ASGI + in-memory database)  
**Duration:** 30 secs, separate from Playwright

```python
# tests/integration/test_task_pipeline_e2e.py

async def test_task_submission_to_completion():
    """Full pipeline: submit → brain schedules → context injected → executes → outputs."""
    
    # Setup
    client = TestClient(app)
    user_id = "test-user-123"
    
    # Step 1: Submit task
    response = client.post(
        '/api/v2/task/submit',
        json={
            'input': 'What is the capital of France?',
            'task_type': 'qa',
            'user_id': user_id,
        }
    )
    assert response.status_code == 200
    task_id = response.json()['task_id']
    
    # Step 2: Verify Brain scheduled task
    status = client.get(f'/api/v2/task/{task_id}/status').json()
    assert status['status'] in ['queued', 'running']
    assert status['agent'] in ['direct', 'brain.qa_agent', 'brain.research_agent']
    
    # Step 3: Simulate execution (call agent directly)
    context = await get_context_for_task(task_id)
    assert len(context['injected_adrs']) > 0  # Context was injected
    assert len(context['injected_memories']) > 0
    
    agent = get_agent_for_task(task_id)
    output = await agent.execute(context)
    
    # Step 4: Verify output stored
    assert output is not None
    assert output['status'] == 'complete'
    assert len(output['content']) > 10  # Has actual output
    
    # Step 5: Verify result API returns output
    result = client.get(f'/api/v2/task/{task_id}/output').json()
    assert result['output'] == output['content']
    assert result['status'] == 'complete'
    
    # Step 6: Verify task completion event (for Console UI)
    events = client.get(f'/api/v2/task/{task_id}/events').json()
    assert any(e['type'] == 'task_complete' for e in events)
```

**Tests needed:** 15+ pipeline tests (one per major flow)

### Layer 3: Brain-Context Integration Tests

**Goal:** Verify Brain correctly selects context + injects into execution

**Test type:** Python pytest (isolated component test)  
**Duration:** 2 secs, run on every commit

```python
# tests/core/test_brain_context_integration.py

def test_brain_selects_context_for_task():
    """Brain identifies relevant ADRs + memories for task."""
    
    task = Task(
        input='How should I design authentication?',
        type='design',
        user_id='user-123',
    )
    
    # Brain's context selection
    context_items = brain.select_context(task)
    
    # Verify relevant ADRs selected
    adr_ids = [item.id for item in context_items if item.type == 'adr']
    assert 'ADR-0268' in adr_ids  # Auth design
    assert 'ADR-0269' in adr_ids  # Context engineering (meta)
    
    # Verify memories selected
    memory_ids = [item.id for item in context_items if item.type == 'memory']
    assert any('authentication' in item.title.lower() for item in context_items)
    
    # Verify context size within budget
    total_tokens = sum(item.token_count for item in context_items)
    assert total_tokens < 4000  # Should fit in agent's context

def test_brain_detects_missing_dependencies():
    """Brain alerts when task needs unavailable context."""
    
    task = Task(
        input='Use the custom-plugin framework',
        type='implementation',
        user_id='user-123',
    )
    
    context_items = brain.select_context(task)
    warnings = brain.get_warnings(task, context_items)
    
    # Should warn: custom-plugin ADR doesn't exist locally
    assert any('not found' in w.message.lower() for w in warnings)
    
    # Output should tell user what's missing
    for warning in warnings:
        assert warning.recovery_action is not None  # Actionable

def test_brain_context_conflict_detection():
    """Brain detects contradictions in injected context."""
    
    task = Task(
        input='Implement both sync and async API',
        type='architecture',
    )
    
    context_items = brain.select_context(task)
    conflicts = brain.detect_conflicts(context_items)
    
    # Should find: ADR-X says "prefer async", ADR-Y says "use sync"
    if conflicts:
        for conflict in conflicts:
            assert conflict.items == 2  # Two conflicting items
            assert conflict.resolution_hint is not None
```

**Tests needed:** 20+ context-selection tests (one per decision type)

### Layer 4: Error Recovery & Edge Cases

**Goal:** Verify system handles failures gracefully

**Test type:** Python pytest + Playwright (mixed)  
**Duration:** 5 secs each

```python
# tests/integration/test_task_error_recovery.py

async def test_task_timeout_handling():
    """Long-running task times out, user gets clear message."""
    
    client = TestClient(app)
    
    # Submit task with 1-second timeout
    response = client.post(
        '/api/v2/task/submit',
        json={
            'input': 'Analyze 1GB dataset',
            'timeout_seconds': 1,
        }
    )
    task_id = response.json()['task_id']
    
    # Wait for timeout
    time.sleep(2)
    
    # Verify error status
    status = client.get(f'/api/v2/task/{task_id}/status').json()
    assert status['status'] == 'failed'
    assert status['error_type'] == 'timeout'
    assert 'Task exceeded 1 second' in status['error_message']

async def test_context_missing_dependency():
    """Task needs context that doesn't exist, agent gets fallback."""
    
    # Hide ADR-X from system
    with patch('core.context.load_adr') as mock_load:
        mock_load.side_effect = FileNotFoundError('ADR-X not found')
        
        task = Task(input='Use ADR-X pattern', type='design')
        context = await get_context_for_task(task)
        
        # Should have fallback context
        assert len(context['fallback_adrs']) > 0
        assert context['warnings'] = ['ADR-X not found, using general pattern']

async def test_brain_selects_fallback_agent():
    """Primary agent fails, Brain switches to fallback."""
    
    task = Task(input='Complex analysis', type='analysis')
    
    # Primary agent crashes
    with patch('brain.agents.qa_agent.execute') as mock_execute:
        mock_execute.side_effect = RuntimeError('OOM')
        
        result = await task_runner.execute(task)
        
        # Fallback agent was used
        assert result['agent'] == 'brain.fallback_agent'
        assert result['status'] == 'complete'  # Still succeeded
```

**Tests needed:** 15+ error-recovery tests (one per failure mode)

### Layer 5: Full Browser E2E (Playwright)

**Goal:** Verify entire flow in real browser + backend

**Test type:** Playwright (TypeScript)  
**Duration:** 30 secs each, run on staging

```typescript
// tests/e2e/task-submission-to-completion.spec.ts

import { test, expect } from '@playwright/test';

test('Full task flow: submit → brain → context → execute → render', async ({ page, context }) => {
  // Setup: open Console, login
  await page.goto('http://localhost:8765/console/app');
  await page.fill('input[name="username"]', 'test@example.com');
  await page.fill('input[name="password"]', 'test123');
  await page.click('button:has-text("Login")');
  
  // Verify logged in
  await expect(page.locator('text=Welcome')).toBeVisible();
  
  // Navigate to Tasks panel
  await page.click('text=Tasks');
  await expect(page.locator('text=Submit Task')).toBeVisible();
  
  // Fill task form
  await page.fill('textarea[name="taskInput"]', 'What is quantum computing?');
  await page.selectOption('select[name="taskType"]', 'explanation');
  await page.click('button:has-text("Submit")');
  
  // Verify task submitted (UI feedback)
  await expect(page.locator('text=Task submitted')).toBeVisible();
  const taskLink = page.locator('a[href*="/task-"]').first();
  await taskLink.click();
  
  // Monitor task progress
  await expect(page.locator('text=Status: running')).toBeVisible({ timeout: 5000 });
  
  // Wait for completion
  await expect(page.locator('text=Status: complete')).toBeVisible({ timeout: 30000 });
  
  // Verify output rendered
  const output = page.locator('[data-testid="task-output"]');
  await expect(output).toBeVisible();
  
  // Verify output has actual content
  const text = await output.textContent();
  expect(text).toContain('quantum'); // Should answer the question
  expect(text.length).toBeGreaterThan(100); // Not just a stub
  
  // Verify metadata visible (duration, agent used, etc.)
  await expect(page.locator('text=Completed in')).toBeVisible();
  await expect(page.locator('text=Agent:')).toBeVisible();
});

test('Error handling: invalid input shows form error', async ({ page }) => {
  await page.goto('http://localhost:8765/console/app/tasks');
  
  // Try to submit empty form
  await page.click('button:has-text("Submit")');
  
  // Verify validation error
  await expect(page.locator('text=Task input is required')).toBeVisible();
  
  // Form should still be editable
  await page.fill('textarea[name="taskInput"]', 'Valid input');
  await page.click('button:has-text("Submit")');
  await expect(page.locator('text=Task submitted')).toBeVisible();
});

test('Task cancellation: user can stop long-running task', async ({ page }) => {
  await page.goto('http://localhost:8765/console/app/tasks');
  
  // Submit task
  await page.fill('textarea[name="taskInput"]', 'Analyze 1GB dataset');
  await page.click('button:has-text("Submit")');
  
  // Navigate to task details
  await page.click('a[href*="/task-"]');
  await expect(page.locator('text=Status: running')).toBeVisible();
  
  // Click Cancel
  await page.click('button:has-text("Cancel Task")');
  await expect(page.locator('text=Cancel confirmed')).toBeVisible();
  
  // Verify status changed
  await expect(page.locator('text=Status: cancelled')).toBeVisible();
});
```

**Tests needed:** 8-10 full browser flows

---

## Part 4: Implementation Roadmap

### Phase 1: Contract Tests (Week 1-2)
**Goal:** Ensure Console UI correctly calls backend  
**Deliverables:**
- Playwright contract test suite (10+ tests)
- Mock API server for isolated testing
- CI/CD integration (run on every commit)

**Effort:** 40 hours

### Phase 2: Pipeline Integration (Week 3-4)
**Goal:** Verify task flows through Brain → Context → Execution  
**Deliverables:**
- Backend pipeline E2E tests (15+ tests)
- Brain-Context integration tests (20+ tests)
- Error recovery tests (15+ tests)

**Effort:** 60 hours

### Phase 3: Full Browser E2E (Week 5-6)
**Goal:** Test entire flow in real browser  
**Deliverables:**
- Playwright suite (8-10 full flows)
- Staging environment testing
- Performance benchmarks

**Effort:** 50 hours

### Phase 4: CI/CD + Documentation (Week 7-8)
**Goal:** Automate all tests, document for future dev  
**Deliverables:**
- GitHub Actions workflows (run on every commit/PR)
- Test documentation + troubleshooting guide
- Maintenance runbook

**Effort:** 30 hours

**Total:** ~180 hours (4-5 weeks)

---

## Part 5: Test Environment Setup

### Local Testing Stack

```yaml
# docker-compose.test.yml
version: '3'
services:
  corvin-backend:
    image: corvin:dev
    ports: [8000]
    environment:
      - CORVIN_MODE=test
      - DB=in-memory
      - CACHE=in-memory
  
  console-frontend:
    image: console:dev
    ports: [8765]
    environment:
      - BACKEND_URL=http://corvin-backend:8000
      - TEST_MODE=true
  
  playwright:
    image: mcr.microsoft.com/playwright:v1.40
    volumes: [./tests:/tests]
    environment:
      - BASE_URL=http://console-frontend:8765
```

### Test Database

```python
# tests/fixtures/database.py
@pytest.fixture
async def test_db():
    """In-memory database for tests."""
    db = InMemoryDB()
    
    # Seed with test data
    await db.insert_adr('ADR-0268', {...})  # Auth design
    await db.insert_memory('auth-patterns', {...})  # Memory
    
    yield db
    
    # Cleanup
    await db.clear()
```

---

## Part 6: Success Metrics

| Metric | Target | How to measure |
|---|---|---|
| **Contract test coverage** | 100% of API endpoints | Coverage report from Playwright |
| **Pipeline test coverage** | 90% of task flows | Test matrix (5 task types × 3 complexity levels) |
| **E2E test pass rate** | 95%+ (flakes acceptable <5%) | CI/CD green rate |
| **Error recovery** | 100% of failure modes | Checklist of 20 failure scenarios |
| **Performance** | Contract tests <5s, Pipeline <30s, E2E <60s | Timeout config in CI |
| **Documentation** | 100% of tests documented | Comments + README in test files |

---

## Summary: The Testing Gap & Solution

| Gap | Problem | Solution |
|---|---|---|
| **No form→API tests** | Silent form failures | Contract tests (Playwright) |
| **No Brain scheduling tests** | Tasks drop silently | Pipeline integration tests (pytest) |
| **No Context injection tests** | Wrong context injected | Brain-Context tests (pytest) |
| **No output rendering tests** | Results never appear | Full browser E2E (Playwright) |
| **No error recovery tests** | No graceful degradation | Error scenario tests (pytest) |

**Implementation:** 180 hours, 4-5 weeks, 180+ tests, complete E2E coverage.

---

**Document:** Console E2E Testing Strategy  
**Status:** READY FOR PHASE 1 IMPLEMENTATION  
**Owner:** TBD (Testing/QA team)  
**Start:** Next sprint
