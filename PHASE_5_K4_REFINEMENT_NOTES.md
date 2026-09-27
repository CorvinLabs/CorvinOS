# Phase 5: K=4 Refinement + Testing (Framework Ready)

**Status:** Test structure created, test cases outlined, ready for implementation + manual E2E

**File:** `tests/e2e/test_phase5_skill_manager_e2e.py` (400 LoC framework, 20 test cases outlined)

---

## Test Coverage Plan (20 tests, ~50 LoC each = ~1000 LoC when implemented)

### Manual E2E Tests (Required Before Merge)

| Scenario | Steps | Pass Criteria |
|---|---|---|
| **Install from Marketplace** | 1. GET /available, 2. Click install, 3. Watch polling, 4. Verify complete | Status progresses: pending → downloading → extracting → installing → complete |
| **Non-Admin Rejection** | 1. Non-admin user, 2. POST /install, 3. Observe 403 | 403 Forbidden + error message |
| **Polling Timeout** | 1. Mock /status to hang, 2. Install skill, 3. Observe polling timeout after 5s | Error banner: "Status unavailable" |
| **Concurrent Installs** | 1. Install skill A, 2. Immediately install skill B, 3. Poll both | Both complete without conflicts |
| **Upload ZIP Validation** | 1. Upload .txt file, 2. Observe 400, 3. Upload .zip, 4. Verify install | Reject non-.zip, accept valid .zip |

### Automated Test Cases (K=4 Implementation)

**Category: Installed Tab (3 tests)**
1. `test_list_installed_skills_success` — fetch works, schema correct
2. `test_uninstall_skill_requires_admin` — 403 on non-admin
3. (TBD) — refresh button re-fetches

**Category: Available Tab (4 tests)**
4. `test_list_available_skills_with_search` — search filter works
5. `test_list_available_skills_with_category_filter` — category filter works
6. `test_install_from_marketplace_success` — full flow + audit event
7. `test_install_requires_admin` — 403 on non-admin

**Category: Upload Tab (3 tests)**
8. `test_upload_valid_zip_file` — install from upload
9. `test_upload_non_zip_file_rejected` — 400 on .txt
10. `test_upload_requires_admin` — 403 on non-admin

**Category: Polling (2 tests)**
11. `test_polling_timeout_on_hung_api` — 5s timeout triggers error
12. `test_polling_cadence_2_seconds` — verify 2s intervals

**Category: Concurrent (2 tests)**
13. `test_concurrent_installs_atomic` — both succeed
14. `test_install_while_uninstall_in_progress` — concurrent ops

**Category: Audit (3 tests)**
15. `test_skill_install_audit_event` — event logged + hash-chained
16. `test_skill_uninstall_audit_event` — event logged
17. `test_skill_upload_audit_event` — event logged

**Category: Admin Gate (5 tests)**
18. `test_install_forbidden_non_admin` — 403
19. `test_uninstall_forbidden_non_admin` — 403
20. `test_upload_forbidden_non_admin` — 403
21. `test_list_installed_allowed_non_admin` — 200 (read-only)
22. `test_list_available_allowed_non_admin` — 200 (read-only)

**Category: End-to-End (1 test)**
23. `test_full_skill_lifecycle` — install → list → uninstall → upload

---

## K=4 Refinement Checklist

### Code Quality

- [ ] React components use proper hooks (useEffect cleanup, useCallback deps)
- [ ] Error boundaries added (try-catch in fetch calls)
- [ ] Loading states everywhere (loading spinner, disabled buttons)
- [ ] Accessibility (labels, error messages, focus management)
- [ ] Responsive design (tested on mobile/tablet)

### Backend

- [ ] Audit events reach audit chain (not silently dropped)
- [ ] Admin gate tested with both admin + non-admin
- [ ] Polling response correctly updates React Context
- [ ] Timeout guard prevents hung requests

### Integration

- [ ] React components import correct types
- [ ] Context state flows to all tabs
- [ ] Progress bar updates on polling response
- [ ] Install tab refreshes after install completes

### Testing

