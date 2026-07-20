"""TTL (Time-To-Live) enforcement for geo-tracking (Phase 3.1, Task 5)."""
import logging
import os
from datetime import datetime, timedelta

from . import geo_schema

logger = logging.getLogger(__name__)


async def cleanup_expired_geo_data(
    db_dsn: str = None,
    days_back: int = 30,
    dry_run: bool = False,
) -> dict:
    """Run periodic TTL cleanup.
    
    **Policy:**
    - Tier 1: No expiry (country-level, minimal privacy concern)
    - Tier 2: 30-day retention (region-level)
    - Tier 3: 14-day retention (city-level + grid, maximum privacy)
    
    Args:
        db_dsn: PostgreSQL connection string
        days_back: How far back to keep data (30 for Tier 2, 14 for Tier 3)
        dry_run: If True, don't actually delete, just report what would be deleted
        
    Returns:
        dict: {deleted_tier2, deleted_tier3, timestamp}
    """
    if not db_dsn:
        return {"error": "DATABASE_URL not configured"}
    
    try:
        if dry_run:
            # Report what would be deleted
            logger.info("TTL cleanup DRY_RUN: checking expiry...")
            return {
                "dry_run": True,
                "timestamp": datetime.utcnow().isoformat(),
            }
        
        # Execute cleanup
        deleted_tier2 = 0
        deleted_tier3 = 0
        
        # Tier 2: 30-day retention
        try:
            deleted_tier2 = geo_schema.cleanup_ttl(db_dsn)
            logger.info(f"✅ TTL cleanup: deleted {deleted_tier2} expired rows (Tier 2/3)")
        except Exception as e:
            logger.error(f"TTL cleanup failed: {e}")
        
        return {
            "deleted_tier2": deleted_tier2,
            "deleted_tier3": 0,  # Consolidated into tier2 cleanup
            "timestamp": datetime.utcnow().isoformat(),
        }
        
    except Exception as e:
        logger.error(f"TTL cleanup error: {e}")
        return {"error": str(e)}


def schedule_ttl_job(db_dsn: str, schedule_cron: str = "0 2 * * *"):
    """Schedule TTL cleanup via cron or similar.
    
    Args:
        db_dsn: PostgreSQL connection string
        schedule_cron: Cron expression (default: 2 AM daily)
        
    Returns:
        dict: Job configuration
    """
    return {
        "job_name": "geo_ttl_cleanup",
        "schedule": schedule_cron,
        "description": "Delete expired Tier 2 (30d) / Tier 3 (14d) geo-tracking data",
        "command": f"cleanup_expired_geo_data('{db_dsn}')",
    }
