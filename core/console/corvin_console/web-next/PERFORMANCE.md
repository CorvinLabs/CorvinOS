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
