/**
 * Sync Monitor — folded into the unified GitHub Integration panel.
 *
 * Consolidation (2026-09-11): this page's worker start/stop controls and
 * status grid now live on /app/settings/github, alongside the connect/verify
 * flow they were describing state for. Its live event log connected to
 * `/v1/console/github/events`, a route that was never implemented server-side
 * — the page just reconnected every 5s forever and never showed a real event.
 *
 * The route stays mounted for deep-link stability (same pattern as
 * plugin-center.tsx's consolidation) but is dropped from the sidebar — see
 * NAV_EXEMPT in tests/unit/panel-nav-wiring.test.ts.
 */
export { default } from './github'
