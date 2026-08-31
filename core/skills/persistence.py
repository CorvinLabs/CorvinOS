"""Skill Persistence (ADR-0313)."""

from __future__ import annotations

import json
from pathlib import Path


class SkillPersistence:
    """Persist skills to disk."""

    def __init__(self, base_path: Path | str):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def save_skill(self, tenant_id: str, skill_name: str, skill_version: str, skill_data: dict) -> None:
        """Persist skill to file."""
        tenant_dir = self.base_path / tenant_id
        tenant_dir.mkdir(exist_ok=True)

        skill_file = tenant_dir / f"{skill_name}-{skill_version}.json"
        skill_file.write_text(json.dumps(skill_data, indent=2))

    def load_skill(self, tenant_id: str, skill_name: str, skill_version: str) -> dict | None:
        """Load persisted skill."""
        skill_file = self.base_path / tenant_id / f"{skill_name}-{skill_version}.json"
        if skill_file.exists():
            return json.loads(skill_file.read_text())
        return None

    def delete_skill(self, tenant_id: str, skill_name: str, skill_version: str) -> bool:
        """Delete persisted skill."""
        skill_file = self.base_path / tenant_id / f"{skill_name}-{skill_version}.json"
        if skill_file.exists():
            skill_file.unlink()
            return True
        return False
