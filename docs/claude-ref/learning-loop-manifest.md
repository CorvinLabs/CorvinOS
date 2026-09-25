# Learning Loop Manifest — ADR-0906 Feature 1 Documentation

**Status:** Feature 1 (Static Discovery) IMPLEMENTED, Phase 1 Iteration 1 ✅

## Overview

Learning loops are now **declaratively discoverable** via plugin.json + skill.json manifests. The Console can index them without scanning audit trails.

## For Plugin Authors

### Declaring a Learning Loop

In your `plugin.json`, add a top-level `learning_loops` array:

```json
{
  "plugin_id": "recommender/feedback-optimizer",
  "name": "Feedback Optimizer",
  "version": "1.2.0",
  "learning_loops": [
    {
      "id": "confidence_routing",
      "description": "Confidence score predictor for task routing",
      "event_source": "SkillExecutedEvent.confidence_score",
      "feedback_types": ["outcome_feedback", "preference_feedback"],
      "aggregation": "rolling_mean_7d",
      "health_threshold": 0.5,
      "dormancy_alert_hours": 24,
      "owner_skill": "os.delegation_router"
    }
  ]
}
```

### Required Fields

| Field | Type | Constraints | Purpose |
|-------|------|---|---|
| `id` | string | regex: `^[a-z0-9_-]+$`, ≤200 chars | Unique within plugin |
| `description` | string | ≤200 chars | Human-readable label |
| `event_source` | string | Format: `<EventType>.<field>` | What data to listen to (ADR-0314) |
| `feedback_types` | array | Non-empty, valid types | `[outcome_feedback, preference_feedback, confidence_score, metric_observed]` |
| `aggregation` | string | See below | Health computation strategy |
| `health_threshold` | float or null | null or [0.0, 1.0] | Degradation threshold |
| `dormancy_alert_hours` | int or null | null or > 0 | Hours before "inactive" |

### Optional Fields

| Field | Type | Purpose |
|-------|------|---------|
| `owner_skill` | string | Skill ID that drives this loop (e.g., `os.delegation_router`) |
| `metadata` | object | Custom labels (reserved key: `internal: bool`) |

### Valid Event Sources

Learning loops listen to audit events from ADR-0314. Examples:

```
SkillExecutedEvent.confidence_score
SkillExecutedEvent.latency_ms
MemoryDecisionEvent.irrelevance_score
MemoryDecisionEvent.token_waste_ratio
FeedbackEvent.user_satisfaction
```

Check the audit chain schema for your event type.

### Valid Aggregations

| Aggregation | Formula | Use When |
|---|---|---|
| `rolling_mean_7d` | Average over past 7 days | Trending score (default) |
| `percentile_p95` | 95th percentile | High-confidence outliers matter |
| `percentile_p50` | Median | Robust aggregation |
| `count` | Event count | Monitoring frequency |
| `sum` | Total | Budget-tracking loops |
| `latest` | Most recent value | Real-time snapshots |

## For Operators

### Viewing Learning Loops

**Console URL:** `/console/learning/loops`

Or via API:
```bash
curl http://127.0.0.1:8765/v1/console/learning/loops
```

Response format:
```json
{
  "status": "success",
  "learning_loops": [
    {
      "loop_id": "test/learning_loop_manifest:confidence_routing",
      "plugin_id": "test/learning_loop_manifest",
      "owner_skill": "os.delegation_router",
      "description": "Confidence score predictor for task routing — test loop",
      "event_source": "SkillExecutedEvent.confidence_score",
      "feedback_types": ["outcome_feedback", "preference_feedback"],
      "aggregation": "rolling_mean_7d",
      "health_threshold": 0.5,
      "dormancy_alert_hours": 24,
      "last_event_ts": "2026-09-21T14:32:00Z",
      "event_count_7d": 342,
      "health_score": 0.78,
      "status": "active"
    }
  ],
  "count": 1
}
```

### Loop Status Meanings

| Status | Meaning | Action |
|--------|---------|--------|
| `active` | Receiving events, health OK | All good ✓ |
| `dormant` | No events for N hours (dormancy_alert_hours) | Check event source |
| `stale` | No events in 7 days | Loop may be dead |
| `degrading` | Health score < health_threshold | Loop quality declining |

### Monitoring

**Pre-flight Checklist:**
- [ ] All declared loops appear in `/v1/console/learning/loops`
- [ ] Loop status is `active` (not `dormant` or `stale`)
- [ ] Event count > 0 in past 7 days
- [ ] Health score ≥ health_threshold

## Implementation Details (for developers)

### Files Added (Feature 1, k=1)

| File | Role |
|------|------|
| `core/learning/learning_loop_manifest.py` | `LearningLoop` dataclass + `ManifestParser` |
| `core/console/corvin_console/routes/learning_loops.py` | Console API routes |
| `tests/fixtures/learning_loop_manifest_test_plugin.json` | Test fixture with 2 example loops |
| `tests/integration/test_learning_loop_manifest_e2e.py` | E2E validation tests |

### API Contract

**GET `/v1/console/learning/loops`**
- Returns all registered learning loops
- Response: `{ status, learning_loops[], count }`

**GET `/v1/console/learning/loops/{loop_id}`**
- Returns details for a specific loop
- Response: `{ status, loop }`

### Manifest Integration

The capability manifest (ADR-0904) now includes learning_loops:

```
GET /v1/console/capabilities/manifest
→ { manifest_version, panels, skills, plugins, learning_loops }
```

## Next Steps (k=2, k=3, k=4, k=5)

- **k=2:** Wire real plugin registry (currently seeded from test fixture)
- **k=3:** Emit seed events from plugins to populate `event_count_7d`, `health_score`
- **k=4:** KG MCP integration for health computation (ADR-0907)
- **k=5:** Console UI panel for loop monitoring + health trends

---

## Implementation Progress

| Iteration | Scope | Status | Date |
|-----------|-------|--------|------|
| **k=1** | Static manifest discovery (test fixture) | ✅ COMPLETE | 2026-09-25 |
| **k=2** | Real plugin scanning + health enrichment | ✅ COMPLETE | 2026-09-25 |
| **k=3** | Audit chain health computation | ⏳ TODO | Next |
| **k=4** | KG MCP indexing (ADR-0907) | ⏳ TODO | TBD |
| **k=5** | Console UI panel + monitoring | ⏳ TODO | TBD |

### k=2: Real Plugin Discovery (2026-09-25)

**What was added:**
- Plugin registry scanning (`_bootstrap_learning_loops_from_plugins`)
- Health data enrichment (`_enrich_loop_with_health_data`)
- Caching layer for performance
- 7 new E2E tests

**How it works:**
1. On first `/v1/console/learning/loops` request, scan all `plugin.json` files
2. Extract `learning_loops` declarations from each manifest
3. Enrich with health data (k=2: synthetic, k=3: real audit chain)
4. Cache results for repeated requests
5. Gracefully skip invalid or missing loops

**Files Changed (k=2):**
- `core/console/corvin_console/routes/learning_loops.py` (+120 LoC)
- `tests/integration/test_learning_loop_manifest_e2e.py` (+95 LoC)

**Reference:** ADR-0906, CONCEPT-0051, ADR-0314 (audit events)
