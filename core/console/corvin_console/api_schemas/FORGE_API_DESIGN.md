# Unified Forge API Design

**Status:** Design Phase (Schema Only)  
**Version:** 1.0  
**Last Updated:** 2026-09-12

## Overview

The Unified Forge API consolidates three subsystems into a single console control plane:

1. **Tools** — MCP tools (read-only discovery, enable/disable lifecycle)
2. **Skills** — SkillForge skills (versioning, rollback, learning state)
3. **OS-Skills** — Kernel-level skills (delegation_router, context_adapter, etc.)

This document describes the **schema design only**. Implementation (route handlers) is pending.

## Architecture

### Three Subsystems

```
Forge Control Plane
├── Tools (MCP tools)
│   ├── GET /v1/console/forge/tools
│   ├── GET /v1/console/forge/tools/<id>
│   ├── POST /v1/console/forge/tools/<id>/enable
│   └── POST /v1/console/forge/tools/<id>/disable
├── Skills (SkillForge)
│   ├── GET /v1/console/forge/skills
│   ├── GET /v1/console/forge/skills/<id>
│   ├── POST /v1/console/forge/skills/<id>/rollback
│   ├── POST /v1/console/forge/skills/<id>/pin
│   └── POST /v1/console/forge/skills/<id>/feedback
├── OS-Skills (Kernel skills)
│   ├── GET /v1/console/forge/os-skills
│   ├── GET /v1/console/forge/os-skills/<id>
│   └── POST /v1/console/forge/os-skills/<id>/config
└── Cross-System
    ├── GET /v1/console/forge/graph
    ├── GET /v1/console/forge/audit
    └── GET /v1/console/forge/search
```

### Design Principles

| Principle | Implementation |
|-----------|-----------------|
| **Audit-First** | Every state change (enable/disable/config) emits audit event before returning (ADR-0537) |
| **Tenant Isolation** | All queries filtered by `tenant_id` from SessionRecord (ADR-0007) |
| **PII-Scrubbed** | Response data contains no user input, prompts, or sensitive text (GDPR Art. 32) |
| **Learning Integration** | Skills carry confidence scores, feedback history, convergence estimates (ADR-0314) |
| **Fail-Closed** | Invalid requests rejected with clear errors; no silent fallbacks |
| **Hash-Chained** | Audit trail events linked with SHA256 hashes (ADR-0232) |
| **Dependency Graph** | Cross-subsystem dependencies visible (A calls B, B depends on C) |
| **Dead Code Detection** | Unreachable nodes flagged (E2E wiring proof, ADR-0610) |

## Endpoint Families

### 1. Tools API

**Purpose:** Discover and manage MCP tools.

**Endpoints:**

| Method | Path | Description | Auth | Audit |
|--------|------|-------------|------|-------|
| GET | `/v1/console/forge/tools` | List all tools | session | — |
| GET | `/v1/console/forge/tools/<tool_id>` | Tool detail + state | session | — |
| POST | `/v1/console/forge/tools/<tool_id>/enable` | Enable tool | session + CSRF | `forge.tool_enabled` |
| POST | `/v1/console/forge/tools/<tool_id>/disable` | Disable tool | session + CSRF | `forge.tool_disabled` |
| POST | `/v1/console/forge/tools/batch-enable` | Enable multiple | session + CSRF | `forge.tool_enabled` (×N) |
| POST | `/v1/console/forge/tools/batch-disable` | Disable multiple | session + CSRF | `forge.tool_disabled` (×N) |

**Example Request:**

```bash
curl -X POST http://localhost:8765/v1/console/forge/tools/git_clone/enable \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: <token>" \
  -d '{"reason": "Adding git support for repo cloning"}'
```

**Example Response:**

