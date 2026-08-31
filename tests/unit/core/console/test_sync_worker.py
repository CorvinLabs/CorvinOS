"""Tests for SyncWorker background job."""

import pytest
import json
import time
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock

from core.console.corvin_console.sync_worker import SyncWorker


class TestSyncWorker:
    """Test background sync worker."""

    def test_worker_initialization(self):
        """SyncWorker initializes correctly."""
        worker = SyncWorker(interval_seconds=60)

        assert worker.interval == 60
        assert worker.running is False
        assert worker.last_sync is None
        assert worker.last_error is None
        assert worker.sync_count == 0
        assert worker.error_count == 0

    def test_worker_start_stop(self):
        """Worker can start and stop."""
        worker = SyncWorker(interval_seconds=1)

        worker.start()
        assert worker.running is True

        time.sleep(0.5)  # Let it run a bit

        worker.stop()
        assert worker.running is False

    def test_worker_callbacks(self):
        """Worker can emit events to callbacks."""
        worker = SyncWorker(interval_seconds=60)

        events = []

        def on_event(payload):
            events.append(payload)

        worker.subscribe(on_event)
        worker.emit('test_event', {'data': 'test'})

        assert len(events) == 1
        assert events[0]['event'] == 'test_event'
        assert events[0]['details']['data'] == 'test'
        assert 'timestamp' in events[0]

    def test_worker_multiple_callbacks(self):
        """Multiple callbacks can be registered."""
        worker = SyncWorker(interval_seconds=60)

        events1 = []
        events2 = []

        worker.subscribe(lambda e: events1.append(e))
        worker.subscribe(lambda e: events2.append(e))

        worker.emit('event1', {'x': 1})

        assert len(events1) == 1
        assert len(events2) == 1

    def test_worker_status(self):
        """Worker status is tracked correctly."""
        worker = SyncWorker(interval_seconds=60)

        status = worker.get_status()

        assert status['running'] is False
        assert status['interval_seconds'] == 60
        assert status['last_sync'] is None
        assert status['sync_count'] == 0
        assert status['error_count'] == 0

    @patch('core.console.corvin_console.sync_worker.requests.head')
    def test_perform_sync_success(self, mock_head, tmp_path, monkeypatch):
        """Successful sync performance."""
        monkeypatch.setenv("HOME", str(tmp_path))
        tenant_path = tmp_path / '.corvin' / 'tenants' / '_default'
        tenant_path.mkdir(parents=True)

        # Mock requests
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_head.return_value = mock_response

        import core.console.corvin_console.sync_worker as sw
        original_tenant_path = sw.TENANT_PATH
        sw.TENANT_PATH = tenant_path

        try:
            worker = SyncWorker(interval_seconds=60)

            github_cfg = {
                'owner': 'owner',
                'repo': 'repo',
                'url': 'https://github.com/owner/repo',
            }

            result = worker._perform_sync(github_cfg)

            assert result['success'] is True
            assert result['github_url'] == 'https://github.com/owner/repo'
            assert 'timestamp' in result

        finally:
            sw.TENANT_PATH = original_tenant_path

    @patch('core.console.corvin_console.sync_worker.requests.head')
    def test_perform_sync_repo_unreachable(self, mock_head, tmp_path, monkeypatch):
        """Sync fails when repo is unreachable."""
        monkeypatch.setenv("HOME", str(tmp_path))
        tenant_path = tmp_path / '.corvin' / 'tenants' / '_default'
        tenant_path.mkdir(parents=True)

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_head.return_value = mock_response

        import core.console.corvin_console.sync_worker as sw
        original_tenant_path = sw.TENANT_PATH
        sw.TENANT_PATH = tenant_path

        try:
            worker = SyncWorker(interval_seconds=60)

            github_cfg = {
                'owner': 'owner',
                'repo': 'nonexistent',
                'url': 'https://github.com/owner/nonexistent',
            }

            result = worker._perform_sync(github_cfg)

            assert result['success'] is False
            assert 'unreachable' in result['error']
            assert result['http_code'] == 404

        finally:
            sw.TENANT_PATH = original_tenant_path

    @patch('core.console.corvin_console.sync_worker.requests.head')
    def test_perform_sync_connection_error(self, mock_head, tmp_path, monkeypatch):
        """Sync handles connection errors gracefully."""
        monkeypatch.setenv("HOME", str(tmp_path))
        tenant_path = tmp_path / '.corvin' / 'tenants' / '_default'
        tenant_path.mkdir(parents=True)

        import requests
        mock_head.side_effect = requests.ConnectionError('Network error')

        import core.console.corvin_console.sync_worker as sw
        original_tenant_path = sw.TENANT_PATH
        sw.TENANT_PATH = tenant_path

        try:
            worker = SyncWorker(interval_seconds=60)

            github_cfg = {
                'owner': 'owner',
                'repo': 'repo',
                'url': 'https://github.com/owner/repo',
            }

            result = worker._perform_sync(github_cfg)

            assert result['success'] is False
            assert 'Failed to reach GitHub' in result['error']

        finally:
            sw.TENANT_PATH = original_tenant_path

    def test_save_sync_status(self, tmp_path, monkeypatch):
        """Sync status is persisted to file."""
        monkeypatch.setenv("HOME", str(tmp_path))
        tenant_path = tmp_path / '.corvin' / 'tenants' / '_default'
        tenant_path.mkdir(parents=True)

        import core.console.corvin_console.sync_worker as sw
        original_tenant_path = sw.TENANT_PATH
        sw.TENANT_PATH = tenant_path

        try:
            worker = SyncWorker(interval_seconds=60)

            result = {
                'success': True,
                'skills_synced': 5,
                'timestamp': datetime.utcnow().isoformat(),
            }

            worker._save_sync_status(result)

            status_file = tenant_path / 'config' / '.sync-status'
            assert status_file.exists()

            with open(status_file) as f:
                saved_status = json.load(f)

            assert saved_status['sync_status'] == 'success'
            assert saved_status['skills_synced'] == 5
            assert saved_status['sync_error'] is None

        finally:
            sw.TENANT_PATH = original_tenant_path

    def test_should_sync_not_configured(self, tmp_path, monkeypatch):
        """Should not sync if auto_sync is disabled."""
        monkeypatch.setenv("HOME", str(tmp_path))
        tenant_path = tmp_path / '.corvin' / 'tenants' / '_default'
        tenant_path.mkdir(parents=True)

        import core.console.corvin_console.sync_worker as sw
        original_tenant_path = sw.TENANT_PATH
        sw.TENANT_PATH = tenant_path

        try:
            worker = SyncWorker(interval_seconds=60)

            github_cfg = {'auto_sync': False}

            assert worker._should_sync(github_cfg) is False

        finally:
            sw.TENANT_PATH = original_tenant_path


class TestSyncWorkerGlobal:
    """Test global worker singleton."""

    def test_get_sync_worker(self):
        """Get or create global worker."""
        from core.console.corvin_console.sync_worker import get_sync_worker

        worker1 = get_sync_worker()
        worker2 = get_sync_worker()

        assert worker1 is worker2

    def test_start_stop_global_worker(self):
        """Start and stop global worker."""
        from core.console.corvin_console.sync_worker import (
            get_sync_worker,
            start_sync_worker,
            stop_sync_worker,
        )

        # Reset global state
        import core.console.corvin_console.sync_worker as sw
        sw._worker = None

        worker = start_sync_worker()
        assert worker.running is True

        time.sleep(0.2)

        stop_sync_worker()
        assert worker.running is False
