# Full Task Orchestration E2E Tests

**File:** `tests/e2e/test_full_task_orchestration_e2e.py`

**Total Tests:** 10 comprehensive Playwright tests covering the complete task orchestration pipeline.

## Test Coverage

### 1. Simple QA Task (E2E Baseline)
- **Test:** `test_simple_qa_task_end_to_end`
- **Scope:** Basic task submission → queuing → scheduling → result display
- **Duration:** ~5-10 seconds per run
- **Risk Level:** Low (baseline test)

### 2. Analysis Task with Dependencies
- **Test:** `test_analysis_task_with_dependencies`
- **Scope:** Task DAG with `depends_on` relationship; verifies parent completes before child executes
- **Duration:** ~10-15 seconds
- **Risk Level:** Medium (tests DAG ordering)

### 3. Long-Running Task Progress
- **Test:** `test_long_running_task_progress_visible`
- **Scope:** Real-time progress updates during long execution; monitors progress bar
- **Duration:** ~15-20 seconds
- **Risk Level:** Medium (timing-sensitive)

### 4. Timeout & Graceful Recovery
- **Test:** `test_task_timeout_graceful_recovery`
- **Scope:** Tasks that exceed timeout show clear error; user can retry
- **Duration:** ~10-15 seconds
- **Risk Level:** Medium (timeout configuration dependent)

### 5. Concurrent Task Isolation
- **Test:** `test_concurrent_tasks_isolated`
- **Scope:** 3+ concurrent tasks run without interference; output not mixed
- **Duration:** ~15-20 seconds
- **Risk Level:** High (concurrency-related, may be flaky)

### 6. Error and Retry Flow
- **Test:** `test_error_and_retry`
- **Scope:** Task failure → error message → retry → re-execution
- **Duration:** ~10-15 seconds
- **Risk Level:** Medium (failure path dependent)

### 7. Multi-Panel Workflow
- **Test:** `test_multi_panel_workflow`
- **Scope:** Task state persists when switching between Console panels
- **Duration:** ~10 seconds
- **Risk Level:** Medium (navigation-dependent)

### 8. Form Validation & API Prevention
- **Test:** `test_form_validation_prevents_invalid_submission`
- **Scope:** Empty/invalid form input blocked before API call
- **Duration:** ~5 seconds
- **Risk Level:** Low (validation is deterministic)

### 9. Large Output Rendering
- **Test:** `test_large_output_rendering`
- **Scope:** Output >100KB renders correctly; no crashes or truncation
- **Duration:** ~30-45 seconds
- **Risk Level:** High (performance-dependent)

### 10. Performance Baseline
- **Test:** `test_task_end_to_end_performance`
- **Scope:** Full pipeline completes within 60s budget
- **Duration:** ~10-30 seconds (depends on task complexity)
- **Risk Level:** Medium (timing-dependent)

## Prerequisites

### Required Services
- **Console Web UI** running at `http://localhost:8765` (default)
- **Backend API** running at `http://localhost:8000` (default)
- **Browser** for Playwright (Chrome/Chromium by default)

### Environment Variables
```bash
# Override defaults if needed
export BASE_URL="http://localhost:8765"
export API_BASE_URL="http://localhost:8000"
export HEADLESS="true"          # Run headless (no window)
export SLOW_MO="0"              # Slow down browser by N ms (for debugging)
export TIMEOUT="30000"          # Default element wait timeout (30s)
```

### Dependencies
```bash
pip install pytest pytest-asyncio pytest-cov httpx playwright
playwright install  # Download browser binaries
```

## Running the Tests

### Run All Tests
```bash
# Full test suite (all 10 tests)
pytest tests/e2e/test_full_task_orchestration_e2e.py -v

# With coverage
pytest tests/e2e/test_full_task_orchestration_e2e.py -v --cov=tests/e2e

# With detailed output
pytest tests/e2e/test_full_task_orchestration_e2e.py -vv --tb=short
```

### Run Specific Test
```bash
# Single test
pytest tests/e2e/test_full_task_orchestration_e2e.py::TestFullTaskOrchestrationE2E::test_simple_qa_task_end_to_end -v

# By marker
pytest tests/e2e/test_full_task_orchestration_e2e.py -k "qa_task" -v
```

### Run with Debugging
```bash
# Show browser window (headful mode)
HEADLESS=false pytest tests/e2e/test_full_task_orchestration_e2e.py::TestFullTaskOrchestrationE2E::test_simple_qa_task_end_to_end -v

# Slow down browser interactions (500ms per action)
SLOW_MO=500 pytest tests/e2e/test_full_task_orchestration_e2e.py -v

# Increase timeout for slow environments
TIMEOUT=60000 pytest tests/e2e/test_full_task_orchestration_e2e.py -v
```

