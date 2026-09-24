"""Task-Tracking SSOT (ADR-2051, amended by ADR-2056).

Per-tenant SQLite store of planned work items (initiative → epic → story →
task → subtask, plus issue / proposal). Runtime runs stay in their own stores
and are only linked. Every mutation is written to the core audit chain first.

  store.py    — path, schema, connection, per-tenant writer lock
  models.py   — request models + closed vocabularies
  service.py  — reads with derived rollups; audited mutations

The console surface is ``corvin_console/routes/task_tracking.py``
(``/v1/console/task-tracking/*``).
"""
