"""Task-Tracking SSOT (ADR-2051, amended by ADR-2056).

Per-tenant SQLite store of planned work items (initiative → epic → story →
task → subtask, plus issue / proposal). Runtime runs stay in their own stores
and are only linked. Every mutation is written to the core audit chain first.

  store.py    — path, schema, connection, per-tenant writer lock
  models.py   — request models + closed vocabularies
  service.py  — reads with derived rollups; audited mutations
  audit.py    — Phase C (k=2) AuditEvent dataclass, hash-chain writer, PII-Detection integration

The console surface is ``corvin_console/routes/task_tracking.py``
(``/v1/console/task-tracking/*``).

Audit Trail Integration (ADR-0232 + ADR-0297):
  Every task mutation creates an immutable AuditEvent with cryptographic hash-chain linking.
  Events are stored in the core audit chain (source of truth) and referenced in the task_tracking.events table.
  PII-Detection (ADR-0297) is applied fail-closed before persistence.
"""

from .audit import AuditEvent, emit_task_audit_event, verify_audit_chain

__all__ = ["AuditEvent", "emit_task_audit_event", "verify_audit_chain"]
