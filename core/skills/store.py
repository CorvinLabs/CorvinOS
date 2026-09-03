"""Skill Store — CRUD operations for skill objects (ADR-0306)."""

import json
import re
from pathlib import Path
from typing import Protocol

from .skill import Skill

# A skill name / version becomes ONE path component: no separators, no dot-dirs.
_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _safe_component(value: str, what: str) -> str:
    """Reject anything that could leave the store root when joined."""
    if not isinstance(value, str) or ".." in value or "/" in value or "\\" in value \
            or _COMPONENT_RE.match(value) is None:
        raise ValueError(f"{what} is not a safe path component: {value!r}")
    return value


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
        self._root_resolved = self.root.resolve()

    def _path(self, name: str, version: str) -> Path:
        """<root>/<name>/<version>.json — validated AND resolve-checked."""
        _safe_component(name, "skill name")
        _safe_component(version, "skill version")
        path = self.root / name / f"{version}.json"
        if not path.resolve().is_relative_to(self._root_resolved):
            raise ValueError(f"skill path escapes store root: {name!r}/{version!r}")
        return path

    def save(self, skill: Skill) -> None:
        """Write skill to <root>/<name>/<version>.json."""
        skill_file = self._path(skill.name, skill.version)
        skill_file.parent.mkdir(parents=True, exist_ok=True)
        skill_file.write_text(json.dumps(skill.to_dict(), indent=2))

    def load(self, name: str, version: str) -> Skill | None:
        """Load skill from <root>/<name>/<version>.json."""
        skill_file = self._path(name, version)
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
        skill_file = self._path(name, version)
        if skill_file.exists():
            skill_file.unlink()
            # Clean up empty dirs
            skill_file.parent.rmdir() if not list(skill_file.parent.iterdir()) else None
            return True
        return False

    def exists(self, name: str, version: str) -> bool:
        """Check if <root>/<name>/<version>.json exists."""
        return self._path(name, version).exists()
