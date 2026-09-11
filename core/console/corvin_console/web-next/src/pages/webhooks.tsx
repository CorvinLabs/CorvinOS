/**
 * Webhooks — removed, folded into the unified GitHub Integration panel.
 *
 * Consolidation (2026-09-11): this page's "Register Webhook" button never
 * called the GitHub API — it wrote a local placeholder file (with a literal
 * `https://your-instance/...` URL) and reported success unconditionally.
 * There was no `/webhook/receive` endpoint to ever handle an incoming
 * GitHub event, so nothing it did was real. A genuine webhook needs a
 * publicly reachable callback URL, which a local console instance doesn't
 * have; the actual sync mechanism (a 5-minute polling worker) now lives on
 * /app/settings/github with a real Automatic Sync toggle.
 *
 * The route stays mounted for deep-link stability (same pattern as
 * plugin-center.tsx's consolidation) but is dropped from the sidebar — see
 * NAV_EXEMPT in tests/unit/panel-nav-wiring.test.ts.
 */
export { default } from './github'
