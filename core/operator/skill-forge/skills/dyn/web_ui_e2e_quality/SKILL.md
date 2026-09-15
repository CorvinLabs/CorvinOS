---
name: web_ui_e2e_quality
description: Web UI testing — Part 2: data/state edge cases, error states, Nielsen usability heuristics, responsiveness, accessibility audit, and structured test report template. Always run after [[web_ui_e2e_testing]].
---

# Web UI — E2E Testing Protocol (Part 2: Quality & Reporting)

**Run after [[web_ui_e2e_testing]]** (phases 0-4 must be complete).

---

## Phase 5 — Data & State

### Empty states
- Delete all records → empty-state illustration + CTA shown (not a blank page)?
- Load views with 0 items from a fresh/empty account.

### Loading states
DevTools → Network → throttle to "Slow 3G":
- Every list/table/card shows skeleton or spinner.
- No layout shift or invisible content while loading.

### Pagination / infinite scroll
- Navigate to page 2 → Back → returns to page 2 (not reset to page 1)?
- Infinite scroll appends (not replaces) content.
- Last page: no "Load more" button when nothing remains.

### Real-time / live data
- Open same view in two tabs. Update in tab A → does tab B reflect it (if feature promises live updates)?

### Persistence
- Fill a draft → refresh → draft preserved (localStorage or server)?
- Log out → log back in → user preferences retained?

---

## Phase 6 — Error & Edge States

| Scenario | Expected behavior |
|----------|-------------------|
| API returns 500 | Human-readable error banner, not a raw JSON dump. |
| API returns 401/403 | Redirect to login or "unauthorized" screen, not a broken page. |
| Network offline | Offline indicator or retry button — never silent. |
| Session timeout | Prompt to re-authenticate, not a cryptic 401. |
| Very large dataset | Table/list does not freeze (virtualization or pagination). |
| Special chars in content | `& < > " '` rendered correctly everywhere. |
| Concurrent edit | Last-write-wins warning or conflict resolution (if applicable). |

---

## Phase 7 — Usability Heuristics (Nielsen — score each Pass / Warn / Fail)

1. **System status** — UI always tells the user what is happening (loading, saving, error, success).
2. **Real-world match** — Labels and icons use words the user knows, not internal codenames.
3. **User control** — Undo available for destructive actions. Cancel always reachable.
4. **Consistency** — Primary action = filled button, secondary = outline. Red = danger, green = success.
5. **Error prevention** — Confirmation before irreversible actions. Impossible actions are disabled, not left to fail.
6. **Recognition over recall** — No need to remember info from a previous screen. Context preserved.
7. **Flexibility** — Power users can use keyboard shortcuts. Filters/search available on long lists.
8. **Minimalist design** — No information overload. Each page has a clear primary action.
9. **Error recovery** — Error messages say what went wrong AND what to do next.
10. **Help & docs** — Tooltips, placeholder text, or doc links where actions are non-obvious.

---

## Phase 8 — Responsiveness & Accessibility

### Responsive (quick pass)
- 375 px wide (iPhone SE): no horizontal scroll on main flows.
- 768 px (tablet): sidebar collapses, tables scroll horizontally — layout adapts gracefully.

### Accessibility
- Tab through the entire page with no mouse — every interactive element reachable.
- Focus outline visible at all times (never `outline: none` without an alternative).
- Images have `alt` text. Icon buttons have `aria-label`.
- Color is not the only state indicator (red border + icon + text, not just color alone).
- DevTools → Lighthouse → Accessibility → run audit. Target score: ≥ 90.

---

## Phase 9 — Test Report (required before "done")

```markdown
## Test Report — [Page/Feature] — [Date]

### Blockers (must fix before release)
- [ ] BUG: [description] | Steps: [1. … 2. …] | Expected: […] | Actual: [...]

### Warnings (should fix)
- [ ] UX: [issue] — [recommendation]

### Suggestions (nice to have)
- [ ] IDEA: [improvement] — [rationale: what user expects vs. what happens]

### Usability Heuristics
| Heuristic            | Score | Notes |
|----------------------|-------|-------|
| System status        |       |       |
| Real-world match     |       |       |
| User control         |       |       |
| Consistency          |       |       |
| Error prevention     |       |       |
| Recognition > recall |       |       |
| Flexibility          |       |       |
| Minimalist design    |       |       |
| Error recovery       |       |       |
| Help & docs          |       |       |

### Accessibility
Lighthouse score: __ / 100
Issues: [list or "none found"]
```

---

## Final Checklist — Before Declaring "Done"

- [ ] DevTools Console: zero red errors on all tested pages
- [ ] Every button and link clicked at least once
- [ ] Every form: happy path + ≥ 3 invalid inputs tested
- [ ] Every workflow: happy path + abandon + error branch
- [ ] Empty state, loading state, error state confirmed for all data views
- [ ] Responsive check at 375 px done
- [ ] Lighthouse accessibility score ≥ 90 (or deviations documented)
- [ ] Test report written with blockers, warnings, and suggestions
