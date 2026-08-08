"""Package Skill Loader (ADR-0268 Phase 5).

Discovers and loads skills from installed packages (ADR-0268).
Bridge between PackageManager and SkillInjection.

Features:
- Discovers installed packages from PackageManager
- Extracts skills declared in package manifests
- Converts package skills to SkillInjection format
- Caches discovered skills for performance
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class PackageSkillInfo:
    """Skill discovered in an installed package."""

    skill_id: str
    """Unique skill identifier (package_id:skill_name)."""

    title: str
    """Skill name/title."""

    description: str
    """Brief skill description."""

    category: str
    """Skill category (from package manifest)."""

    package_id: str
    """ID of the package this skill comes from."""

    file_path: str
    """Path to skill definition file."""

    trigger: Optional[str] = None
    """Skill trigger type (e.g., 'preprocessing', 'error_handling')."""


class PackageSkillLoader:
    """Discover and load skills from installed packages.

    Integrates with ADR-0268 Skill Package System.
    """

    def __init__(self, tenant_id: str = "_default", cache_ttl_minutes: int = 30):
        """Initialize package skill loader.

        Args:
            tenant_id: Tenant ID for package discovery.
            cache_ttl_minutes: Cache TTL in minutes.
        """
        self.tenant_id = tenant_id
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self._skill_cache: Dict[str, tuple[List[PackageSkillInfo], datetime]] = {}

        # Lazy-load PackageManager (avoid circular import)
        self._package_manager = None

        logger.info(f"PackageSkillLoader initialized (tenant={tenant_id}, TTL={cache_ttl_minutes}min)")

    def _get_package_manager(self):
        """Lazy-load PackageManager to avoid circular imports."""
        if self._package_manager is None:
            from core.package_manager import PackageManager

            self._package_manager = PackageManager(self.tenant_id)
        return self._package_manager

    def discover_package_skills(self) -> List[PackageSkillInfo]:
        """Discover all skills from installed packages.

        Returns:
            List of PackageSkillInfo for all discovered package skills.
        """
        # Check cache first
        cache_key = f"all_skills_{self.tenant_id}"
        if cache_key in self._skill_cache:
            cached_skills, timestamp = self._skill_cache[cache_key]
            age = datetime.now() - timestamp
            if age < self.cache_ttl:
                logger.debug(
                    f"Package skills cache hit: {len(cached_skills)} skills, age={age.total_seconds():.0f}s"
                )
                return cached_skills
            else:
                del self._skill_cache[cache_key]

        logger.debug("Discovering skills from installed packages...")

        all_skills: List[PackageSkillInfo] = []

        try:
            manager = self._get_package_manager()
            packages = manager.list_packages()

            for package_id, pkg in packages.items():
                manifest = pkg.manifest
                skills = self._extract_skills_from_manifest(package_id, manifest)
                all_skills.extend(skills)
                logger.debug(f"Package {package_id}: {len(skills)} skills discovered")

        except Exception as e:
            logger.warning(f"Error discovering package skills: {e}")
            # Graceful fallback: return empty list, don't crash
            all_skills = []

        # Cache results
        self._skill_cache[cache_key] = (all_skills, datetime.now())

        logger.info(f"Package skill discovery complete: {len(all_skills)} total skills")
        return all_skills

    def _extract_skills_from_manifest(self, package_id: str, manifest: Dict) -> List[PackageSkillInfo]:
        """Extract skill info from package manifest.

        Package manifest format (ADR-0268):
        {
          "id": "com.example.pkg",
          "name": "Example Package",
          "contents": {
            "skills": [
              {"id": "skill_1", "file": "skills/skill_1.yaml", "name": "Skill 1", ...}
            ]
          }
        }

        Args:
            package_id: Package identifier.
            manifest: Package manifest dict.

        Returns:
            List of PackageSkillInfo discovered in this package.
        """
        skills = []

        # Look for skills in manifest["contents"]["skills"]
        contents = manifest.get("contents", {})
        manifest_skills = contents.get("skills", [])

        for skill_def in manifest_skills:
            try:
                # Extract basic info
                skill_id = skill_def.get("id")
                if not skill_id:
                    logger.warning(f"Package {package_id}: skill missing 'id' field")
                    continue

                skill_file = skill_def.get("file", "")
                skill_name = skill_def.get("name", skill_id)
                skill_description = skill_def.get("description", "")
                skill_category = skill_def.get("category", "general")

                # Hooks (optional)
                hooks = skill_def.get("hooks", [])
                trigger = None
                if hooks and len(hooks) > 0:
                    trigger = hooks[0].get("trigger")

                # Create PackageSkillInfo
                pkg_skill = PackageSkillInfo(
                    skill_id=f"{package_id}:{skill_id}",
                    title=skill_name,
                    description=skill_description,
                    category=skill_category,
                    package_id=package_id,
                    file_path=skill_file,
                    trigger=trigger,
                )

                skills.append(pkg_skill)

            except Exception as e:
                logger.warning(f"Error extracting skill from {package_id}: {e}")
                continue

        return skills

    def get_skills_for_task(self, task: object) -> List[Dict]:
        """Get package skills relevant to a task.

        This is a basic implementation that returns all package skills.
        A more sophisticated version would score by relevance.

        Args:
            task: Task object to find relevant skills for.

        Returns:
            List of skill dicts (SkillInjection format).
        """
        package_skills = self.discover_package_skills()

        # Convert to SkillInjection format
        skill_dicts = []
        for pkg_skill in package_skills:
            skill_dicts.append(
                {
                    "skill_id": pkg_skill.skill_id,
                    "title": pkg_skill.title,
                    "description": pkg_skill.description,
                    "category": pkg_skill.category,
                    "relevance_score": 0.5,  # Default relevance; could be improved
                    "success_rate": 0.7,  # Default success rate
                    "source": f"package:{pkg_skill.package_id}",
                }
            )

        return skill_dicts

    def clear_cache(self):
        """Clear the skill discovery cache."""
        self._skill_cache.clear()
        logger.debug("Package skill cache cleared")
