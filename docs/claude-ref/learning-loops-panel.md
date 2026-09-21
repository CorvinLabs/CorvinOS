# Learning Loops Dashboard — Console Panel (ADR-0908)

Monitor plugin and skill learning loop health, events, and trends in real-time.

## Navigation

**URL:** `http://<host>:8765/console/app/learning-loops` | **Group:** Assistant | **Icon:** TrendingUp

The console SPA is mounted under `/console/`, so the bare `/app/learning-loops`
is a 404 on the host — that is the router path inside the SPA, not a server
route. The panel needs BOTH registrations to be reachable: `PANELS`
(`web-next/src/panels/registry.tsx`) mounts the route, `NAV_GROUPS`
(`web-next/src/components/layout.tsx`) draws the sidebar entry.
`tests/unit/panel-nav-wiring.test.ts` keeps the two in sync.

## Overview

The Learning Loops panel provides observability into the self-learning feedback loops that power plugins and skills. Each loop continuously collects outcome signals, adjusts internal parameters, and reports health metrics.

### What is a Learning Loop?

A learning loop (declared by a plugin via `learning_loops:` manifest field) represents a self-improving mechanism:
- **Input:** plugin behavior, user feedback, task outcomes
- **Processing:** confidence scoring, parameter optimization
- **Output:** adjusted routing, skill weights, decision thresholds
- **Feedback:** health score, convergence rate, recommendations

### Key Metrics

| Metric | Meaning | Range |
|---|---|---|
| **Health Score** | P(loop is making correct decisions) | 0.0–1.0 (0=broken, 1=perfect) |
| **Status** | operational state | `active`, `dormant`, `stale`, `degrading` |
| **Events/7d** | feedback signals received | count (higher=more active) |
| **Trend** | health direction over 7 days | ↑ up, ↓ down, → flat |

## Using the Panel

### Grid View

**All Loops tab** displays every plugin/skill learning loop:
- **Sort by:** click column header (plugin, status, last_event, health)
- **Filter by status:** buttons at top (active, dormant, stale, degrading)
- **Click row:** view detailed metrics

### Detail View

**Details tab** opens after selecting a loop:
- **Metrics card:** health %, last event timestamp, 7d/30d event counts
- **Trend tab:** 7-day sparkline (min/avg/max), bars per day
- **Events tab:** audit log, event type breakdown, export to JSON/CSV
- **Export button:** download loop state for analysis

### Real-Time Updates

The panel polls `/v1/console/learning-loops/list` every 2 minutes; details are
fetched on demand. There is no WebSocket feed: `use-websocket-loop-updates.ts`
exists but points at `/v1/console/learning-loops/{id}/updates`, a route the
backend does not define, and nothing imports the hook. Wire the route before
wiring the hook.

### Where the rows come from

Loops are declared by installed plugins (`learning_loops:` in the manifest,
ADR-0906) and indexed per tenant (ADR-0907). The `/list` route reconciles the
index against the installed plugins in-process on each call, at most once every
five minutes — it calls `capabilities._get_learning_loops()` directly rather
than issuing an authenticated HTTP request to its own manifest endpoint.
New loops are added, loops a plugin no longer declares are archived (audited).

Runtime metrics fill in separately: `EventStore._update_kg_index`
(`core/learning/event_persistence.py`) updates a loop's health score, event
count and status for every learning event carrying both `plugin_id` and
`learning_loop_id`. An event without those is a no-op, and an event naming a
loop that is not indexed logs "index entry not found".

**As of 2026-09-21 no installed plugin declares a `learning_loops:` section**,
so the panel correctly shows an empty state naming that reason. It is not an
error and not a placeholder for sample data.

### Storage backend

The index is a tenant-scoped key/value store under
`<tenant_home>/learning_loop_index/`. It runs on **sqlite3 from the standard
library** by default; plyvel/LevelDB is used only when plyvel happens to be
installed, so an existing LevelDB directory stays readable. plyvel is in no
requirements file and is not installable on the Windows releases — requiring it
is what made every install answer 503.

## Status States

| Status | Meaning | Action |
|---|---|---|
| **active** | loop is healthy, receiving feedback | monitor for regression |
| **dormant** | loop has not emitted events in 24h | plugin may be unused |
| **stale** | loop has not emitted events in 7d | recommend archival |
| **degrading** | health score dropped >10% in 7d | investigate cause, consider rollback |

## Event Types

Events in the audit log represent:
- `outcome_feedback` — task succeeded/failed (source: task result)
- `skill_executed` — skill ran (decision point recorded)
- `learning_event_received` — feedback signal processed
- `skill_config_updated` — loop adjusted internal parameter
- `confidence_score_changed` — rooting decision confidence adjusted

## Interpreting Health Trends

**Health climbing (↑):** loop is learning; decisions improving.  
**Health declining (↓):** negative feedback accumulating; parameter tuning may be failing.  
**Health flat (→):** stable state; loop converged or no new feedback.

## Colour

The console is `data-theme` driven, so every colour carries an explicit dark
variant; a bare `bg-green-100 text-green-800` renders near-white on dark.

- **Status badges** colour the category by identity (ADR-0761): emerald
  (active), amber (dormant), orange (stale), red (degrading) — each with a
  `dark:` pair in `utils/learning-loop-formatting.ts`.
- **Health bars and the 7-day trend** draw on `var(--viz-tier-2)` over a
  `bg-muted` track. They encode the value as length; the hue is constant, so
  the bar does not double-encode its own number.
- **Trend arrows** are emerald (up) / red (down) / muted (flat), each with a
  dark variant.

## Export & Sharing

The detail view's **Export** button downloads one JSON file holding the loop
summary, its 7-day trend and the fetched events. There is no CSV export.

Use exports for:
- Compliance audits (GDPR Art. 30 — decision history)
- Performance analysis (trend graphs)
- Cross-team sharing of loop behavior

## Troubleshooting

| Issue | Cause | Fix |
|---|---|---|
| No loops displayed | no installed plugin declares `learning_loops:` | expected today; add the section to a plugin manifest (ADR-0906) |
| "Learning subsystem not available" (503) | `LearningLoopService` failed to construct — check `journalctl --user -u corvin-webui` for the import or storage error it logged | fix the underlying import; the route degrades rather than crashing, so the log is the only signal |
| Panel missing from the sidebar | `NAV_GROUPS` entry absent while `PANELS` has one | add both; `tests/unit/panel-nav-wiring.test.ts` catches it |
| Health stuck at 0% | loop received no feedback yet | plugin may not emit outcome signals |
| Events tab empty | loop has no audit events | wait 24h or trigger plugin behavior |

## References

- ADR-0908: Learning Loops Console Panel
- ADR-0906: Learning Loop Manifest Schema
- ADR-0907: Knowledge Graph MCP Learning Loop Service
- ADR-0314: Learning Infrastructure (event schema, persistence)

---

**Go-Live:** 2026-09-21 | **Last Updated:** 2026-09-21 (panel wired into the
sidebar, backend reachability fixed, manifest→index sync wired)
