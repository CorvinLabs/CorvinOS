"""Migration from ~/.claude/skills/ to tenant-scoped _shared/."""

import json
import shutil
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import List

@dataclass
class MigrationReport:
    migrated_skills: List[str]
    migrated_tools: List[str]
    backup_path: Path
    warnings: List[str]
    status: str  # "success" | "partial" | "failed"

class SkillMigrator:
    """Migrate skills from ~/.claude/skills/ to tenant-scoped structure."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.source_base = Path.home() / ".claude" / "skills"
        self.dest_base = Path.home() / ".corvin" / "tenants" / tenant_id / "_shared" / "skills"

    def migrate_from_claude_global(self, backup: bool = True) -> MigrationReport:
        """Migrate ~/.claude/skills/* -> tenant/_shared/skills/"""

        migrated_skills = []
        migrated_tools = []
        warnings = []
        backup_path = None

        # Step 1: Create backup
        if backup:
            backup_path = Path.home() / ".corvin" / "tenants" / self.tenant_id / "backups" / f"pre_migration_{datetime.now().strftime('%Y%m%d_%H%M%S')}.tar.gz"
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.make_archive(str(backup_path.with_suffix('')), 'gztar', self.source_base.parent, self.source_base.name)
            except Exception as e:
                warnings.append(f"Backup failed: {str(e)}")

        # Step 2: Ensure dest exists
        self.dest_base.mkdir(parents=True, exist_ok=True)

        # Step 3: Migrate each skill
        if not self.source_base.exists():
            return MigrationReport(
                migrated_skills=[],
                migrated_tools=[],
                backup_path=backup_path,
                warnings=["Source ~/.claude/skills/ does not exist"],
                status="failed"
            )

        for skill_dir in self.source_base.iterdir():
            if not skill_dir.is_dir() or skill_dir.name.startswith('.'):
                continue

            try:
                dest_dir = self.dest_base / skill_dir.name
                shutil.copytree(skill_dir, dest_dir, dirs_exist_ok=True)
                migrated_skills.append(skill_dir.name)
            except Exception as e:
                warnings.append(f"Failed to migrate {skill_dir.name}: {str(e)}")

        status = "success" if not warnings else "partial"

        return MigrationReport(
            migrated_skills=migrated_skills,
            migrated_tools=migrated_tools,
            backup_path=backup_path,
            warnings=warnings,
            status=status
        )

    def rollback_migration(self, backup_path: Path) -> bool:
        """Restore from backup."""
        try:
            # Remove partial migration
            if self.dest_base.exists():
                shutil.rmtree(self.dest_base)
            # Restore from backup
            shutil.unpack_archive(str(backup_path), Path.home() / ".corvin" / "tenants" / self.tenant_id / "backups")
            return True
        except Exception as e:
            print(f"Rollback failed: {e}")
            return False

def migrate_skills(tenant_id: str = "_default") -> MigrationReport:
    """Public API: Migrate skills from ~/.claude/ to tenant."""
    migrator = SkillMigrator(tenant_id)
    return migrator.migrate_from_claude_global(backup=True)
