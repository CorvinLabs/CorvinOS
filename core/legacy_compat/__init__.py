"""
Legacy Compat Layer — Phase C: Removal (ADR-0538)

This package is being decomissioned as part of Phase C (Weeks 9–12).

All deprecated APIs have been removed:
- Brain Engineering APIs (get_session_context, recall_recent_sessions) — REMOVED Week 9
- Vibe Engineering APIs (delegate_to_persona) — REMOVED Week 10
- Context v1 APIs (create_snapshot_v1) — REMOVED Week 11
- Entire module directories — REMOVED Week 12

Migration guide: see docs/deprecated/CONTEXT_V1_MIGRATION.md
Use ACP Skills (os.context_adapter, etc.) directly instead.
"""

__all__ = [
    # All compat modules removed in Phase C
]