- [ ] All 20+ test cases pass
- [ ] Manual browser test: install, uninstall, upload
- [ ] Adversarial: non-admin curl rejected
- [ ] Timeout: API hangs, error shown
- [ ] Concurrent: two installs in parallel succeed

---

## Known Gaps (K=3 → K=4)

| Gap | Fix | Priority |
|---|---|---|
| **Real SkillInstaller** | Import from Phase 4, call actual methods | HIGH (no mock data in prod) |
| **Marketplace data** | Fetch from real source (plugin registry? CDN?) | HIGH |
| **Polling state update** | Context needs setter for status/progress from polling response | HIGH |
| **Real audit integration** | Confirm `emit_audit()` reaches audit chain | HIGH |
| **Capabilities endpoint** | Add `is_admin` field (may exist already) | MEDIUM |
| **Console route registration** | Add `/app/skills` to navigation | MEDIUM |
| **Error recovery** | Retry logic on transient network failure | LOW (MVP ok without) |

---

## Test Fixtures to Create (K=4)

```python
@pytest.fixture
def admin_session():
    """Admin auth headers for mutations."""
    # Return: headers with valid admin session
    
@pytest.fixture  
def non_admin_session():
    """Non-admin auth headers (read-only)."""
    # Return: headers with non-admin session
    
@pytest.fixture
def sample_skill_zip():
    """Valid skill .zip for upload testing."""
    # Create: temp file with skill/plugin.json structure
```

---

## Manual E2E Checklist (Before Merge)

Run this in browser before declaring K=4 complete:

1. **Open `/app/skills`**
   - [ ] Tabs render (Installed, Available, Upload)
   - [ ] Installed tab shows current skills
   - [ ] Non-admin user: Available/Upload tabs hidden

2. **Install from Available Tab**
   - [ ] Search works (filter marketplace)
   - [ ] Category filter works
   - [ ] Click install → progress banner appears
   - [ ] Progress bar updates (0 → 100%)
   - [ ] Status text progresses (pending → downloading → complete)
   - [ ] Auto-hides after 5s
   - [ ] Installed tab now shows new skill

3. **Uninstall from Installed Tab**
   - [ ] Click uninstall → confirmation dialog
   - [ ] Progress banner appears
   - [ ] Polling updates progress
   - [ ] Installed tab refreshes
   - [ ] Skill removed from list

4. **Upload Tab**
   - [ ] Drag-drop zone appears
   - [ ] Can drag .zip file
   - [ ] Can select via file picker
   - [ ] Try .txt file → error "File must be .zip"
   - [ ] Upload valid .zip → progress starts
   - [ ] After complete → installed tab shows new skill

5. **Polling Timeout (Adversarial)**
   - [ ] Mock `/v1/skills/status` to hang indefinitely
   - [ ] Install skill → polling starts
   - [ ] Wait 5+ seconds
   - [ ] Error banner shows: "Status unavailable (network error)"
   - [ ] Unmock → polling resumes and completes

6. **Non-Admin Access (Adversarial)**
   - [ ] Log in as non-admin user
   - [ ] Installed tab visible and works (read-only)
   - [ ] Available/Upload tabs hidden
   - [ ] Try curl `/v1/skills/install` as non-admin
   - [ ] Get 403 Forbidden

---

## Integration with Phase 6 (Future)

K=5 documentation will include:
- ADR-0681: Console Skill Manager UI (design, audit events, admin gates)
- Integration points for Phase 6 (Marketplace discovery, real SkillInstaller)
- Known limitations (mock data, no real marketplace yet)

---

## Success Criteria for K=4 → K=5

✅ All 20 test cases pass (or clearly documented as K=5+ work)  
✅ Manual E2E: browser testing confirms install/uninstall/upload works  
✅ Adversarial: non-admin access rejected (403)  
✅ Polling: timeout guards work  
✅ Concurrent: two installs in flight succeed  
✅ Audit: events logged and hash-chained  
✅ No console errors or warnings  
✅ Ready for code review + merge to main  

**Estimated K=4 work: 2–3 hours** (tests + manual E2E + bug fixes)
