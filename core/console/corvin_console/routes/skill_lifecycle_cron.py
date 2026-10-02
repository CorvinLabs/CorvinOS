"""Daily cron job for CEL skill lifecycle cleanup (ADR-0409, Commit 4).

Runs daily (midnight UTC) to delete turn-lifecycle skills older than 24h.
Prevents skill manifest from growing unbounded over time.
"""

from datetime import datetime, timedelta
from pathlib import Path
import sys
import logging

logger = logging.getLogger(__name__)


def cleanup_ephemeral_skills_daily(tenant_ids: list = None, ttl_hours: int = 24) -> dict:
    """Delete all turn-scoped CEL skills older than TTL.

    Args:
        tenant_ids: List of tenant IDs to clean. If None, clean all tenants.
        ttl_hours: Hours before a turn-skill is considered expired (default: 24).

    Returns: {
        'tenant_results': {
            'tenant_id': {
                'deleted_count': int,
                'errors': [str],
                'audit_events': [str]
            }
        },
        'total_deleted': int,
        'total_errors': int
    }
    """
    results = {
        'tenant_results': {},
        'total_deleted': 0,
        'total_errors': 0
    }

    if not tenant_ids:
        # If no tenants specified, get all tenant directories
        try:
            from corvin_operator.forge.forge.paths import corvin_home
            tenant_root = Path(corvin_home()) / "tenants"
            if tenant_root.exists():
                tenant_ids = [d.name for d in tenant_root.iterdir() if d.is_dir()]
            else:
                logger.warning(f"Tenant root not found: {tenant_root}")
                return results
        except Exception as e:
            logger.error(f"Failed to discover tenant IDs: {e}")
            return results

    now = datetime.utcnow()

    for tenant_id in tenant_ids:
        tenant_result = {
            'deleted_count': 0,
            'errors': [],
            'audit_events': []
        }

        try:
            # Import skill registry dynamically
            _sf_dir = str(Path(__file__).resolve().parents[2] / "operator" / "skill-forge")
            if _sf_dir not in sys.path:
                sys.path.insert(0, _sf_dir)

            from skill_forge.registry import SkillRegistry
            from corvin_operator.forge.forge.paths import tenant_home

            skill_root = Path(tenant_home(tenant_id)) / "skill-forge"
            if not skill_root.exists():
                tenant_result['audit_events'].append(
                    f"skill_lifecycle_cron: tenant {tenant_id} - no skill registry"
                )
                results['tenant_results'][tenant_id] = tenant_result
                continue

            registry = SkillRegistry(skill_root)

            # List all skills
            try:
                all_skills = registry.list()
            except Exception as e:
                tenant_result['errors'].append(f"Failed to list skills: {str(e)}")
                results['tenant_results'][tenant_id] = tenant_result
                continue

            # Filter and delete old turn-lifecycle skills
            for skill in all_skills:
                try:
                    skill_meta = registry.get(skill.name if hasattr(skill, 'name') else skill)

                    if not skill_meta:
                        continue

                    lifecycle = getattr(skill_meta, 'lifecycle', None) or 'durable'
                    created_at_str = getattr(skill_meta, 'created_at', None)

                    # Only clean up turn-lifecycle skills
                    if lifecycle != "turn":
                        continue

                    # Parse created_at timestamp and check age
                    if created_at_str:
                        try:
                            created_at = datetime.fromisoformat(created_at_str)
                            age = now - created_at

                            if age > timedelta(hours=ttl_hours):
                                skill_name = skill.name if hasattr(skill, 'name') else skill
                                try:
                                    registry.delete(skill_name, reason=f"cron_ttl_expired:{ttl_hours}h")
                                    tenant_result['deleted_count'] += 1
                                    tenant_result['audit_events'].append(
                                        f"skill_lifecycle_cron: deleted turn-skill {skill_name} "
                                        f"(age: {age.total_seconds()/3600:.1f}h, tenant: {tenant_id})"
                                    )
                                except Exception as e:
                                    tenant_result['errors'].append(
                                        f"Failed to delete skill {skill_name}: {str(e)}"
                                    )
                        except ValueError:
                            # created_at is not a valid ISO timestamp
                            tenant_result['errors'].append(
                                f"Invalid created_at timestamp: {created_at_str}"
                            )
                except Exception as e:
                    tenant_result['errors'].append(f"Error processing skill: {str(e)}")

        except Exception as e:
            tenant_result['errors'].append(f"Tenant cleanup failed: {str(e)}")

        results['tenant_results'][tenant_id] = tenant_result
        results['total_deleted'] += tenant_result['deleted_count']
        results['total_errors'] += len(tenant_result['errors'])

    return results


def log_cleanup_results(results: dict) -> None:
    """Log cleanup results for audit trail and monitoring."""
    if results['total_deleted'] == 0 and results['total_errors'] == 0:
        logger.info("skill_lifecycle_cron: no cleanup needed")
        return

    logger.info(
        f"skill_lifecycle_cron: cleaned up {results['total_deleted']} turn-skills, "
        f"{results['total_errors']} errors across {len(results['tenant_results'])} tenants"
    )

    for tenant_id, tenant_result in results['tenant_results'].items():
        if tenant_result['deleted_count'] > 0:
            logger.debug(
                f"tenant {tenant_id}: deleted {tenant_result['deleted_count']} skills"
            )

        for error in tenant_result['errors']:
            logger.warning(f"tenant {tenant_id}: {error}")

        for event in tenant_result['audit_events']:
            logger.debug(event)


# Cron job entry point (called by systemd timer or scheduler)
def run_daily_cleanup() -> None:
    """Entry point for daily cron execution (systemd timer)."""
    try:
        results = cleanup_ephemeral_skills_daily(ttl_hours=24)
        log_cleanup_results(results)
    except Exception as e:
        logger.error(f"Daily skill lifecycle cleanup failed: {e}", exc_info=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_daily_cleanup()