```json
{
  "manifest": {
    "tool_id": "git_clone",
    "name": "Git Clone",
    "description": "Clone a git repository",
    "category": "version-control",
    "version": "1.0.0",
    "author": "Corvin Labs",
    "tags": ["git", "repo", "clone"],
    "requires_approval": false,
    "sandbox_required": true,
    "audit_enabled": true,
    "input_schema": {
      "type": "object",
      "properties": {
        "url": {"type": "string"},
        "destination": {"type": "string"}
      },
      "required": ["url", "destination"]
    },
    "output_schema": {"type": "object"}
  },
  "state": {
    "tool_id": "git_clone",
    "status": "enabled",
    "enabled": true,
    "call_count": 42,
    "error_count": 2,
    "last_called": "2026-09-12T14:35:22Z",
    "avg_latency_ms": 1250.5,
    "error_rate": 0.048,
    "depends_on_tools": [],
    "used_by_skills": ["github_integration", "repo_sync"]
  },
  "enabled_at": "2026-09-12T14:35:22Z",
  "enabled_by": "user_fingerprint"
}
```

**Audit Event:**

```json
{
  "event_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
  "event_type": "forge.tool_enabled",
  "timestamp": "2026-09-12T14:35:22.123Z",
  "resource_type": "tool",
  "resource_id": "git_clone",
  "action": "enable",
  "reason": "Adding git support for repo cloning",
  "after_state": {"enabled": true, "status": "enabled"},
  "user_id_fingerprint": "<hash>",
  "hash": "sha256(...)",
  "prev_hash": "sha256(...)"
}
```

### 2. Skills API

**Purpose:** Manage skill versions, rollback, and learning state.

**Endpoints:**

| Method | Path | Description | Auth | Audit |
|--------|------|-------------|------|-------|
| GET | `/v1/console/forge/skills` | List all skills | session | — |
| GET | `/v1/console/forge/skills/<skill_id>` | Skill detail + versions + learning state | session | — |
| POST | `/v1/console/forge/skills/<skill_id>/rollback` | Restore prior version | session + CSRF | `forge.skill_rollback` |
| POST | `/v1/console/forge/skills/<skill_id>/pin` | Pin to specific version | session + CSRF | `forge.skill_pinned` |
| POST | `/v1/console/forge/skills/<skill_id>/feedback` | Submit learning feedback | session + CSRF | `forge.skill_feedback` |

**Learning State (ADR-0314):**

Skills carry confidence scores updated by user feedback. The learning loop works:

1. Skill executes → SkillExecutedEvent logged
2. User provides feedback → SkillFeedbackRequest
3. Feedback integrated → confidence_score updated
4. Next execution uses updated confidence in routing/filtering decisions

**Example: Rollback Request**

```bash
curl -X POST http://localhost:8765/v1/console/forge/skills/data_classifier/rollback \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: <token>" \
  -d '{"version": "1.2.3", "reason": "Recent version has higher error rate"}'
```

**Example: Rollback Response**

```json
{
  "metadata": {
    "skill_id": "data_classifier",
    "name": "Data Classifier",
    "description": "Classify data by type and sensitivity",
    "current_version": "1.2.3",
    "available_versions": ["1.0.0", "1.1.0", "1.2.0", "1.2.3"],
    "author": "assistant",
    "origin": "personal",
    "scope": "project"
  },
  "learning_state": {
    "confidence_score": 0.87,
    "execution_count": 4521,
    "success_count": 3932,
    "avg_latency_ms": 123.4,
    "p95_latency_ms": 450.2,
    "recent_feedback": [
      {
        "timestamp": "2026-09-12T14:32:00Z",
        "type": "outcome",
        "signal": "correct",
        "confidence_delta": 0.02
      }
    ],
    "learning_active": true,
    "convergence_estimate": 0.92,
    "last_feedback_at": "2026-09-12T14:32:00Z"
  },
  "versions": [
    {
      "version": "1.2.3",
      "status": "active",
      "created_at": "2026-09-10T10:00:00Z",
      "test_count": 45,
      "test_pass_count": 45,
      "can_rollback_to": true,
      "pinned": false
    },
    {
      "version": "1.2.0",
      "status": "rollback_available",
      "created_at": "2026-09-08T15:30:00Z",
      "test_count": 50,
      "test_pass_count": 50,
      "can_rollback_to": true,
      "pinned": false
    }
  ]
}
```

**Example: Feedback Request**

