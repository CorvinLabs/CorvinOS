# Learning Loops REST API Reference

Backend API for the Learning Loops console panel. Base: `/v1/console/learning-loops`

## Endpoints

### GET /list

**List all learning loops with status and health summary.**

**Query Parameters:**
- `plugin_id` (string, optional) — filter by plugin
- `skill_id` (string, optional) — filter by skill
- `status` (enum, optional) — `active|dormant|stale|degrading`
- `sort_by` (enum, default=`last_event`) — `plugin_id|status|last_event|health_score`
- `limit` (integer, default=50, max=500) — results per page
- `offset` (integer, default=0) — pagination offset

**Response:** `200 OK`
```json
{
  "loops": [
    {
      "loop_id": "plugin-a:default",
      "plugin_id": "plugin-a",
      "skill_id": null,
      "status": "active",
      "health": {
        "score": 0.92,
        "trend": "up"
      },
      "last_event": "2026-09-21T15:30:00Z",
      "event_count_7d": 42,
      "description": "Main router loop"
    }
  ],
  "total": 157,
  "timestamp": "2026-09-21T15:35:00Z"
}
```

**Performance:** <50ms (cached 2 minutes)  
**Tenant Isolation:** uses `session.tenant_id` (fail-closed)

---

### GET /{loop_id}/details

**Get full details for a single learning loop.**

**Response:** `200 OK`
```json
{
  "loop": {
    "loop_id": "plugin-a:default",
    "plugin_id": "plugin-a",
    "status": "active",
    "health": {"score": 0.92, "trend": "up"},
    "last_event": "2026-09-21T15:30:00Z",
    "event_count_7d": 42,
    "event_count_30d": 127,
    "description": "Main router loop",
    "owner": "plugin-a",
    "created_at": "2026-09-01T00:00:00Z"
  },
  "health_trend": {
    "points": [
      {"date": "2026-09-15", "health_score": 0.85, "event_count": 5},
      {"date": "2026-09-16", "health_score": 0.87, "event_count": 6}
    ],
    "min_score": 0.85,
    "max_score": 0.92,
    "avg_score": 0.89
  },
  "last_10_events": [
    {
      "timestamp": "2026-09-21T15:30:00Z",
      "event_type": "outcome_feedback",
      "skill_id": null,
      "signal": "task_success",
      "outcome": "success"
    }
  ],
  "recommendations": [
    "Health trending upward — no action needed",
    "42 events in 7d suggests active usage"
  ]
}
```

**Performance:** <200ms (cached 5 minutes)

---

### GET /{loop_id}/events

**Fetch audit log for a loop (raw learning events from core audit chain).**

**Query Parameters:**
- `limit` (integer, default=100, max=1000) — max events returned
- `offset` (integer, default=0) — pagination offset
- `event_type` (string, optional) — filter by type

**Response:** `200 OK`
```json
{
  "events": [
    {
      "timestamp": "2026-09-21T15:30:00Z",
      "event_type": "outcome_feedback",
      "skill_id": "router.v1",
      "signal": "task_success",
      "outcome": "success",
      "metadata": {
        "confidence": 0.95,
        "task_id": "task-123"
      }
    }
  ],
  "total_count": 3847,
  "limit": 100,
  "offset": 0
}
```

**Performance:** <500ms (not cached; reads live audit chain)

---

## Error Responses

| Status | Condition |
|---|---|
| `400 Bad Request` | invalid query param (e.g., `status=unknown`) |
| `404 Not Found` | loop_id does not exist |
| `503 Service Unavailable` | core.learning module not available (stripped install) |

**Example 503:**
```json
{
  "detail": "learning subsystem not wired (core.learning unavailable)"
}
```

---

## Caching & Performance

| Endpoint | TTL | Strategy |
|---|---|---|
| `/list` | 2 min | in-memory dict, TTL per tenant |
| `/{loop_id}/details` | 5 min | per-loop, keyed on loop_id |
| `/{loop_id}/events` | none | live audit query, fail-closed |

Cache keys are scoped by `tenant_id` (from `session.tenant_id`). Cross-tenant leakage is blocked by the require_session dependency (fail-closed).

---

## Authentication & Audit

All endpoints require:
- **Session:** `require_session` dependency (authenticated SessionRecord with `tenant_id`)
- **Audit:** all queries logged to core audit chain (GDPR Art. 30, 32)
- **Tenant Isolation:** queries filtered by `session.tenant_id` (GDPR Art. 6)

---

## Examples

**List active loops for a plugin:**
```bash
curl -s 'http://localhost:8765/v1/console/learning-loops/list?plugin_id=plugin-a&status=active' \
  -H "Cookie: session=..." | jq
```

**Get loop details and export:**
```bash
curl -s 'http://localhost:8765/v1/console/learning-loops/plugin-a:default/details' \
  -H "Cookie: session=..." | jq '.last_10_events' > events.json
```

**Stream recent events (pagination):**
```bash
for offset in 0 100 200; do
  curl -s "http://localhost:8765/v1/console/learning-loops/plugin-a:default/events?offset=$offset&limit=100" \
    -H "Cookie: session=..."
done
```

---

**Implementation:** `/core/console/corvin_console/routes/learning_analytics.py`  
**Schema:** `/core/console/corvin_console/routes/learning_analytics.py` (Pydantic models)  
**Backend Service:** `/core/knowledge_graph/mcp/learning_loop_service.py` (Phase 2)