## Expected Results

### Baseline (All Pass)
- 10 tests passed in ~100-150 seconds (10-15s per test)
- <5% flake rate (occasional timing issues are acceptable)
- All screenshots saved to `tests/e2e/screenshots/` on failure

### Common Issues & Mitigations

| Issue | Cause | Fix |
|-------|-------|-----|
| Timeout waiting for element | Console not ready | Increase `TIMEOUT` env var |
| Test flakes intermittently | Slow network/system | Add retries, increase sleeps |
| API not called | Form validation blocks | Check form validation UI |
| Task doesn't complete | Backend timeout | Increase task timeout, check server logs |
| Output not rendered | Missing test ID | Update selectors if UI changed |
| Browser crashes | Out of memory | Run headless, reduce concurrent tasks |

## Architecture Notes

### Test Fixtures (from conftest.py)

```python
# Provided by conftest.py
page              # Playwright page object
test_task_data    # Sample task inputs (QA, analysis, invalid)
api_helper        # HTTP client for API calls
screenshot_helper # Screenshot utility for debugging
form_helper       # Form interaction helper
```

### API Endpoints Used

```
POST   /api/v2/task/submit          # Submit new task
GET    /api/v2/task/{task_id}/status # Get task status
GET    /api/v2/task/{task_id}/output # Get task output
POST   /api/v2/task/{task_id}/cancel # Cancel task
```

### UI Selectors (Expected)

```
textarea[name="taskInput"]     # Task input field
select[name="taskType"]        # Task type dropdown
button:has-text("Submit")      # Submit button
[data-testid="task-id"]        # Task ID display
[data-testid="task-output"]    # Task output display
[data-testid="progress-bar"]   # Progress indicator (optional)
[role="alert"]                 # Error messages
```

## Continuous Integration

### GitHub Actions Example
```yaml
- name: Start Console
  run: |
    cd core/console/corvin_console
    npm install && npm run build
    npm run start &
    sleep 10  # Wait for startup
    
- name: Run E2E Tests
  run: |
    pytest tests/e2e/test_full_task_orchestration_e2e.py -v --tb=short
    
- name: Upload Screenshots
  if: failure()
  uses: actions/upload-artifact@v3
  with:
    name: e2e-screenshots
    path: tests/e2e/screenshots/
```

## Quality Gates

- ✅ All 10 tests pass locally
- ✅ <5% flake rate (acceptable for browser tests)
- ✅ Performance: <60s per test
- ✅ No visual regressions (screenshots reviewed on failure)
- ✅ Coverage: All critical user journeys covered

## Next Steps

### Week 4 Friday Gate
- [ ] All tests passing locally
- [ ] Performance baseline established (<60s per test)
- [ ] Flake rate <5% after 3 consecutive runs
- [ ] Screenshots/video recording working
- [ ] Ready for CI/CD integration

### Post-Gate Improvements
1. Add visual regression testing (Percy, Playwright Visual Testing)
2. Expand to Safari/Firefox browsers (currently Chrome only)
3. Add load testing (multiple concurrent users)
4. Integrate with APM (Datadog, New Relic) for monitoring
5. Create performance dashboards (test duration trends)

## Troubleshooting

### Tests Won't Start
```bash
# 1. Check Console is running
curl http://localhost:8765/console/ -s | head -20

# 2. Check API is running
curl http://localhost:8000/api/v2/health -s | jq .

# 3. Check Playwright browsers installed
playwright install
python -m pytest --version
```

### Tests Timeout
```bash
# Increase timeout to 120 seconds
TIMEOUT=120000 pytest tests/e2e/test_full_task_orchestration_e2e.py -v

# Or use headful mode to see what's happening
HEADLESS=false TIMEOUT=120000 pytest tests/e2e/test_full_task_orchestration_e2e.py::TestFullTaskOrchestrationE2E::test_simple_qa_task_end_to_end -v
```

### Flaky Tests
```bash
# Run single test multiple times to check stability
pytest tests/e2e/test_full_task_orchestration_e2e.py::TestFullTaskOrchestrationE2E::test_concurrent_tasks_isolated --count=5 -v
```

---

**Last Updated:** 2026-08-29  
**Status:** Ready for Week 3-4 execution  
**Author:** Claude Code (auto-generated E2E test suite)