```bash
curl -X POST http://localhost:8765/v1/console/forge/skills/data_classifier/feedback \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: <token>" \
  -d '{
    "execution_id": "exec-12345",
    "outcome": "correct",
    "confidence": 0.95,
    "notes": "Correctly identified PII patterns in financial data"
  }'
```

### 3. OS-Skills API

**Purpose:** Manage kernel-level skills with tunable configuration and learning loops.

**Endpoints:**

| Method | Path | Description | Auth | Audit |
|--------|------|-------------|------|-------|
| GET | `/v1/console/forge/os-skills` | List all OS-Skills | session | — |
| GET | `/v1/console/forge/os-skills/<skill_id>` | OS-Skill detail + config + metrics | session | — |
| POST | `/v1/console/forge/os-skills/<skill_id>/config` | Update parameters | session + CSRF | `forge.osskill_config_updated` |

**Examples:**

- `os.delegation_router` — which engine to route to based on task type (learnable: confidence_threshold)
- `os.context_adapter` — how to inject context into prompts (learnable: window_size, trim_strategy)
- `os.flow_guard` — data flow validation (learnable: sensitivity_thresholds)

**Example: Config Update Request**

```bash
curl -X POST http://localhost:8765/v1/console/forge/os-skills/os.delegation_router/config \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: <token>" \
  -d '{
    "parameter_updates": {
      "confidence_threshold": 0.65,
      "fallback_engine": "opus"
    },
    "reason": "Reducing false negatives in model selection",
    "dry_run": false
  }'
```

**Example: Config Update Response**

```json
{
  "success": true,
  "old_config": {
    "confidence_threshold": 0.75,
    "fallback_engine": "sonnet"
  },
  "new_config": {
    "confidence_threshold": 0.65,
    "fallback_engine": "opus"
  },
  "warnings": [
    "Lower threshold may route more tasks to Opus (higher cost)"
  ],
  "applied_at": "2026-09-12T14:35:22Z",
  "config_version": "os.delegation_router@v3.2.1"
}
```

### 4. Graph & Dependencies (ADR-0535)

**Purpose:** Visualize cross-system dependencies and detect dead code.

**Endpoint:**

```
GET /v1/console/forge/graph
→ GraphResponse
```

**Example Response:**

```json
{
  "nodes": [
    {
      "node_id": "git_clone",
      "node_type": "tool",
      "name": "Git Clone",
      "status": "enabled",
      "execution_count": 42,
      "error_rate": 0.048
    },
    {
      "node_id": "github_integration",
      "node_type": "skill",
      "name": "GitHub Integration",
      "status": "active",
      "execution_count": 315,
      "confidence_score": 0.89
    },
    {
      "node_id": "os.delegation_router",
      "node_type": "os-skill",
      "name": "Delegation Router",
      "status": "active",
      "execution_count": 50000,
      "confidence_score": 0.91
    }
  ],
  "edges": [
    {
      "source_id": "github_integration",
      "target_id": "git_clone",
      "edge_type": "calls",
      "call_count": 42,
      "error_count": 2
    },
    {
      "source_id": "os.delegation_router",
      "target_id": "github_integration",
      "edge_type": "depends_on",
      "call_count": 150,
      "error_count": 0
    }
  ],
  "circular_dependencies": [],
  "unreachable_nodes": [
    "deprecated_tool_v1",
    "legacy_skill_old"
  ],
  "layout_hint": "dag"
}
```

### 5. Audit Trail

**Purpose:** Read immutable, hash-chained audit events.

**Endpoint:**

```
GET /v1/console/forge/audit?since=<iso_timestamp>&type=<prefix>&limit=100
→ AuditQueryResponse
```

**Query Parameters:**

- `since`: ISO 8601 timestamp (return events after this time)
- `type`: Prefix filter (e.g., `forge.tool_*`, `forge.skill_*`, `forge.osskill_*`)
- `limit`: Max results (capped at 1000)
- `offset`: Pagination offset

**Example:**

```bash
curl 'http://localhost:8765/v1/console/forge/audit?since=2026-09-12T00:00:00Z&type=forge.skill_&limit=50'
```

**Example Response:**

