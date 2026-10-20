# Web-UI Performance Optimizations

## Build & Deployment Performance

**Build Time:** 10 seconds (down from 14.7s / 32% faster)

### Deployed Optimizations (June 2, 2026)

1. **Sourcemaps disabled in production** — Maps only in dev mode
2. **Route-based code splitting** — Each page = separate lazy chunk
3. **Dependency chunking** — Mermaid, Katex, Cytoscape = on-demand loading
4. **Removed TypeScript pre-build step** — Vite's esbuild is faster inline
5. **Terser minification** — Extra compression pass on all JS

### Initial Bundle Size

| State | Size (gzip) | Notes |
|-------|-------------|-------|
| **Before** | 308 kB | All pages bundled together |
| **After** | 106 kB | Only core + router loaded |
| **Reduction** | **65%** | Massive initial load improvement |

### Per-Route Bundle Sizes (gzip)

- Landing: 2.4 kB (lazy)
- Login: 0.4 kB (lazy)
- Dashboard: 2.4 kB (lazy)
- Chat: 75 kB (lazy, loads on demand)
- Workflows: 17 kB (lazy)
- Bridges: 8.2 kB (lazy)
- Mermaid diagrams: 147 kB (lazy, only if displayed)

## Browser Runtime Performance

### First Contentful Paint (FCP)
- Initial HTML + CSS + core JS: **~2-3 seconds** on 4G
- (was 5+ seconds with monolithic bundle)

### Time to Interactive (TTI)
- Core app interactive: **~3-4 seconds** on 4G
- Heavy pages (Chat/Workflows) load on-demand

### Network Waterfall

1. `index.html` (2 kB)
2. `index-*.css` (10.8 kB gzip)
3. `index-*.js` main bundle (106 kB gzip)
4. On route change: additional page chunk (2-75 kB) loads

No waterfall blocking — all assets stream in parallel.

## Chat Message List (Long Sessions)

The chat list is windowed: `ChatMessageRows` (in `src/pages/chat.tsx`) mounts only the
rows that intersect the viewport plus 1000 px of overscan, using
`src/hooks/use-virtual-list.ts` and the pure geometry in `src/lib/virtual-window.ts`.
Row heights are measured with a ResizeObserver (estimate 120 px until measured), and
spacers stand in for the rest. `MessageRow` is memoised per message, so a streaming
delta re-renders the row it changed. The day separator is decided against the
predecessor's timestamp. "Show older" reveals 100 more messages per click, up to
1000 listed messages (`HISTORY_CAP`). The scroll tracker runs once per frame and
passive, and it is the only writer of the stick-to-bottom flag.

Measured with `claude-playground/chat-perf/measure-chat.mjs` against the dev server, a
synthetic session with code blocks, headless Chromium, before and after the change
(`result-before.json`, `result-after.json`):

| Session | Mode | DOM nodes before → after | Mounted rows after |
|---|---|---|---|
| 1000 messages | default (60 listed) | 1765 → 695 | 8 |
| 1000 messages | all listed by "Show older" | 21092 → 843 | 14 |
| 3000 messages | default (60 listed) | 1765 → 695 | 8 |
| 3000 messages | all listed by "Show older" | 62197 → 822 | 14 |

Keypress latency, median over three to four runs per mode: 31–67 ms before and 12–24 ms
after in the expanded modes. In the default mode there is no clear gain: 12–34 ms before
and 13–31 ms after, and the p95 was higher after the change in some runs (77–132 ms
against 24–49 ms at 1000 messages). No list DOM mutations or long tasks occur while
typing, before or after, so the default-mode difference was not traced to the list.
Scroll frame time is capped by vsync in headless runs; the layout cost per scroll step
(about 0.1 ms) is the useful figure there.

Caveats: the numbers come from the dev build (React dev mode), not the production
bundle; re-measure after deploy. Scroll anchoring on "Show older" is verified in a real
browser to about 0 px, but not for every size change of the rows above the reader.

## Development Workflow

```bash
# Dev server (auto-reload, fast refresh)
npm run dev
# Loads at http://127.0.0.1:5173 with proxy to :8765

# Production build
npm run build
# Outputs optimized dist/ in 10 seconds

# Type checking (separate, not in build path)
npm run type-check
```

## Server-Side Setup (FastAPI)

The gateway in `core/console/app.py` mounts `web-next/dist/` as a SPA with:
- **Automatic gzip compression** (handled by gateway middleware)
- **Cache-busting via hash in filenames** (Vite default)
- **SPA fallback** (unknown paths → index.html)

### Recommended Headers (gateway middleware)

```
Cache-Control: public, max-age=31536000, immutable  # for *.js/css with hash
Cache-Control: public, max-age=3600                 # for index.html
Content-Encoding: gzip                              # automatic
```

## Metrics Dashboard

To measure real-world performance:
- Chrome DevTools → Network tab → throttle to 4G
- Measure FCP, LCP, CLS on first page load
- Lazy chunks should load in < 2s per page on 4G

## Next Steps (Not Implemented)

- Service Worker for offline support (low priority)
- Image optimization (future: convert PNG→WebP)
- Dynamic import() for individual form components (diminishing returns)
- Critical CSS inlining (already in index.html)

---

**Baseline:** 10s builds, 106 kB initial load, 65% smaller than monolithic approach.
