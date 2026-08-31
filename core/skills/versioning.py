"""Skill Versioning (ADR-0314)."""

from __future__ import annotations

from packaging import version


class SkillVersion:
    """Manages skill version comparisons."""

    def __init__(self, version_str: str):
        self.version = version.parse(version_str)

    def is_newer_than(self, other: SkillVersion | str) -> bool:
        """Check if this version is newer."""
        other_v = other.version if isinstance(other, SkillVersion) else version.parse(other)
        return self.version > other_v

    def is_compatible_with(self, other: SkillVersion | str) -> bool:
        """Check major version compatibility."""
        other_v = other.version if isinstance(other, SkillVersion) else version.parse(other)
        return self.version.major == other_v.major

    def __str__(self) -> str:
        return str(self.version)