```json
{
  "events": [
    {
      "event_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
      "event_type": "forge.skill_feedback",
      "timestamp": "2026-09-12T14:35:22.123Z",
      "resource_type": "skill",
      "resource_id": "data_classifier",
      "action": "feedback",
      "before_state": {"confidence_score": 0.85},
      "after_state": {"confidence_score": 0.87},
      "reason": null,
      "user_id_fingerprint": "<hash>",
      "hash": "sha256(event_payload)",
      "prev_hash": "sha256(prior_event)"
    }
  ],
  "total": 127,
  "limit": 50,
  "offset": 0,
  "chain_verified": true
}
```

### 6. Search

**Purpose:** Full-text search across all subsystems.

**Endpoint:**

```
GET /v1/console/forge/search?q=<term>&type=<type>&limit=<n>&offset=<n>
→ SearchResponse
```

**Query Parameters:**

- `q`: Search term (BM25 scored against name, description, tags)
- `type`: Filter by type (`tool`, `skill`, `os-skill`; omit for all)
- `limit`: Results per page
- `offset`: Pagination offset

**Example:**

```bash
curl 'http://localhost:8765/v1/console/forge/search?q=git&type=tool&limit=20'
```

**Example Response:**

```json
{
  "query": "git",
  "results": [
    {
      "id": "git_clone",
      "type": "tool",
      "name": "Git Clone",
      "description": "Clone a git repository",
      "match_score": 0.95,
      "matched_fields": ["name", "tags"],
      "status": "enabled",
      "enabled": true
    },
    {
      "id": "github_integration",
      "type": "skill",
      "name": "GitHub Integration",
      "description": "Interact with GitHub via git and API",
      "match_score": 0.72,
      "matched_fields": ["description"],
      "status": "active"
    }
  ],
  "total": 2,
  "limit": 20,
  "offset": 0,
  "facets": {
    "tool": 1,
    "skill": 1,
    "os-skill": 0
  }
}
```

## Request/Response Patterns

### Authentication & Tenant Isolation

All requests require a valid session. Tenant is extracted from `SessionRecord`:

```python
from ..deps import require_session

@router.get("/tools")
def list_tools(
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> ToolListResponse:
    tenant_id = _rec.tenant_id  # From session, never env var (ADR-0007)
    # Filter all responses by tenant_id
```

### CSRF Protection

State-changing endpoints (enable/disable/rollback/config) require CSRF token:

```python
from ..deps import require_csrf

@router.post("/tools/{tool_id}/enable")
def enable_tool(
    tool_id: str,
    req: ToolEnableRequest,
    _: Annotated[None, Depends(require_csrf)],
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> ToolResponse:
    # Emit audit event before returning
    # Audit event must include: tenant_id, user_id_fingerprint, action, reason
```

### Audit Emission (Load-Bearing)

Every state-changing endpoint **must** emit an audit event:

```python
# BEFORE responding
audit_event = {
    "event_id": generate_ulid(),
    "event_type": AuditEventType.TOOL_ENABLED,
    "timestamp": utcnow(),
    "resource_type": "tool",
    "resource_id": tool_id,
    "action": "enable",
    "reason": req.reason,
    "after_state": {"enabled": True, "status": "enabled"},
    "user_id_fingerprint": fingerprint(_rec.user_id),
    "tenant_id": _rec.tenant_id,
}

# Write to audit chain (appends to audit.jsonl, computes hash, links to prev)
audit_trail.write_event(audit_event, tenant_id=_rec.tenant_id)

# THEN return response
return ToolResponse(...)
```

**Critical:** Audit write must succeed before endpoint returns. If audit fails, the state change is rolled back (fail-closed).

### Error Handling

| Error | Status | Body |
|-------|--------|------|
| Tool not found | 404 | `{"error": "tool_not_found", "message": "..."}` |
| Invalid version | 400 | `{"error": "invalid_version", "message": "..."}` |
| Lock busy (config update in progress) | 503 | `{"error": "lock_busy", "message": "Retry in 5s"}` |
| Audit write failed | 507 | `{"error": "audit_unavailable", "message": "..."}` |
| Unauthorized | 403 | (FastAPI default) |
| Missing CSRF | 403 | (FastAPI default) |

