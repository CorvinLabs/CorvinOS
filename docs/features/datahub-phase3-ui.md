# DataHub: Console Panel + HTTP API

**Status:** Working end to end since 2026-10-01 (backend E2E
`core/console/tests/test_datahub_api_e2e.py`, browser E2E
`web-next/tests/e2e/panel-datahub-unified.spec.ts`)
**ADRs:** ADR-0661 (security scan before any model input), ADR-0510

## What was broken until 2026-10-01

The panel had never produced an artifact:

- it posted to `/v1/datahub/ingest` and `/v1/datahub/create`, which do not exist (404);
- `POST /v1/console/datahub/create` answered 500 on every call (`rec.user_id` — the
  session record has no such field); a test with a hand-made session hid it;
- `GET /datahub/list` was declared after `GET /datahub/{artifact_id}` and never matched;
- CSV ingestion was a stub returning no rows; SQL/API/Parquet returned `[]` and still
  produced a "completed" artifact; the generated body was thrown away;
- `test_count` counted names of tests nothing ever wrote;
- the ADR-0661 security scanner never ran, nothing was audited, and `data_path`
  could read another tenant's files.

## Flow

```
DataHub panel (pages/datahub-unified.tsx)
  → POST /v1/console/datahub/analyze   read + scan the file, show rows/fields/scan
  → POST /v1/console/datahub/create    generate the artifact, store it
  → GET  /v1/console/datahub/list | /{id} | DELETE /{id}
        └─ core/skills/os_skills/datahub_unified  (ingest, describe)
           └─ core/skills/os_skills/data_hub/security/scanner.py  (ADR-0661 scanner)
```

One ingestion path and one scanner: `datahub_unified` scans every row with the
`data_hub` scanner; there is no second, unscanned path.

## HTTP API

All routes need a console session; `POST`/`DELETE` also need `X-CSRF-Token` (the
SPA's fetch wrapper adds it). The tenant comes from the session.

### Source fields (analyze and create)

| Field | Rule |
|---|---|
| `data_source` | `json` or `csv`. `sql`, `api`, `parquet` → 400 "not supported on this build" |
| `data_path` | a file on the host, ≤ 50 MB, extension matching `data_source`. A path inside the Corvin home that is not this tenant's home → 400 |
| `sample_rows` | 1–10000 (default 100) |

### POST /datahub/analyze

→ `{"analysis": {"row_count", "sample_rows", "schema", "completeness", "security": {"secret", "pii", "injection"}}}`

`completeness` counts `null` and `""` as empty. `security` counts matches from the
ADR-0661 scanner; values are never returned.

### POST /datahub/create (201)

Body: the source fields plus `name`, `description`, `creation_type`
(`skill`/`tool`/`dataset`/`pipeline`), `complexity` (`low`/`medium`/`high`).

| Answer | When |
|---|---|
| 201 `{artifact_id, status, message, metadata}` | created (`status: completed`, or `failed` with `validation_errors`) |
| 409 | a live artifact with this name exists |
| 422 | the source contains a secret, or has no rows |
| 400 | invalid source (see above) or unreadable file |

`metadata`: `artifact_id, name, description, creation_type, created_at, status,
row_count, validation_errors`. The generated text describes the rows actually read.

### GET /datahub/list?limit=50&offset=0

→ `{items: [metadata…], total, limit, offset}`, newest first, deleted ones omitted.

### GET /datahub/{artifact_id}

→ metadata plus `body` (the generated text). `artifact_id` is 8 hex characters;
anything else → 404.

### DELETE /datahub/{artifact_id}

Soft delete → `{"status": "deleted", "artifact_id"}`.

## Storage and audit

Artifacts: `<tenant global>/datahub_artifacts/<artifact_id>.json`, written atomically
at mode `0600`. Each create and delete chains one `console.action_performed` record
(`action: datahub.create | datahub.delete`, `target_kind: datahub_artifact`,
`target_id: <artifact_id>`) to the tenant audit chain. Names, descriptions and data
never enter the chain.

## Tests

```bash
pytest core/console/tests/test_datahub_api_e2e.py        # real router + session, sandbox chain
pytest tests/skills/test_datahub_unified_complete.py
cd core/console/corvin_console/web-next && \
  npx playwright test tests/e2e/panel-datahub-unified.spec.ts --project=chromium   # live console
```

The browser spec uses plain `@playwright/test`, not `panel-fixtures`: that fixture
mocks `/auth/whoami` without a `csrf_token`, under which no raw-fetch mutation can
succeed.

## Not covered

The DataHub Creator workspace (`panels/datahub-creator/`, ADR-0878) is a separate
surface backed by `routes/datahub_creator_routes.py` (`/v1/console/datahub/projects*`). It is
registered in neither `PANELS` nor `NAV_GROUPS`, so no route mounts it.
