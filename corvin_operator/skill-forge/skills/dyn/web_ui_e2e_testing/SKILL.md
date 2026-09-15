---
name: web_ui_e2e_testing
description: End-to-end web UI testing protocol — phases 0-4: orient, navigation, every button/control, every form/input, multi-step workflows. Always pair with [[web_ui_e2e_quality]] for states, usability, and reporting.
---

# Web UI — E2E Testing Protocol (Part 1: Interaction)

**Fire when asked to test, verify, or QA a running web interface.**
Complete all phases below, then run [[web_ui_e2e_quality]] for states + usability.

---

## Phase 0 — Orient (2 min cap)

1. Open the app. Note URL, title, and initial visible state.
2. Open DevTools Console — **keep open the entire session**. Red errors = blockers.
3. Open Network → Fetch/XHR. Note baseline requests on load.
4. Map the UI: what pages/views exist? What is the nav structure?
5. Identify the **golden path** (core user task) — test this first.

---

## Phase 1 — Navigation & Routing

For every nav item (sidebar, topbar, tabs, breadcrumbs):

- Click it → correct view loads, URL updates.
- Browser Back → correct previous view, no crash.
- Browser Refresh → page does not 404 or unexpectedly reset state.
- Direct URL navigation → deep links must work.
- Invalid route → is there a 404 page?

**Usability checks:**
- Active nav item visually highlighted?
- Page title (tab + h1) reflects the current section?
- No orphan pages reachable only by guessing a URL?

---

## Phase 2 — Every Interactive Control (button-by-button)

For **each button, link, icon-button, FAB, dropdown trigger, toggle**:

| Step | What to check |
|------|---------------|
| Hover | Cursor → pointer? Tooltip if icon-only? |
| Click (happy path) | Expected action fires. Network request sent. UI updates. |
| Click (loading) | Button disabled/spinner while in-flight? Double-click fires twice? |
| Click (error path) | DevTools → offline → click → error message shown, not silent fail. |
| Click (disabled) | Trigger the disabled condition. Non-clickable + visual cue. |

**Flag immediately:**
- Button that does nothing on click.
- Destructive button (Delete/Reset/Archive) without a confirmation dialog.
- Icon-only button without `aria-label` or tooltip.
- Action that can be double-submitted.

---

## Phase 3 — Every Form & Input Field

For **each text input, textarea, select, checkbox, radio, date-picker, file-upload, slider**:

### 3a — Happy path
Enter valid value → submit → success state (toast, redirect, reload confirms save).

### 3b — Validation

| Input | What to try |
|-------|-------------|
| Required | Leave empty → submit → inline error shown (not console-only). |
| Email | `not-an-email`, `a@`, `@b.com` → rejected. |
| Number | Letters, negative, out-of-range → rejected with message. |
| Text | 500+ chars — truncated gracefully or rejected with limit message. |
| Text | `<script>alert(1)</script>` — rendered as text, never executed. |
| File | Wrong MIME, too large, empty → rejected with readable message. |
| Date | Out-of-allowed-range dates → rejected. |

### 3c — Form UX
- Tab order follows visual top-to-bottom.
- Enter key submits single-field forms only.
- Character counter shown for max-length fields.
- Password show/hide toggle works.
- On submit error: focus moves to first invalid field.
- On success: form resets or navigates — not left in stale state.

---

## Phase 4 — Workflows (Multi-Step & Nested)

A workflow = any sequence of actions that must happen in order.

**Identify workflows:** wizards, create→configure→publish flows, actions that unlock after prerequisites, bulk operations.

### Test every branch per workflow:

```
Happy path:    step1 → step2 → step3 → success
Back tracking: step2 → BACK → step1 — is state preserved?
Abandon:       step2 → navigate away — data lost? user warned?
Error mid-way: step2 returns error — can retry from step2?
Refresh:       refresh at step2 — resumes at step2 or resets to step1?
```

### Nested interactions (modal inside modal, tab inside dialog):
- Close inner → outer still intact?
- ESC closes innermost layer only (not all layers)?
- Body scroll locked when modal is open?
- Tab key stays inside modal (focus trap)?

---

## Handoff to Part 2

After completing phases 0-4, immediately run **[[web_ui_e2e_quality]]** for:
- Phase 5: Data & State (empty, loading, pagination, real-time)
- Phase 6: Error & Edge States
- Phase 7: Usability Heuristics (Nielsen)
- Phase 8: Responsiveness & Accessibility
- Phase 9: Test Report template
