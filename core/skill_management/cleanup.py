"""Auto-cleanup of expired _local/ skills."""

import shutil
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import List
from dataclasses import dataclass

from core.skill_management.config_loader import load_tenant_skill_config


@dataclass
class CleanupResult:
    """Result of cleanup operation."""
    deleted_skills: List[str]
    skipped_skills: List[str]
    errors: List[str]


class LocalSkillCleanup:
    """Clean up expired _local/ skills."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.base_path = Path.home() / ".corvin" / "tenants" / tenant_id

    def cleanup_expired_local_skills(self, ttl_days: int = 90, dry_run: bool = False) -> CleanupResult:
        """Delete _local/ skills older than ttl_days."""
        deleted = []
        skipped = []
        errors = []

        local_skills_dir = self.base_path / "_local" / "skills"
        if not local_skills_dir.exists():
            return CleanupResult(deleted, skipped, errors)

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=ttl_days)

        for skill_dir in local_skills_dir.iterdir():
            if not skill_dir.is_dir() or skill_dir.name.startswith('.'):
                continue

            skill_id = skill_dir.name

            # Get creation time from meta.json
            meta_path = skill_dir / "meta.json"
            if not meta_path.exists():
                skipped.append(skill_id)
                continue

            try:
                with open(meta_path) as f:
                    metadata = json.load(f)

                created_str = metadata.get("created")
                if not created_str:
                    skipped.append(skill_id)
                    continue

                # Parse ISO 8601 timestamp
                created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))

                # Check if expired
                if created < cutoff:
                    if not dry_run:
                        try:
                            shutil.rmtree(skill_dir)
                            deleted.append(skill_id)
                        except Exception as e:
                            errors.append(f"{skill_id}: {str(e)}")
                    else:
                        deleted.append(skill_id)  # Would be deleted
                else:
                    skipped.append(skill_id)

            except Exception as e:
                errors.append(f"{skill_id}: {str(e)}")

        return CleanupResult(deleted, skipped, errors)

    def cleanup_all_expired(self, dry_run: bool = False) -> CleanupResult:
        """Cleanup with config-driven TTL."""
        config = load_tenant_skill_config(self.tenant_id)

        if not config.auto_cleanup_local:
            return CleanupResult([], [], ["Auto-cleanup disabled"])

        return self.cleanup_expired_local_skills(config.cleanup_ttl_days, dry_run)


def cleanup_local_skills(tenant_id: str = "_default", ttl_days: int = 90, dry_run: bool = False) -> CleanupResult:
    """Public API: Cleanup expired local skills."""
    cleanup = LocalSkillCleanup(tenant_id)
    return cleanup.cleanup_expired_local_skills(ttl_days, dry_run)
