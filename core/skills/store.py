"""Skill Store — CRUD operations for skill objects (ADR-0306)."""

import json
from pathlib import Path
from typing import Protocol

from .skill import Skill


class SkillStore(Protocol):
    """Protocol for skill persistence backends."""

    def save(self, skill: Skill) -> None:
        """Persist skill to store (upsert on name+version)."""
        ...

    def load(self, name: str, version: str) -> Skill | None:
        """Load skill by name+version, None if not found."""
        ...

    def list_all(self) -> list[Skill]:
        """List all stored skills (unsorted)."""
        ...

    def list_by_mean_score(self, limit: int | None = None) -> list[Skill]:
        """List skills sorted by mean_score descending."""
        ...

    def delete(self, name: str, version: str) -> bool:
        """Delete skill by name+version. Returns True if deleted, False if not found."""
        ...

    def exists(self, name: str, version: str) -> bool:
        """Check if skill exists."""
        ...


class InMemorySkillStore:
    """In-memory skill store (dev/test)."""

    def __init__(self):
        self._skills: dict[tuple[str, str], Skill] = {}

    def save(self, skill: Skill) -> None:
        """Store skill in-memory (overwrites existing version)."""
        key = (skill.name, skill.version)
        self._skills[key] = skill

    def load(self, name: str, version: str) -> Skill | None:
        """Retrieve skill by name+version."""
        return self._skills.get((name, version))

    def list_all(self) -> list[Skill]:
        """Get all stored skills."""
        return list(self._skills.values())

    def list_by_mean_score(self, limit: int | None = None) -> list[Skill]:
        """Sort by mean_score (desc), optionally limit results."""
        sorted_skills = sorted(self._skills.values(), key=lambda s: s.mean_score, reverse=True)
        return sorted_skills[:limit] if limit else sorted_skills

    def delete(self, name: str, version: str) -> bool:
        """Remove skill by name+version."""
        key = (name, version)
        if key in self._skills:
            del self._skills[key]
            return True
        return False

    def exists(self, name: str, version: str) -> bool:
        """Check if skill exists."""
        return (name, version) in self._skills

    def clear(self) -> None:
        """Clear all skills (test helper)."""
        self._skills.clear()


class FileSkillStore:
    """Persistent skill store backed by JSON files.

    Directory layout:
      <store_root>/
        <skill_name>/
          <version>.json   (one file per skill version)
    """

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, skill: Skill) -> None:
        """Write skill to <root>/<name>/<version>.json."""
        skill_dir = self.root / skill.name
        skill_dir.mkdir(parents=True, exist_ok=True)

        skill_file = skill_dir / f"{skill.version}.json"
        skill_file.write_text(json.dumps(skill.to_dict(), indent=2))

    def load(self, name: str, version: str) -> Skill | None:
        """Load skill from <root>/<name>/<version>.json."""
        skill_file = self.root / name / f"{version}.json"
        if not skill_file.exists():
            return None

        data = json.loads(skill_file.read_text())
        return Skill.from_dict(data)

    def list_all(self) -> list[Skill]:
        """Scan all skill files."""
        skills = []
        for skill_dir in self.root.iterdir():
            if not skill_dir.is_dir():
                continue
            for version_file in skill_dir.glob("*.json"):
                try:
                    data = json.loads(version_file.read_text())
                    skills.append(Skill.from_dict(data))
                except (json.JSONDecodeError, KeyError, ValueError):
                    pass  # Skip corrupted files
        return skills

    def list_by_mean_score(self, limit: int | None = None) -> list[Skill]:
        """Get all skills sorted by mean_score (desc)."""
        sorted_skills = sorted(self.list_all(), key=lambda s: s.mean_score, reverse=True)
        return sorted_skills[:limit] if limit else sorted_skills

    def delete(self, name: str, version: str) -> bool:
        """Delete <root>/<name>/<version>.json."""
        skill_file = self.root / name / f"{version}.json"
        if skill_file.exists():
            skill_file.unlink()
            # Clean up empty dirs
            skill_file.parent.rmdir() if not list(skill_file.parent.iterdir()) else None
            return True
        return False

    def exists(self, name: str, version: str) -> bool:
        """Check if <root>/<name>/<version>.json exists."""
        return (self.root / name / f"{version}.json").exists()
