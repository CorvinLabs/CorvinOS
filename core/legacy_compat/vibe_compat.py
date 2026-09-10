"""
Vibe Engineering Compat Layer — REMOVED (ADR-0538 Phase C)

This module was deprecated in Phase B and has been removed in Phase C.

For request routing, use:
- `os.delegation_router` Skill (see core/skills/os_skills_phase1.py)
- Direct SkillSystemIntegration calls

See docs/deprecated/VIBE_V1_MIGRATION.md for migration details.
"""

# Phase C (Week 10): All deprecated APIs removed
# delegate_to_persona() and VibeBrainAdapter are no longer available
# Use ACP Skills instead
