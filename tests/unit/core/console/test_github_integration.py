"""Tests for GitHub Integration API."""

import pytest
import json
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock

from core.console.corvin_console.routes.github_integration import (
    validate_github_url,
    check_github_connectivity,
    save_github_config,
    load_github_config,
    get_sync_status,
)


class TestValidateGitHubURL:
    """Test GitHub URL validation."""

    def test_valid_url(self):
        """Valid GitHub URL passes."""
        valid, owner, repo, error = validate_github_url('https://github.com/owner/repo')
        assert valid is True
        assert owner == 'owner'
        assert repo == 'repo'
        assert error is None

    def test_valid_url_with_trailing_slash(self):
        """Valid GitHub URL with trailing slash passes."""
        valid, owner, repo, error = validate_github_url('https://github.com/owner/repo/')
        assert valid is True
        assert owner == 'owner'
        assert repo == 'repo'
        assert error is None

    def test_valid_url_with_dashes(self):
        """GitHub URL with dashes in names passes."""
        valid, owner, repo, error = validate_github_url('https://github.com/my-owner/my-repo')
        assert valid is True
        assert owner == 'my-owner'
        assert repo == 'my-repo'

    def test_valid_url_with_underscores(self):
        """GitHub URL with underscores in names passes."""
        valid, owner, repo, error = validate_github_url('https://github.com/my_owner/my_repo')
        assert valid is True
        assert owner == 'my_owner'
        assert repo == 'my_repo'

    def test_valid_url_with_dots(self):
        """GitHub URL with dots in repo name passes."""
        valid, owner, repo, error = validate_github_url('https://github.com/owner/my.repo')
        assert valid is True
        assert owner == 'owner'
        assert repo == 'my.repo'

    def test_invalid_url_format(self):
        """Invalid URL format fails."""
        valid, owner, repo, error = validate_github_url('https://gitlab.com/owner/repo')
        assert valid is False
        assert error is not None
        assert 'Invalid GitHub URL format' in error

    def test_invalid_url_no_https(self):
        """URL without https fails."""
        valid, owner, repo, error = validate_github_url('http://github.com/owner/repo')
        assert valid is False

    def test_invalid_url_empty(self):
        """Empty URL fails."""
        valid, owner, repo, error = validate_github_url('')
        assert valid is False

    def test_invalid_owner_with_special_chars(self):
        """Owner with invalid characters fails."""
        valid, owner, repo, error = validate_github_url('https://github.com/owner@bad/repo')
        assert valid is False

    def test_invalid_repo_with_special_chars(self):
        """Repo with invalid characters fails."""
        valid, owner, repo, error = validate_github_url('https://github.com/owner/repo@bad')
        assert valid is False


class TestCheckGitHubConnectivity:
    """Test GitHub API connectivity checks."""

    @patch('core.console.corvin_console.routes.github_integration.requests.get')
    def test_successful_connection(self, mock_get):
        """Successful GitHub API connection."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'full_name': 'owner/repo',
            'html_url': 'https://github.com/owner/repo',
            'private': False,
            'description': 'Test repo',
        }
        mock_response.headers = {'X-RateLimit-Remaining': '4999'}
        mock_get.return_value = mock_response

        accessible, details = check_github_connectivity('owner', 'repo')

        assert accessible is True
        assert details['status'] == 'connected'
        assert details['repo_exists'] is True
        assert details['repo_name'] == 'owner/repo'
        assert details['repo_private'] is False

    @patch('core.console.corvin_console.routes.github_integration.requests.get')
    def test_repo_not_found(self, mock_get):
        """Repo not found (404)."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        accessible, details = check_github_connectivity('owner', 'nonexistent')

        assert accessible is False
        assert details['status'] == 'not_found'
        assert 'not found' in details['error']

    @patch('core.console.corvin_console.routes.github_integration.requests.get')
    def test_unauthorized(self, mock_get):
        """Unauthorized (invalid token)."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_get.return_value = mock_response

        accessible, details = check_github_connectivity('owner', 'repo', token='bad-token')

        assert accessible is False
        assert details['status'] == 'unauthorized'
        assert 'authentication failed' in details['error']

    @patch('core.console.corvin_console.routes.github_integration.requests.get')
    def test_forbidden(self, mock_get):
        """Forbidden (403)."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_get.return_value = mock_response

        accessible, details = check_github_connectivity('owner', 'private-repo')

        assert accessible is False
        assert details['status'] == 'forbidden'

    @patch('core.console.corvin_console.routes.github_integration.requests.get')
    def test_timeout(self, mock_get):
        """Request timeout."""
        import requests
        mock_get.side_effect = requests.Timeout()

        accessible, details = check_github_connectivity('owner', 'repo')

        assert accessible is False
        assert details['status'] == 'timeout'
        assert 'timed out' in details['error']

    @patch('core.console.corvin_console.routes.github_integration.requests.get')
    def test_connection_error(self, mock_get):
        """Connection error."""
        import requests
        mock_get.side_effect = requests.ConnectionError('Network unreachable')

        accessible, details = check_github_connectivity('owner', 'repo')

        assert accessible is False
        assert details['status'] == 'connection_error'
        assert 'Failed to connect' in details['error']


