"""Tests for Release Manager."""

import pytest
import json
from pathlib import Path
from datetime import datetime

from core.console.corvin_console.release_manager import (
    Release,
    ReleaseManager,
    create_release_snapshot,
)


class TestRelease:
    """Test Release data class."""

    def test_create_release(self):
        """Create release object."""
        release = Release(
            skill_id='my-skill',
            version='1.0.0',
            timestamp=datetime.utcnow().isoformat(),
            description='Initial release',
            author='user-1',
            changes=['First release'],
            skills_included=1,
        )

        assert release.skill_id == 'my-skill'
        assert release.version == '1.0.0'
        assert release.breaking_changes is False

    def test_release_to_dict(self):
        """Release serializes to dict."""
        release = Release(
            skill_id='test',
            version='1.0.0',
            timestamp='2026-08-19T12:00:00',
            description='Test',
            author='user',
            changes=['Change 1'],
            skills_included=1,
            tags=['beta'],
        )

        d = release.to_dict()

        assert d['skill_id'] == 'test'
        assert d['version'] == '1.0.0'
        assert d['tags'] == ['beta']


class TestReleaseManager:
    """Test ReleaseManager."""

    def test_create_release(self, tmp_path):
        """Create a release."""
        manager = ReleaseManager(tmp_path)

        success, error = manager.create_release(
            skill_id='my-skill',
            version='1.0.0',
            description='First release',
            author='user-1',
            changes=['Initial version'],
        )

        assert success is True
        assert error is None

        # Verify file created
        release_file = tmp_path / 'releases' / 'my-skill-1.0.0.json'
        assert release_file.exists()

    def test_create_release_invalid_version(self, tmp_path):
        """Reject invalid semantic version."""
        manager = ReleaseManager(tmp_path)

        success, error = manager.create_release(
            skill_id='test',
            version='not-a-version',
            description='Test',
            author='user',
            changes=[],
        )

        assert success is False
        assert error is not None

    def test_get_latest_release(self, tmp_path):
        """Get latest release."""
        manager = ReleaseManager(tmp_path)

        manager.create_release(
            skill_id='test',
            version='1.0.0',
            description='V1',
            author='user',
            changes=[],
        )

        manager.create_release(
            skill_id='test',
            version='1.1.0',
            description='V2',
            author='user',
            changes=[],
        )

        latest = manager.get_latest_release('test')

        assert latest is not None
        assert latest.version == '1.1.0'

    def test_get_release(self, tmp_path):
        """Get specific release."""
        manager = ReleaseManager(tmp_path)

        manager.create_release(
            skill_id='test',
            version='1.0.0',
            description='Test release',
            author='user',
            changes=['Change 1', 'Change 2'],
        )

        release = manager.get_release('test', '1.0.0')

        assert release is not None
        assert release.version == '1.0.0'
        assert len(release.changes) == 2

    def test_list_releases(self, tmp_path):
        """List all releases of a skill."""
        manager = ReleaseManager(tmp_path)

        for i in range(1, 4):
            manager.create_release(
                skill_id='test',
                version=f'1.{i}.0',
                description=f'Release {i}',
                author='user',
                changes=[],
            )

        releases = manager.list_releases('test')

        assert len(releases) == 3
        # Should be sorted by version (latest first)
        assert releases[0].version == '1.3.0'
        assert releases[2].version == '1.1.0'

    def test_calculate_next_version(self, tmp_path):
        """Calculate next version."""
        manager = ReleaseManager(tmp_path)

        manager.create_release(
            skill_id='test',
            version='1.2.3',
            description='Current',
            author='user',
            changes=[],
        )

        assert manager.calculate_next_version('test', 'patch') == '1.2.4'
        assert manager.calculate_next_version('test', 'minor') == '1.3.0'
        assert manager.calculate_next_version('test', 'major') == '2.0.0'

    def test_calculate_next_version_no_releases(self, tmp_path):
        """First release starts at 0.1.0."""
        manager = ReleaseManager(tmp_path)

        version = manager.calculate_next_version('new-skill', 'patch')
        assert version == '0.1.0'

    def test_get_changelog(self, tmp_path):
        """Generate changelog."""
        manager = ReleaseManager(tmp_path)

        manager.create_release(
            skill_id='test',
            version='1.0.0',
            description='Initial version',
            author='alice',
            changes=['First feature', 'Bug fix'],
        )

        manager.create_release(
            skill_id='test',
            version='1.1.0',
            description='Minor update',
            author='bob',
            changes=['New capability'],
        )

        changelog = manager.get_changelog('test')

        assert 'test' in changelog
        assert '1.1.0' in changelog
        assert '1.0.0' in changelog
        assert 'New capability' in changelog

    def test_get_changelog_from_version(self, tmp_path):
        """Generate changelog from specific version."""
        manager = ReleaseManager(tmp_path)

        for i in range(1, 4):
            manager.create_release(
                skill_id='test',
                version=f'1.{i}.0',
                description=f'Release {i}',
                author='user',
                changes=[f'Change {i}'],
            )

        changelog = manager.get_changelog('test', from_version='1.2.0')

        # Should include 1.3.0 and 1.2.0 only
        assert '1.3.0' in changelog
        assert '1.2.0' in changelog
        assert '1.1.0' not in changelog

    def test_can_rollback_to(self, tmp_path):
        """Check rollback eligibility."""
        manager = ReleaseManager(tmp_path)

        manager.create_release(
            skill_id='test',
            version='1.0.0',
            description='V1',
            author='user',
            changes=[],
        )

        manager.create_release(
            skill_id='test',
            version='1.1.0',
            description='V2',
            author='user',
            changes=[],
        )

        # Can rollback to older version
        can_rollback, error = manager.can_rollback_to('test', '1.0.0')
        assert can_rollback is True
        assert error is None

        # Cannot rollback to current version
        can_rollback, error = manager.can_rollback_to('test', '1.1.0')
        assert can_rollback is False

        # Cannot rollback to non-existent version
        can_rollback, error = manager.can_rollback_to('test', '2.0.0')
        assert can_rollback is False

    def test_get_release_notes(self, tmp_path):
        """Generate release notes."""
        manager = ReleaseManager(tmp_path)

        manager.create_release(
            skill_id='my-skill',
            version='1.2.0',
            description='Important update',
            author='alice',
            changes=['Feature A', 'Bug fix B'],
            breaking=True,
        )

        notes = manager.get_release_notes('my-skill', '1.2.0')

        assert notes is not None
        assert 'my-skill v1.2.0' in notes
        assert 'alice' in notes
        assert 'BREAKING CHANGES' in notes
        assert 'Feature A' in notes

    def test_breaking_changes_flag(self, tmp_path):
        """Track breaking changes."""
        manager = ReleaseManager(tmp_path)

        manager.create_release(
            skill_id='test',
            version='2.0.0',
            description='Major update',
            author='user',
            changes=['Breaking change'],
            breaking=True,
        )

        release = manager.get_latest_release('test')

        assert release.breaking_changes is True
