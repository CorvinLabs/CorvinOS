"""
Brain Engineering Compat Layer — REMOVED (ADR-0538 Phase C)

This module was deprecated in Phase B and has been removed in Phase C.

For context tracking, use:
- `os.context_adapter` Skill (see core/skills/os_skills_phase1.py)
- Direct SkillSystemIntegration calls

See docs/deprecated/CONTEXT_V1_MIGRATION.md for migration details.
"""

# Phase C (Week 9): All deprecated APIs removed
# get_session_context() and recall_recent_sessions() are no longer available
# Use ACP Skills instead
