"""Tenant-native skill directory initialization and structure validation."""

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List
from dataclasses import dataclass, asdict

from core.skill_management.tenant_validator import validate_tenant_id

@dataclass
class SkillDirectoryInfo:
    """Info about initialized tenant skill structure."""
    tenant_id: str
    base_path: Path
    created_dirs: List[Path]
    timestamp: str
    status: str  # "success" | "partial" | "failed"
    errors: List[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []

class SkillDirectoryInitializer:
    """Initialize tenant-scoped skill directory structure."""

    REQUIRED_DIRS = ["_platform", "_shared", "_local", "config", "exports"]

    def __init__(self, tenant_id: str = "_default"):
        validate_tenant_id(tenant_id)
        self.tenant_id = tenant_id
        self.base_path = Path.home() / ".corvin" / "tenants" / tenant_id

    def init_tenant_structure(self) -> SkillDirectoryInfo:
        """Create all required directories for tenant skill structure."""
        created_dirs = []
        errors = []

        self.base_path.mkdir(parents=True, exist_ok=True)

        for dir_name in self.REQUIRED_DIRS:
            dir_path = self.base_path / dir_name
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
                created_dirs.append(dir_path)
            except Exception as e:
                errors.append(f"Failed to create {dir_name}: {str(e)}")

        # Create subdirs
        try:
            (self.base_path / "_platform" / "skills").mkdir(parents=True, exist_ok=True)
            (self.base_path / "_platform" / "tools").mkdir(parents=True, exist_ok=True)
            (self.base_path / "_shared" / "skills").mkdir(parents=True, exist_ok=True)
            (self.base_path / "_shared" / "tools").mkdir(parents=True, exist_ok=True)
            (self.base_path / "_shared" / "templates").mkdir(parents=True, exist_ok=True)
            (self.base_path / "_local" / "skills").mkdir(parents=True, exist_ok=True)
            (self.base_path / "_local" / "tools").mkdir(parents=True, exist_ok=True)
        except Exception as e:
            errors.append(f"Failed to create subdirectories: {str(e)}")

        status = "success" if not errors else "partial"

        return SkillDirectoryInfo(
            tenant_id=self.tenant_id,
            base_path=self.base_path,
            created_dirs=created_dirs,
            timestamp=datetime.now().isoformat(),
            status=status,
            errors=errors
        )

    def validate_structure(self) -> Dict[str, bool]:
        """Validate that all required directories exist."""
        validation = {}

        for dir_name in self.REQUIRED_DIRS:
            dir_path = self.base_path / dir_name
            validation[dir_name] = dir_path.exists() and dir_path.is_dir()

        return validation

    def create_placeholder_manifests(self) -> None:
        """Create placeholder manifest.json files for each layer."""

        # _platform manifest
        platform_manifest = {
            "version": "1.0",
            "layer": "_platform",
            "description": "Platform-level skills and tools (read-only)",
            "created": datetime.now().isoformat(),
            "skills": [],
            "tools": []
        }
        with open(self.base_path / "_platform" / "manifest.json", "w") as f:
            json.dump(platform_manifest, f, indent=2)

        # _shared manifest
        shared_manifest = {
            "version": "1.0",
            "layer": "_shared",
            "description": "User-owned skills and tools (exportable)",
            "created": datetime.now().isoformat(),
            "skills": [],
            "tools": [],
            "git_sync": {
                "enabled": False,
                "repo": None,
                "branch": "main",
                "last_pull": None,
                "last_push": None
            }
        }
        with open(self.base_path / "_shared" / "manifest.json", "w") as f:
            json.dump(shared_manifest, f, indent=2)

        # _local manifest
        local_manifest = {
            "version": "1.0",
            "layer": "_local",
            "description": "Session-scoped skills (ephemeral, 90-day TTL)",
            "created": datetime.now().isoformat(),
            "skills": [],
            "tools": []
        }
        with open(self.base_path / "_local" / "manifest.json", "w") as f:
            json.dump(local_manifest, f, indent=2)


def init_tenant_skills(tenant_id: str = "_default") -> SkillDirectoryInfo:
    """Public API: Initialize tenant skill structure."""
    initializer = SkillDirectoryInitializer(tenant_id)
    info = initializer.init_tenant_structure()
    if info.status in ["success", "partial"]:
        initializer.create_placeholder_manifests()
    return info
