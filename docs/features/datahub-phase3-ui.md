# DataHub Phase 3: Console UI + HTTP Wiring

**Status:** ✅ Production Ready (k=1-5 complete)  
**Implemented:** 2026-09-16  
**ADR:** ADR-0510 (VIBE Engineering Hub), ADR-0512 (Vector Semantic Layer)

---

## Overview

DataHub Phase 3 brings the unified data artifact creation system to the CorvinOS console via HTTP API + React UI.

### Features

- **Create** artifacts (Skills, Tools, Datasets, Pipelines) from data sources
- **List** all artifacts with pagination
- **Retrieve** artifact metadata and preview
- **Delete** artifacts (soft delete, audit-logged)

### Architecture

```
Console UI (React) → HTTP Routes (FastAPI) → DataHub Skills (Phase 1-2) → Storage
```

---

## HTTP API Reference

### Create Artifact

**Endpoint:** `POST /v1/console/datahub/create`

**Request:**
```json
{
  "name": "user_profiler_skill",
  "description": "Analyzes user behavior data",
  "creation_type": "skill",
  "data_source": "json",
  "data_path": "/data/users.json",
  "sample_rows": 100,
  "complexity": "medium"
}
```

**Response (201 Created):**
```json
{
  "artifact_id": "a3b4c5d6",
  "status": "completed",
  "message": "Artifact created successfully",
  "metadata": { ... }
}
```

### Retrieve Artifact

**Endpoint:** `GET /v1/console/datahub/{artifact_id}`

**Response (200 OK):**
```json
{
  "artifact_id": "a3b4c5d6",
  "name": "user_profiler_skill",
  "description": "Analyzes user behavior data",
  "creation_type": "skill",
  "created_at": "2026-09-16T10:30:45Z",
  "status": "completed",
  "test_count": 5,
  "validation_errors": []
}
```

### Delete Artifact

**Endpoint:** `DELETE /v1/console/datahub/{artifact_id}`

**Response (200 OK):**
```json
{
  "status": "deleted",
  "artifact_id": "a3b4c5d6"
}
```

### List Artifacts

**Endpoint:** `GET /v1/console/datahub/list?limit=50&offset=0`

**Response (200 OK):**
```json
{
  "items": [ ... ],
  "total": 42,
  "limit": 50,
  "offset": 0
}
```

---

## Console UI (React Component)

### DataHubPanel

**Location:** `core/console/corvin_console/web-next/src/components/DataHubPanel.tsx`

**Features:**
- **Create tab:** Form for new artifact creation
- **List tab:** Paginated artifact list with delete action
- **Status badges:** Visual indicator for artifact status
- **Error handling:** User-friendly error alerts

**Usage:**
```tsx
import { DataHubPanel } from '@/components/DataHubPanel';

export default function DataHubPage() {
  return <DataHubPanel />;
}
```

---

## Data Models

### Artifact Status
- `pending` → Creation in progress
- `completed` → Creation successful
- `failed` → Creation failed
- `deleted` → Soft deleted (record preserved)

### Creation Types
- `skill` → Reusable skill
- `tool` → MCP tool
- `dataset` → Data snapshot
- `pipeline` → Workflow

### Data Sources
- `json` → JSON file/array
- `csv` → CSV file
- `sql` → SQL query
- `api` → HTTP API
- `parquet` → Parquet file

---

## Tenant Isolation

All operations are tenant-scoped:

**Storage:** `~/.corvin/tenants/<tenant_id>/global/datahub_artifacts/`

**Security:** Every HTTP route filters by `SessionRecord.tenant_id`

---

## Audit Trail

All create/delete operations logged:

```json
{
  "timestamp": "2026-09-16T10:30:45Z",
  "event_type": "artifact_created",
  "artifact_id": "a3b4c5d6",
  "tenant_id": "_default",
  "created_by": "user@example.com"
}
```

---

## Testing

### Unit Tests
```bash
pytest tests/skills/test_datahub_unified_complete.py -v
```

### E2E Tests
```bash
pytest tests/skills/test_datahub_phase3_http_e2e.py -v
```

Tests cover: CRUD operations, pagination, conflict handling, error cases.

---

## Deployment

No special configuration required. Routes are always available once the console plugin boots.

---

## Implementation Files

- **HTTP Routes:** `core/console/corvin_console/routes/datahub_api.py`
- **React Component:** `core/console/corvin_console/web-next/src/components/DataHubPanel.tsx`
- **Tests:** `tests/skills/test_datahub_phase3_http_e2e.py`
- **App Integration:** `core/console/corvin_console/app.py`

---

**Version:** Phase 3 (HTTP + UI)  
**Last Updated:** 2026-09-16  
**Author:** Claude Haiku 4.5
