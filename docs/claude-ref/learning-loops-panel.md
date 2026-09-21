# Learning Loops Dashboard — Console Panel (ADR-0908)

Monitor plugin and skill learning loop health, events, and trends in real-time.

## Navigation

**URL:** `http://<host>:8765/console/app/learning-loops` | **Group:** Assistant | **Icon:** TrendingUp

**Also a tab** in the Learnings dashboard: `/console/app/vibe-engineering` →
"Learning Loops". Both mount the SAME component (`components/learning-loops-view.tsx`),
so the two surfaces cannot show different numbers for the same loops. A second
implementation reading the same endpoints would eventually disagree with the
first; one component in two places cannot.

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
| **Health Score** | the loop's measured success rate, or **null** when nothing measured one | 0.0–1.0, or null |
| **Status** | operational state | `active`, `dormant`, `stale`, `degrading`, `unknown` |
| **Events** | total recorded events, and how many fell in the last 7 days | count |
| **Trend** | health direction | ↑ up, ↓ down, → flat |

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

### Where the rows come from — two sources

| Source | `origin` | What it is | Read from |
|---|---|---|---|
| **OS skills** | `os_skill` | One loop per skill the tenant records learning events for — executions, task outcomes and operator feedback | `core.learning.event_store.EventStore`, the same store `GET /v1/console/learning/status` reports from |
| **CEL stages** | `cel_stage` | One loop per context-engineering pipeline stage; confidence earned from every turn's outcome | `ce_stage_grades.json` (ADR-0269 G4 / ADR-0285 G3) — the store `core.learning.earned_tree` projects |
| **Plugin-declared** | `plugin` | A loop an installed plugin declares via `learning_loops:` (ADR-0906) | the tenant loop index (ADR-0907) |

The first two are discovered live by `core/learning/loop_discovery.py`. They are
deliberately **not** indexed: their store already is the record, so reading it
live cannot drift from it the way a cached copy would. The third is indexed,
because a manifest declaration carries no metrics of its own — `/list`
reconciles the index against the installed plugins in-process, at most once
every five minutes, by calling `capabilities._get_learning_loops()` directly
rather than issuing an authenticated HTTP request to its own manifest endpoint.
Removed loops are archived (audited), never dropped.

Plugin-loop metrics fill in via `EventStore._update_kg_index`
(`core/learning/event_persistence.py`), which updates health, event count and
status for every learning event carrying both `plugin_id` and
`learning_loop_id`.

**As of 2026-09-21 this install shows 14 loops** — 6 OS skills and 8 CEL
stages — and no installed plugin declares a `learning_loops:` section.

### A health score means something measured one

`health.score` is **null** for a loop that has produced no outcome and no grade,
and the grid renders that as "not measured", never as an empty bar. An empty bar
reads as zero, and "nothing measured this" and "this fails everything" are the
opposite diagnosis. `health.basis` always names what a present score counts —
e.g. `29 of 31 recorded task outcomes succeeded (all time)`.

This matters on real data: `os.capabilities` has **2 294** recorded executions
and no health score at all, because an execution is not an outcome. Only
`os.delegation_router` records task outcomes, and only the CEL stages record
grades.

### `unknown` is not a fifth health state

It is the absence of an age. The CEL stage-grade records carry no timestamp, so
no age-based verdict about those loops can be read from the store — and "stale"
is a claim that a loop stopped emitting. A status of `unknown` says the age was
never recorded, not that something is wrong.

### Windows travel with their totals

A loop's headline health is all-time; the trend cards are the trend window. Both
say so in the UI. The list header names the number of learning events the scan
covered, and says when the scan bound was reached — a truncated pass describes a
different period than a complete one.

In the trend, a day with no recorded outcome has a **null** score and draws no
mark. Carrying the previous day's value forward, or writing 0.0, would both
assert something about a day that measured nothing.

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
| **active** | last event under 24h old | monitor for regression |
| **dormant** | last event 1–7 days old | may simply be idle |
| **stale** | last event over 7 days old | check whether the loop still runs |
| **degrading** | a **measured** score below 0.5, at any age | investigate; the score's basis says what it counts |
| **unknown** | the source store does not date its records, so no age can be read | not a fault — see above |

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
