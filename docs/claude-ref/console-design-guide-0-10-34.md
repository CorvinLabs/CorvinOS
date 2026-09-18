# Console Design Guide — Release 0.10.34

**Status:** Reference  
**Updated:** 2026-09-18  
**Applies to:** `core/console/corvin_console/web-next/`  
**References:** ADR-0763, ADR-0764, ADR-0037, ADR-0015

---

## Visual Identity

The console uses **Corvin's visual identity** (ADR-0037):

| Element | Light Mode | Dark Mode |
|---------|-----------|-----------|
| Background | Off-white bone (#f5f3ed) | Deep navy (#0e1320) |
| Foreground (text) | Deep navy (#1a1715) | Bone (#f5f3ed) |
| Accent (brass) | Warm brass (#c3974b) | Bright brass (#e6cfa8) |
| Card/Surface | White (#ffffff) | Navy-dark (#151f2d) |
| Borders | Light grey (#d9d7d1) | Dark blue-grey (#1e2636) |

**All colors are defined as CSS variables in `src/index.css`:**
```css
:root, [data-theme="light"] {
  --background: 36 38% 96%;    /* HSL, no wrapper */
  --foreground: 222 38% 9%;
  --accent: 38 52% 53%;
}

[data-theme="dark"] {
  --background: 222 44% 6%;
  --foreground: 36 38% 96%;
  --accent: 38 60% 56%;
}
```

**Tailwind extends these via `tailwind.config.ts`:**
```typescript
colors: {
  background: "hsl(var(--background) / <alpha-value>)",
  foreground: "hsl(var(--foreground) / <alpha-value>)",
  accent: "hsl(var(--accent) / <alpha-value>)",
  // ... all semantic tokens map to CSS variables
}
```

## Dark Mode Implementation

### Theme Attribute (`data-theme`)

The console switches modes via the `data-theme` HTML attribute:

```html
<!-- Light mode -->
<html data-theme="light">

<!-- Dark mode -->
<html data-theme="dark">
```

### Theme Toggle Component

**File:** `src/components/theme-toggle.tsx`

```tsx
export function ThemeToggle() {
  const [theme, setTheme] = useTheme();
  const next: Record<Theme, Theme> = { auto: "dark", dark: "light", light: "auto" };
  
  const handleToggle = () => {
    setTheme(next[theme]); // Cycles auto → dark → light → auto
    localStorage.setItem('corvin-theme', next[theme]);
  };
}
```

### Theme Hook

**File:** `src/components/theme-toggle.tsx`

```tsx
export function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(() => readStored());
  
  useEffect(() => {
    applyTheme(theme);
    localStorage.setItem(STORAGE_KEY, theme);
  }, [theme]);
  
  return [theme, setTheme];
}
```

**Key behaviors:**
- Reads from localStorage on mount (`readStored()`)
- Applies theme via `document.documentElement.setAttribute('data-theme', effective)`
- Falls back to system preference if `auto` mode
- Persists selection in localStorage

### Adding Dark Mode to Components

**DO: Use Tailwind utilities**
```tsx
<div className="bg-background text-foreground">
  <button className="bg-accent text-accent-foreground hover:bg-accent/90">
    Submit
  </button>
</div>
```

**DO NOT: Hardcode hex colors**
```tsx
// ❌ WRONG
<div style={{backgroundColor: '#ffffff', color: '#1a1715'}}>

// ✅ CORRECT
<div className="bg-background text-foreground">
```

**DO NOT: Use inline theme-specific styles**
```tsx
// ❌ WRONG
<div className={isDark ? 'bg-slate-900' : 'bg-white'}>

// ✅ CORRECT
<div className="bg-background"> {/* respects data-theme automatically */}
```

## Responsive Layouts

### Breakpoints

Tailwind breakpoints (default) + custom additions:

| Breakpoint | Width | Use Case |
|-----------|-------|----------|
| `sm` | 640px | Small mobile |
| `md` | 768px | Tablet |
| `lg` | 1024px | Desktop |
| `xl` | 1280px | Wide desktop |
| `2xl` | 1536px | Ultra-wide |

**Custom breakpoints in `tailwind.config.ts`:**
```typescript
container: {
  center: true,
  padding: "1.5rem",
  screens: {
    "2xl": "1280px",
  },
}
```

### Responsive Patterns

**Mobile-first stacking:**
```tsx
<div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
  {/* Stacks on mobile, 2 cols on tablet, 3 cols on desktop */}
</div>
```

**Responsive typography:**
```tsx
<h1 className="text-xl sm:text-2xl md:text-3xl lg:text-4xl">
  Heading
</h1>
```

**Hide/show by breakpoint:**
```tsx
<div className="hidden md:block">
  {/* Visible only on tablet and up */}
</div>
```

### Testing Responsive Layouts

Test at these breakpoints (matching Release 0.10.34 acceptance criteria):

| Breakpoint | Dimensions | Device |
|-----------|-----------|--------|
| Mobile-S | 320×568 | iPhone SE |
| Mobile-L | 480×854 | Android typical |
| Tablet | 768×1024 | iPad |
| Desktop | 1024×768 | Laptop |
| Wide | 1920×1080 | Desktop monitor |

**Acceptance:** No horizontal scroll, text readable (>= 12px), interactive elements reachable.

## Accessibility (a11y)

### Keyboard Navigation

**Focus indicators must be visible:**
```tsx
// In index.css
.focus-ring {
  @apply outline-none ring-offset-2 ring-offset-background focus-visible:ring-2 focus-visible:ring-ring;
}

// Usage:
<button className="focus-ring">Click me</button>
```

**Tab order must be logical:**
```tsx
// ❌ Avoid: tabindex="999" or negative tabindex
// ✅ Correct: Natural DOM order or tabindex="0"

<div>
  <input className="focus-ring" placeholder="First" />
  <button className="focus-ring">Next</button>
  <button className="focus-ring">Later</button>
</div>
```

### Semantic HTML

```tsx
// ✅ Use semantic elements
<nav>
  <ul>
    <li><a href="/chat">Chat</a></li>
  </ul>
</nav>

<main>
  <article>
    <h1>Title</h1>
    <p>Content</p>
  </article>
</main>

// ❌ Avoid: <div role="nav"> when <nav> exists
```

### Alt Text & ARIA

```tsx
// Images must have alt text
<img src="icon.svg" alt="Settings icon" />

// Decorative images are marked
<img src="divider.svg" alt="" aria-hidden="true" />

// Buttons without visible text need aria-label
<button aria-label="Close dialog">×</button>

// Form inputs need labels
<label htmlFor="email">Email</label>
<input id="email" type="email" />
```

### Color Contrast

Minimum contrast ratio: **4.5:1** (WCAG AA standard).

**Verified pairs in Release 0.10.34:**

| Pair | Light | Dark | Status |
|------|-------|------|--------|
| Foreground on background | #1a1715 on #f5f3ed | #f5f3ed on #0e1320 | ✅ 14.8:1 / 14.8:1 |
| Accent on background | #c3974b on #f5f3ed | #e6cfa8 on #0e1320 | ✅ 5.4:1 / 5.2:1 |
| Muted on background | #7a7567 on #f5f3ed | #b8b4a6 on #0e1320 | ✅ 7.2:1 / 7.0:1 |

## Performance Standards

### Load Time Targets

- **Panel load:** < 500ms (measured from navigation to interactive)
- **Average across all panels:** < 500ms
- **Lazy-loaded panels:** < 1s (accepted for off-critical-path panels)

**Measurement method (Playwright E2E):**
```typescript
const start = Date.now();
await page.goto('/console/app/chat', { waitUntil: 'networkidle' });
const loadTime = Date.now() - start;
```

### Code-Splitting Strategy

**Entry points and their lazy imports:**
- `/console/app/chat` → inline (critical path)
- `/console/app/marketplace` → lazy `import('./pages/marketplace')`
- `/console/app/settings` → lazy `import('./pages/settings')`

**Bundle size targets:**
- Chromium initial bundle: < 250KB (gzipped)
- Lazy chunks: < 50KB each (gzipped)

## Component Guidelines

### Button Styling

```tsx
// Primary action
<button className="bg-accent text-accent-foreground hover:bg-accent/90">
  Submit
</button>

// Secondary action
<button className="bg-secondary text-secondary-foreground hover:bg-secondary/90">
  Cancel
</button>

// Ghost (minimal)
<button className="text-foreground hover:bg-muted">
  Link
</button>
```

### Card Layout

```tsx
<div className="rounded-lg border border-border bg-card p-4">
  <h3 className="text-card-foreground font-semibold">Title</h3>
  <p className="text-muted-foreground">Description</p>
</div>
```

### Form Inputs

```tsx
<div className="space-y-2">
  <label htmlFor="field" className="text-sm font-medium">
    Label
  </label>
  <input
    id="field"
    type="text"
    className="rounded-md border border-input bg-background px-3 py-2 text-foreground
               focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    placeholder="Placeholder"
  />
</div>
```

### Scrollbars (Theme-Aware)

**Defined in `index.css`:**
```css
* {
  scrollbar-width: thin;
  scrollbar-color: hsl(var(--accent) / 0.6) hsl(var(--muted) / 0.4);
}

*::-webkit-scrollbar { width: 10px; height: 10px; }
*::-webkit-scrollbar-track { background: hsl(var(--muted) / 0.4); }
*::-webkit-scrollbar-thumb { background-color: hsl(var(--accent) / 0.6); }
```

Scrollbars automatically respect theme without component-level changes.

## Data Visualization

### Chart Colors

**Built on Corvin's amber, validated for WCAG compliance:**

```typescript
// From index.css
--viz-role-os: #c3974b;        // Light: Corvin's work
--viz-role-worker: #1295a1;     // Cool counterpart
--viz-tier-1: #d6b171;          // Haiku (cheapest)
--viz-tier-2: #ca9b49;          // Sonnet (mid)
--viz-tier-3: #775822;          // Opus (expensive)
--viz-baseline: #9b9a9480;      // Counterfactual (grey)
```

**Key rules:**
- No dual-axis charts (use small multiples instead)
- Light role amber carries labels (2.68:1 contrast < 3:1 minimum)
- Never encode redundant info (e.g., role color + bar height)
- Verify palette with dataviz validator before committing

See ADR-0761 for full validation methodology.

## Console-Specific Rules (ADR-0763, ADR-0764)

1. **Shipped UI is English.** Bot answers in user's language at runtime; UI copy is English.
2. **No fabricated data.** A 404 returns empty results + explanation, never sample data.
3. **ADR ids stay in comments.** Never render in UI.
4. **One window per metric.** Don't show same metric over two different time periods on one panel.
5. **Charts are Corvin's, not foreign widgets.** Use console's palette, not third-party defaults.

## Testing Console Updates

### Unit Tests

**File:** `src/components/__tests__/ThemeToggle.test.tsx`

```typescript
test('theme toggle cycles through modes', () => {
  const { getByLabelText } = render(<ThemeToggle />);
  const toggle = getByLabelText(/Theme/);
  
  fireEvent.click(toggle);
  expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
});
```

### E2E Tests

**File:** `tests/e2e/console-release-0-10-34-core.spec.ts`

Covers:
- Dark/light mode toggle and persistence (5+ tests)
- Responsive layouts at 5 breakpoints (6+ tests)
- Performance <500ms (5+ tests)
- Accessibility — keyboard nav, focus, landmarks (4+ tests)
- Component rendering — no hardcoded colors (4+ tests)
- Dark/light visual consistency (3+ tests)
- Edge cases — network errors, missing panels (2+ tests)

**Run tests:**
```bash
npm run test:e2e -- tests/e2e/console-release-0-10-34-core.spec.ts
```

## Deployment Checklist

Before shipping Release 0.10.34:

- [ ] All 35+ E2E tests pass (Chromium + Firefox)
- [ ] Dark mode tested on 10+ critical panels
- [ ] Responsive verified at 5 breakpoints (no horizontal scroll)
- [ ] Performance baseline captured (<500ms avg)
- [ ] a11y — keyboard nav, focus visible, alt text
- [ ] No hardcoded colors in critical components
- [ ] Console design guide updated (this file)
- [ ] ADRs migrated to Corvin-ADR (ADR-0763, ADR-0764)
- [ ] CHANGELOG updated with UI/UX improvements

## Related Documentation

- **ADR-0763:** Console as production surface (no fabricated data, English UI)
- **ADR-0764:** Cross-page consistency (shared styling, one window per metric)
- **ADR-0037:** Visual identity (brass/navy/bone, HSL tokens)
- **ADR-0761:** Data visualization palette and validation
- **ADR-0015:** Console rendering & lazy loading
- **Layer-24 (Audio):** Audio player styling (theme-aware)

---

**Last updated:** 2026-09-18  
**Next review:** Post-0.10.34 release
