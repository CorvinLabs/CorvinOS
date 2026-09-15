---
name: corvinOS_review_frontend
description: CorvinOS Code Review: Web-UI Testing + E2E + Accessibility + Lighthouse
---

# CorvinOS Review: Frontend & E2E Testing

## PHASE 6: WEB-UI TESTING (React/TypeScript)

### 6a. Build & Type Safety
- [ ] `npm run build` succeeds (no ts-errors)?
- [ ] ESLint/Prettier clean?
- [ ] No hard-coded hex-colors (use CSS-tokens)?

### 6b. Accessibility (a11y)
- [ ] ARIA labels on interactive elements?
- [ ] Semantic HTML (button, nav, main)?
- [ ] Keyboard navigation works?
- [ ] Color contrast ≥ 4.5:1 (WCAG AA)?

### 6c. Responsive Design
- [ ] Mobile/tablet/desktop tested?
- [ ] No horizontal scrollbars?
- [ ] Touch-targets ≥ 44x44px?

### 6d. Visual Regression
- [ ] Before/after screenshots for UI-changes?
- [ ] CSS-classes consistent with design-system?
- [ ] No unexpected layout-shifts (CLS)?

### 6e. Console Clean
- [ ] No JavaScript errors in DevTools?
- [ ] Warnings limited to expected sources?

---

## PHASE 7: E2E TESTING (Playwright)

### 7a. Critical User Flows
- [ ] Login flow working?
- [ ] Consent gate (`/consent on|off`) working?
- [ ] Feature-specific flows (new/edit/delete) tested?
- [ ] Error states handled gracefully?

### 7b. Test Coverage
- [ ] New features have E2E tests?
- [ ] Coverage ≥ impact of change?
- [ ] Flaky tests? (retry logic, race conditions?)

### 7c. Security-Touching Code
- [ ] Forge UI: tool creation/execution E2E green?
- [ ] Skill-Forge UI: skill creation E2E?
- [ ] Policy editor: changes persist correctly?

---

## PHASE 8: PERFORMANCE

### 8a. Lighthouse
- [ ] Mobile score ≥ 90 for critical pages?
- [ ] Largest Contentful Paint (LCP) < 2.5s?
- [ ] Cumulative Layout Shift (CLS) < 0.1?

### 8b. Bundle Size
- [ ] No unexpected growth (>10% = flag)?
- [ ] Lazy-loading where sensible?
- [ ] Dead code eliminated?

### 8c. Runtime Performance
- [ ] No N+1 API calls?
- [ ] Memoization where needed?
- [ ] Event-listener cleanup on unmount?

---

## DECISION MATRIX

| Finding | Action |
|---|---|
| Build fails | REJECT |
| a11y critical failure | REQUEST CHANGES |
| E2E test fails | REQUEST CHANGES |
| Lighthouse < 80 | REQUEST CHANGES |
| Security flow untested | REQUEST CHANGES |
| All E2E green, perf OK | → Continue to Backend Logic |

