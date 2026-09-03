"""Skill Persistence (ADR-0313).

``tenant_id``, ``skill_name`` and ``skill_version`` each become ONE path
component under ``base_path``. They are validated before any join and the
final path is resolve-checked against the base — ``tenant_id="../"`` or
``skill_name="../../../esc"`` used to write outside the store (D-15).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from core.tenants import validate_tenant_id

_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _safe_component(value: str, what: str) -> str:
    if not isinstance(value, str) or ".." in value or "/" in value or "\\" in value \
            or _COMPONENT_RE.match(value) is None:
        raise ValueError(f"{what} is not a safe path component: {value!r}")
    return value


class SkillPersistence:
    """Persist skills to disk."""

    def __init__(self, base_path: Path | str):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        self._base_resolved = self.base_path.resolve()

    def _path(self, tenant_id: str, skill_name: str, skill_version: str) -> Path:
        validate_tenant_id(tenant_id)
        _safe_component(skill_name, "skill_name")
        _safe_component(skill_version, "skill_version")
        path = self.base_path / tenant_id / f"{skill_name}-{skill_version}.json"
        if not path.resolve().is_relative_to(self._base_resolved):
            raise ValueError("skill path escapes base_path")
        return path

    def save_skill(self, tenant_id: str, skill_name: str, skill_version: str, skill_data: dict) -> None:
        """Persist skill to file."""
        skill_file = self._path(tenant_id, skill_name, skill_version)
        skill_file.parent.mkdir(exist_ok=True)
        skill_file.write_text(json.dumps(skill_data, indent=2))

    def load_skill(self, tenant_id: str, skill_name: str, skill_version: str) -> dict | None:
        """Load persisted skill."""
        skill_file = self._path(tenant_id, skill_name, skill_version)
        if skill_file.exists():
            return json.loads(skill_file.read_text())
        return None

    def delete_skill(self, tenant_id: str, skill_name: str, skill_version: str) -> bool:
        """Delete persisted skill."""
        skill_file = self._path(tenant_id, skill_name, skill_version)
        if skill_file.exists():
            skill_file.unlink()
            return True
        return False
