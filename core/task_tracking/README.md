# core/task_tracking — Task-Tracking SSOT

Per-tenant store of planned work items (initiative → epic → story → task →
subtask, plus issue / proposal). ADR-2051, amended by ADR-2056.

| File | Role |
|---|---|
| `store.py` | `<tenant_home>/global/task_tracking/tasks.db` (SQLite, WAL, 0o600), schema, per-tenant writer lock |
| `models.py` | closed vocabularies, parent rules, Pydantic v2 request models |
| `service.py` | reads with derived rollups; every mutation audit-first (core chain, then row, one transaction) |

Console surface: `core/console/corvin_console/routes/task_tracking.py`
(`/v1/console/task-tracking/*`). Importer for the legacy board:
`python -m corvin_console.task_tracking_import [--apply]`.

Tests: `core/console/tests/test_task_tracking_route.py` (HTTP, real chain),
`tests/task_tracking/test_service.py` (service).

Full reference: `docs/claude-ref/task-tracking-ssot.md`.
