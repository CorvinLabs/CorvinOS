"""Session-close cleanup handler for CEL skills (ADR-0409, Commit 3).

When a session is closed, delete all session-scoped skills.
This prevents skill manifest bloat and ensures session isolation.
"""

from datetime import datetime
from pathlib import Path
import sys


def cleanup_session_skills(tenant_id: str, session_id: str) -> dict:
    """Delete all session-scoped CEL skills for a closed session.

    Returns: {
        'deleted_count': int,
        'errors': [str],
        'audit_events': [str]
    }
    """
    results = {
        'deleted_count': 0,
        'errors': [],
        'audit_events': []
    }

    try:
        # Import skill registry dynamically
        _sf_dir = str(Path(__file__).resolve().parents[3] / "operator" / "skill-forge")
        if _sf_dir not in sys.path:
            sys.path.insert(0, _sf_dir)

        from skill_forge.registry import SkillRegistry
        from corvin_operator.forge.forge.paths import tenant_home

        root = Path(tenant_home(tenant_id)) / "skill-forge"
        if not root.exists():
            results['audit_events'].append(
                f"skill_lifecycle_cleanup: session {session_id} - no skill registry found"
            )
            return results

        registry = SkillRegistry(root)

        # List all skills and filter by session_id + lifecycle="session"
        try:
            all_skills = registry.list()
        except Exception as e:
            results['errors'].append(f"Failed to list skills: {str(e)}")
            return results

        for skill in all_skills:
            try:
                # Check if skill is session-scoped and belongs to this session
                skill_meta = registry.get(skill.name if hasattr(skill, 'name') else skill)

                if not skill_meta:
                    continue

                lifecycle = getattr(skill_meta, 'lifecycle', None) or 'durable'
                skill_session_id = getattr(skill_meta, 'session_id', None)

                if lifecycle == "session" and skill_session_id == session_id:
                    skill_name = skill.name if hasattr(skill, 'name') else skill
                    try:
                        registry.delete(skill_name, reason=f"session_close:{session_id}")
                        results['deleted_count'] += 1
                        results['audit_events'].append(
                            f"skill_lifecycle_cleanup: deleted session-skill {skill_name} "
                            f"(session {session_id})"
                        )
                    except Exception as e:
                        results['errors'].append(
                            f"Failed to delete skill {skill_name}: {str(e)}"
                        )
            except Exception as e:
                results['errors'].append(f"Error processing skill: {str(e)}")

    except Exception as e:
        results['errors'].append(f"Skill lifecycle cleanup failed: {str(e)}")

    return results


def on_session_close(tenant_id: str, session_id: str) -> None:
    """Hook called when a session is closed (e.g., user clicks 'close chat').

    Deletes all session-scoped CEL skills to prevent manifest bloat.
    Best-effort: failures are logged but don't block session close.
    """
    result = cleanup_session_skills(tenant_id, session_id)

    # Log results for audit trail
    if result['deleted_count'] > 0:
        # Emit audit event (placeholder for actual audit integration)
        pass  # TODO: integrate with ADR-0314 audit events

    for error in result['errors']:
        # Log errors (placeholder for actual logging)
        pass  # TODO: integrate with structured logging

    for audit_event in result['audit_events']:
        # Emit audit events (placeholder)
        pass  # TODO: integrate with ADR-0314 audit events
