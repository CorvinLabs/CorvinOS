"""Promote _local/ skills to _shared/."""

import json
import shutil
import re
from pathlib import Path
from datetime import datetime
from typing import Optional
from dataclasses import dataclass

from core.skill_management.validator import MetadataValidator


@dataclass
class PromotionResult:
    """Result of skill promotion."""
    success: bool
    skill_id: str
    new_scope: str
    new_version: Optional[str] = None
    error: Optional[str] = None


class SkillPromoter:
    """Promote skills from _local/ to _shared/."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.base_path = Path.home() / ".corvin" / "tenants" / tenant_id

    def promote_local_to_shared(
        self,
        skill_id: str,
        custom_version: Optional[str] = None
    ) -> PromotionResult:
        """Promote a _local/ skill to _shared/."""

        # Load local skill
        local_skill_dir = self.base_path / "_local" / "skills" / skill_id
        if not local_skill_dir.exists():
            return PromotionResult(
                success=False,
                skill_id=skill_id,
                new_scope="_shared",
                error=f"Skill not found in _local/: {skill_id}"
            )

        # Load metadata
        meta_path = local_skill_dir / "meta.json"
        if not meta_path.exists():
            return PromotionResult(
                success=False,
                skill_id=skill_id,
                new_scope="_shared",
                error="meta.json not found"
            )

        try:
            with open(meta_path) as f:
                metadata = json.load(f)
        except Exception as e:
            return PromotionResult(
                success=False,
                skill_id=skill_id,
                new_scope="_shared",
                error=f"Failed to load metadata: {str(e)}"
            )

        # Validate dependencies
        validator = MetadataValidator(self.tenant_id)
        validation = validator.validate_skill_exports(skill_id, "_local")
        if not validation.valid:
            return PromotionResult(
                success=False,
                skill_id=skill_id,
                new_scope="_shared",
                error=f"Validation failed: {validation.errors[0].error if validation.errors else 'Unknown error'}"
            )

        # Calculate new version
        old_version = metadata.get("version", "0.0.0")
        if custom_version:
            new_version = custom_version
        else:
            # Auto-increment minor version
            parts = old_version.split(".")
            if len(parts) >= 2:
                try:
                    major = int(parts[0])
                    minor = int(parts[1]) + 1
                    patch = parts[2] if len(parts) > 2 else "0"
                    new_version = f"{major}.{minor}.{patch}"
                except:
                    new_version = f"{old_version}.promoted"
            else:
                new_version = f"{old_version}.promoted"

        # Update metadata
        metadata["version"] = new_version
        metadata["scope"] = "_shared"
        metadata["last_modified"] = datetime.now().isoformat()
        metadata["promotion_timestamp"] = datetime.now().isoformat()

        # Remove task-specific fields
        if "task_id" in metadata:
            del metadata["task_id"]

        # Move directory
        shared_skill_dir = self.base_path / "_shared" / "skills" / skill_id
        if shared_skill_dir.exists():
            return PromotionResult(
                success=False,
                skill_id=skill_id,
                new_scope="_shared",
                error=f"Skill already exists in _shared/"
            )

        try:
            # Create parent dir
            shared_skill_dir.parent.mkdir(parents=True, exist_ok=True)

            # Copy to _shared/
            shutil.copytree(local_skill_dir, shared_skill_dir)

            # Update meta.json
            with open(shared_skill_dir / "meta.json", "w") as f:
                json.dump(metadata, f, indent=2)

            # Remove from _local/
            shutil.rmtree(local_skill_dir)

            return PromotionResult(
                success=True,
                skill_id=skill_id,
                new_scope="_shared",
                new_version=new_version
            )

        except Exception as e:
            # Cleanup on error
            if shared_skill_dir.exists():
                try:
                    shutil.rmtree(shared_skill_dir)
                except:
                    pass

            return PromotionResult(
                success=False,
                skill_id=skill_id,
                new_scope="_shared",
                error=f"Promotion failed: {str(e)}"
            )

    def list_promotable_skills(self) -> dict:
        """List skills available for promotion."""
        local_skills_dir = self.base_path / "_local" / "skills"
        if not local_skills_dir.exists():
            return {}

        promotable = {}
        for skill_dir in local_skills_dir.iterdir():
            if not skill_dir.is_dir() or skill_dir.name.startswith('.'):
                continue

            meta_path = skill_dir / "meta.json"
            if meta_path.exists():
                try:
                    with open(meta_path) as f:
                        metadata = json.load(f)
                    promotable[skill_dir.name] = {
                        "version": metadata.get("version"),
                        "created": metadata.get("created"),
                        "task_id": metadata.get("task_id")
                    }
                except:
                    pass

        return promotable


def promote_skill(skill_id: str, tenant_id: str = "_default", custom_version: Optional[str] = None) -> PromotionResult:
    """Public API: Promote skill from _local/ to _shared/."""
    promoter = SkillPromoter(tenant_id)
    return promoter.promote_local_to_shared(skill_id, custom_version)