## PII & Scrubbing

**Rules:**

1. **No user data in responses** — ever. No email, name, IP, phone.
2. **No prompts/transcripts** — even in error messages.
3. **User identity hashed** — `user_id_fingerprint = sha256(user_id + salt)`
4. **Reason field scrubbed** — audit reason goes through fail-closed scrubber before storage.

**Implementation:**

```python
from corvin_logging.scrubber import scrub_text

reason_scrubbed, was_modified = scrub_text(req.reason or "")
if was_modified:
    logging.warning(f"Audit reason contained PII patterns; scrubbed")

# Store scrubbed version
audit_event["reason"] = reason_scrubbed
```

## Integration Points

### With ADR-0314 (Learning)

Skills carry `learning_state` with confidence, feedback history, convergence estimates. Feedback endpoints (`POST /skills/<id>/feedback`) feed the learning loop:

```
SkillFeedbackRequest
  → outcome_sink.record_outcome()
  → learns from feedback
  → updates confidence_score
  → next execution uses updated score
```

### With ADR-0537 (Audit + LoM)

Every audit event carries:

- `hash`: SHA256 of event payload
- `prev_hash`: Hash of prior event (forms immutable chain)
- `lom`: Line-of-Moral-Responsibility (code location making the decision)

```python
from core.skills.skill_registry_phase1 import compute_lom_hash

lom = "core/console/corvin_console/routes/forge_tools.py:enable_tool:L42"
lom_hash = compute_lom_hash(lom)

audit_event = {
    "lom": lom,
    "lom_hash": lom_hash,
    "hash": compute_event_hash(audit_event),
    "prev_hash": <prior_event_hash>,
}
```

### With ADR-0535 (OS-Skills Composition)

OS-Skills dependencies are tracked in the graph endpoint:

```json
{
  "edges": [
    {
      "source_id": "os.delegation_router",
      "target_id": "os.context_adapter",
      "edge_type": "depends_on"
    }
  ]
}
```

## Pagination

List endpoints support pagination:

```
GET /v1/console/forge/tools?limit=20&offset=0
→ {"tools": [...], "total": 127, "limit": 20, "offset": 0}
```

- `limit`: Max results (capped at 1000)
- `offset`: Skip N results
- `total`: Total count (for UI progress bar)

## Filtering (Future)

Future versions may add filtering:

```
GET /v1/console/forge/tools?status=enabled&category=version-control&limit=20
```

(Not in Phase 1 schema, but reserve parameter space.)

## Rate Limiting (Future)

Future versions may add rate limiting on read-heavy endpoints (graph, audit). Consumers should respect `X-RateLimit-*` headers.

## Versioning

The API version is stable at `v1`. Schema changes are backward-compatible:

- Adding optional fields: OK
- Removing fields: Only after deprecation period
- Renaming fields: Not allowed (add new, deprecate old)
- Changing field types: Not allowed (use new field name)

## Implementation Roadmap

| Phase | Scope | Estimate | Status |
|-------|-------|----------|--------|
| **1** | Tools + Skills (GET endpoints) | 2–3w | — |
| **2** | Tools + Skills (POST endpoints + audit) | 2–3w | — |
| **3** | OS-Skills + config + learning | 2–3w | — |
| **4** | Graph + audit queries + search | 2w | — |
| **5** | Testing + E2E proof + docs | 2w | — |
| **Total** | Complete Forge API | 10–13w | — |

## Files

- **Schema (Python):** `core/console/corvin_console/api_schemas/forge_unified.py`
- **Schema (TypeScript):** `core/console/corvin_console/web-next/src/types/forge.ts`
- **This Doc:** `core/console/corvin_console/api_schemas/FORGE_API_DESIGN.md`

## Next Steps

1. **Code review:** Review schema design with backend team
2. **Frontend preview:** Generate OpenAPI docs, preview in Swagger UI
3. **E2E planning:** Draft test plan (pytest + playwright)
4. **Implementation:** Start Phase 1 (list endpoints)
5. **ADR migration:** Create ADR-TBD for Unified Forge API once ready
