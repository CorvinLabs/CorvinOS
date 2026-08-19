"""Release Management for Tenant Skills.

Provides:
- Semantic versioning (major.minor.patch)
- Changelog tracking
- Release tagging
- Rollback support
- Cross-tenant distribution
"""

import json
import semver
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple
from dataclasses import dataclass, asdict, field
import logging

logger = logging.getLogger(__name__)

TENANT_PATH = Path.home() / '.corvin' / 'tenants' / '_default'


@dataclass
class Release:
    """A skill release version."""
    skill_id: str
    version: str
    timestamp: str
    description: str
    author: str
    changes: List[str]  # Changelog entries
    skills_included: int
    breaking_changes: bool = False
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return asdict(self)


class ReleaseManager:
    """Manages skill versions and releases."""

    def __init__(self, tenant_path: Path = TENANT_PATH):
        self.tenant_path = tenant_path
        self.releases_file = tenant_path / 'config' / 'releases.json'
        self.releases_dir = tenant_path / 'releases'

    def _ensure_dirs(self) -> None:
        """Ensure required directories exist."""
        self.releases_dir.mkdir(parents=True, exist_ok=True)
        self.releases_file.parent.mkdir(parents=True, exist_ok=True)

    def _load_releases(self) -> dict[str, List[dict]]:
        """Load releases index."""
        self._ensure_dirs()

        if not self.releases_file.exists():
            return {}

        try:
            with open(self.releases_file) as f:
                return json.load(f)
        except:
            return {}

    def _save_releases(self, releases: dict[str, List[dict]]) -> None:
        """Save releases index."""
        self._ensure_dirs()

        with open(self.releases_file, 'w') as f:
            json.dump(releases, f, indent=2)

    def create_release(
        self,
        skill_id: str,
        version: str,
        description: str,
        author: str,
        changes: List[str],
        skills_included: int = 1,
        breaking: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        """
        Create a new release.

        Args:
            skill_id: Skill identifier
            version: Semantic version (e.g., "1.2.3")
            description: Release description
            author: Release author
            changes: List of changelog entries
            skills_included: Number of skills in this release
            breaking: Whether this is a breaking change

        Returns:
            (success: bool, error_msg: str | None)
        """
        # Validate version format
        try:
            semver.VersionInfo.parse(version)
        except ValueError:
            return False, f'Invalid semantic version: {version}'

        self._ensure_dirs()

        release = Release(
            skill_id=skill_id,
            version=version,
            timestamp=datetime.utcnow().isoformat(),
            description=description,
            author=author,
            changes=changes,
            skills_included=skills_included,
            breaking_changes=breaking,
        )

        try:
            # Save release metadata
            release_path = self.releases_dir / f'{skill_id}-{version}.json'
            with open(release_path, 'w') as f:
                json.dump(release.to_dict(), f, indent=2)

            # Update releases index
            releases = self._load_releases()
            if skill_id not in releases:
                releases[skill_id] = []

            # Add to index (sorted by version)
            releases[skill_id].append(release.to_dict())
            releases[skill_id].sort(
                key=lambda r: semver.VersionInfo.parse(r['version']),
                reverse=True
            )

            self._save_releases(releases)

            logger.info(f'Release created: {skill_id}@{version}')
            return True, None

        except Exception as e:
            logger.error(f'Failed to create release: {e}')
            return False, str(e)

    def get_latest_release(self, skill_id: str) -> Optional[Release]:
        """Get latest release of a skill."""
        releases = self._load_releases()

        if skill_id not in releases or not releases[skill_id]:
            return None

        latest_data = releases[skill_id][0]
        return Release(**latest_data)

    def get_release(self, skill_id: str, version: str) -> Optional[Release]:
        """Get specific release."""
        release_path = self.releases_dir / f'{skill_id}-{version}.json'

        if not release_path.exists():
            return None

        try:
            with open(release_path) as f:
                data = json.load(f)
            return Release(**data)
        except:
            return None

    def list_releases(self, skill_id: str) -> List[Release]:
        """List all releases of a skill."""
        releases = self._load_releases()

        if skill_id not in releases:
            return []

        return [Release(**r) for r in releases[skill_id]]

    def get_changelog(self, skill_id: str, from_version: Optional[str] = None) -> str:
        """
        Generate markdown changelog.

        Args:
            skill_id: Skill identifier
            from_version: Optional starting version (earlier changes ignored)

        Returns:
            Markdown formatted changelog
        """
        releases = self.list_releases(skill_id)

        if not releases:
            return f'# Changelog for {skill_id}\n\nNo releases yet.\n'

        lines = [f'# Changelog for {skill_id}\n']

        start_idx = len(releases)
        if from_version:
            for i, r in enumerate(releases):
                if r.version == from_version:
                    start_idx = i
                    break

        for release in releases[:start_idx]:
            lines.append(f'\n## [{release.version}] - {release.timestamp[:10]}')
            lines.append(f'\n*Released by {release.author}*\n')

            if release.description:
                lines.append(f'{release.description}\n')

            if release.breaking_changes:
                lines.append('⚠️ **BREAKING CHANGES**\n')

            if release.changes:
                lines.append('### Changes')
                for change in release.changes:
                    lines.append(f'- {change}')
                lines.append('')

        return '\n'.join(lines)

    def calculate_next_version(
        self,
        skill_id: str,
        bump_type: str = 'patch',
    ) -> str:
        """
        Calculate next version.

        Args:
            skill_id: Skill identifier
            bump_type: 'major', 'minor', or 'patch'

        Returns:
            Next semantic version
        """
        latest = self.get_latest_release(skill_id)

        if not latest:
            return '0.1.0'

        current = semver.VersionInfo.parse(latest.version)

        if bump_type == 'major':
            return str(current.bump_major())
        elif bump_type == 'minor':
            return str(current.bump_minor())
        else:  # patch
            return str(current.bump_patch())

    def can_rollback_to(self, skill_id: str, version: str) -> Tuple[bool, Optional[str]]:
        """
        Check if rollback to version is possible.

        Args:
            skill_id: Skill identifier
            version: Target version to rollback to

        Returns:
            (can_rollback: bool, error_msg: str | None)
        """
        release = self.get_release(skill_id, version)

        if not release:
            return False, f'Release {version} not found'

        # Check if there's a more recent version
        latest = self.get_latest_release(skill_id)
        if latest.version == version:
            return False, 'Already at this version'

        # Verify version is valid semver
        try:
            semver.VersionInfo.parse(version)
            return True, None
        except ValueError:
            return False, f'Invalid version: {version}'

    def get_release_notes(self, skill_id: str, version: str) -> Optional[str]:
        """Get formatted release notes."""
        release = self.get_release(skill_id, version)

        if not release:
            return None

        notes = []
        notes.append(f'# {skill_id} v{version}\n')
        notes.append(f'Released: {release.timestamp}\n')
        notes.append(f'Author: {release.author}\n')

        if release.breaking_changes:
            notes.append('\n⚠️ **BREAKING CHANGES**\n')

        notes.append(f'\n{release.description}\n')

        if release.changes:
            notes.append('## Changes\n')
            for change in release.changes:
                notes.append(f'- {change}')

        notes.append(f'\n## Stats\n')
        notes.append(f'- Skills included: {release.skills_included}\n')
        notes.append(f'- Tags: {", ".join(release.tags) if release.tags else "none"}\n')

        return '\n'.join(notes)


def create_release_snapshot(
    skill_id: str,
    version: str,
    description: str,
    author: str,
) -> Tuple[bool, Optional[str]]:
    """
    High-level function to create a release from current skill state.

    Captures current skill metadata and creates a versioned release.
    """
    tenant_path = TENANT_PATH
    skill_dir = tenant_path / '_shared' / 'skills' / skill_id

    if not skill_dir.exists():
        return False, f'Skill not found: {skill_id}'

    try:
        # Read current skill metadata
        meta_path = skill_dir / 'meta.json'
        if not meta_path.exists():
            return False, 'Skill metadata not found'

        with open(meta_path) as f:
            meta = json.load(f)

        # Generate changelog entries
        changes = [
            f'Updated {meta.get("name", skill_id)}',
            f'Version: {meta.get("version", "unknown")}',
        ]

        if 'dependencies' in meta:
            changes.append(f'Dependencies: {len(meta.get("dependencies", []))}')

        manager = ReleaseManager()
        return manager.create_release(
            skill_id=skill_id,
            version=version,
            description=description,
            author=author,
            changes=changes,
            skills_included=1,
            breaking=False,
        )

    except Exception as e:
        logger.error(f'Failed to create release snapshot: {e}')
        return False, str(e)