class TestConfigStorage:
    """Test GitHub configuration persistence."""

    def test_save_and_load_config(self, tmp_path, monkeypatch):
        """Save and load GitHub config."""
        monkeypatch.setenv("HOME", str(tmp_path))
        tenant_path = tmp_path / '.corvin' / 'tenants' / '_default'
        tenant_path.mkdir(parents=True)

        # Mock TENANT_PATH
        import core.console.corvin_console.routes.github_integration as gi
        original_tenant_path = gi.TENANT_PATH
        gi.TENANT_PATH = tenant_path

        try:
            # Save config
            config_file = save_github_config('owner', 'repo', token='test-token', auto_sync=True)

            assert config_file.exists()

            # Load config
            config = load_github_config()

            assert config['github']['owner'] == 'owner'
            assert config['github']['repo'] == 'repo'
            assert config['github']['auto_sync'] is True
            assert config['github']['url'] == 'https://github.com/owner/repo'

            # Check token file was created with restricted permissions
            token_file = tenant_path / 'config' / '.github-token'
            assert token_file.exists()
            # Note: chmod check depends on OS

        finally:
            gi.TENANT_PATH = original_tenant_path

    def test_load_config_not_exists(self, tmp_path, monkeypatch):
        """Load config when not configured returns empty dict."""
        monkeypatch.setenv("HOME", str(tmp_path))
        tenant_path = tmp_path / '.corvin' / 'tenants' / '_default'
        tenant_path.mkdir(parents=True)

        import core.console.corvin_console.routes.github_integration as gi
        original_tenant_path = gi.TENANT_PATH
        gi.TENANT_PATH = tenant_path

        try:
            config = load_github_config()
            assert config == {}
        finally:
            gi.TENANT_PATH = original_tenant_path


class TestSyncStatus:
    """Test sync status tracking."""

    def test_sync_status_not_configured(self, tmp_path, monkeypatch):
        """Sync status when not configured."""
        monkeypatch.setenv("HOME", str(tmp_path))
        tenant_path = tmp_path / '.corvin' / 'tenants' / '_default'
        tenant_path.mkdir(parents=True)

        import core.console.corvin_console.routes.github_integration as gi
        original_tenant_path = gi.TENANT_PATH
        gi.TENANT_PATH = tenant_path

        try:
            status = get_sync_status()
            assert status['connected'] is False
            assert status['configured'] is False
        finally:
            gi.TENANT_PATH = original_tenant_path

    def test_sync_status_configured(self, tmp_path, monkeypatch):
        """Sync status when configured."""
        monkeypatch.setenv("HOME", str(tmp_path))
        tenant_path = tmp_path / '.corvin' / 'tenants' / '_default'
        tenant_path.mkdir(parents=True)

        import core.console.corvin_console.routes.github_integration as gi
        original_tenant_path = gi.TENANT_PATH
        gi.TENANT_PATH = tenant_path

        try:
            # Save config first
            save_github_config('owner', 'repo')

            status = get_sync_status()
            assert status['connected'] is True
            assert status['configured'] is True
            assert status['owner'] == 'owner'
            assert status['repo'] == 'repo'
            assert status['auto_sync'] is True

        finally:
            gi.TENANT_PATH = original_tenant_path
