# Learning Loops Dashboard — Console Panel (ADR-0908)

Monitor plugin and skill learning loop health, events, and trends in real-time.

## Navigation

**Path:** `/app/learning-loops` | **Group:** Assistant | **Icon:** TrendingUp

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

Dashboard polls `/v1/console/learning-loops/list` every 2 minutes. Details are fetched on-demand. WebSocket updates (future) will push live changes without polling.

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

## Dark Mode

All colors adapt to light/dark theme:
- Status badges: green (active), yellow (dormant), orange (stale), red (degrading)
- Health bars: emerald (good), amber (fair), rose (poor)
- Trend arrows: green (up), red (down), gray (flat)

## Export & Sharing

**Export options** in detail view:
- **JSON:** full loop state + 100 recent events
- **CSV:** spreadsheet-compatible event log

Use exports for:
- Compliance audits (GDPR Art. 30 — decision history)
- Performance analysis (trend graphs)
- Cross-team sharing of loop behavior

## Troubleshooting

| Issue | Cause | Fix |
|---|---|---|
| No loops displayed | no plugins installed with learning loops | install a plugin with `learning_loops:` declared |
| "Learning subsystem not available" | core.learning module not available | rebuild console, reinstall package |
| Health stuck at 0% | loop received no feedback yet | plugin may not emit outcome signals |
| Events tab empty | loop has no audit events | wait 24h or trigger plugin behavior |

## References

- ADR-0908: Learning Loops Console Panel
- ADR-0906: Learning Loop Manifest Schema
- ADR-0907: Knowledge Graph MCP Learning Loop Service
- ADR-0314: Learning Infrastructure (event schema, persistence)

---

**Go-Live:** 2026-09-21 | **Last Updated:** 2026-09-21
