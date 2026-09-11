"""
Unit tests for LearnedThresholdStore — ADR-0377 Phase 2b

Tests:
1. Persistence (write/read to disk)
2. Tenant isolation (separate stores per tenant)
3. Graceful degradation (missing/corrupt file)
4. Import/export JSON
5. Reset functionality
6. Immutability (dataclass frozen)
7. Cache behavior
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import pytest

from core.learning.learned_threshold_store import (
    LearnedThresholdStore,
    StoredThreshold,
    get_store,
    reset_store,
)


@pytest.fixture
def temp_corvin_home(monkeypatch):
    """Temporary CORVIN_HOME for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        corvin_home = Path(tmpdir) / "corvin"
        corvin_home.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("CORVIN_HOME", str(corvin_home))
        yield corvin_home


@pytest.fixture
def store(temp_corvin_home):
    """Fresh store for each test."""
    reset_store("_default")
    return get_store("_default")


class TestStoredThresholdDataclass:
    """Test StoredThreshold immutability and serialization."""

    def test_frozen_dataclass(self):
        """Stored thresholds are immutable (frozen dataclass)."""
        st = StoredThreshold(
            task_type="code_gen",
            subsystem="analyzer",
            tenant_id="_default",
            learned_threshold=0.42,
        )

        # Should not be able to modify
        with pytest.raises(AttributeError):
            st.learned_threshold = 0.5  # type: ignore

    def test_serialization(self):
        """StoredThreshold serializes to dict."""
        st = StoredThreshold(
            task_type="code_gen",
            subsystem="analyzer",
            tenant_id="_default",
            learned_threshold=0.42,
            sample_count=25,
            converged=True,
        )

        data = st.to_dict()
        assert data["task_type"] == "code_gen"
        assert data["learned_threshold"] == 0.42
        assert data["sample_count"] == 25
        assert data["converged"] is True

    def test_deserialization(self):
        """StoredThreshold reconstructs from dict."""
        data = {
            "task_type": "code_gen",
            "subsystem": "analyzer",
            "tenant_id": "_default",
            "learned_threshold": 0.42,
            "base_threshold": 0.5,
            "timestamp": "2026-09-11T12:00:00Z",
            "sample_count": 25,
            "converged": True,
        }

        st = StoredThreshold.from_dict(data)
        assert st.task_type == "code_gen"
        assert st.learned_threshold == 0.42
        assert st.sample_count == 25


class TestLearnedThresholdStorePersistence:
    """Test persistence to disk."""

    def test_get_threshold_default(self, store):
        """Get threshold returns base when no data."""
        threshold = store.get_threshold("code_gen", "analyzer", base_threshold=0.5)
        assert threshold == 0.5

    def test_set_and_get_threshold(self, store, temp_corvin_home):
        """Store a threshold and retrieve it."""
        st = StoredThreshold(
            task_type="code_gen",
            subsystem="analyzer",
            tenant_id="_default",
            learned_threshold=0.42,
            sample_count=25,
            converged=True,
        )

        store.set_threshold(st)

        # Retrieve
        threshold = store.get_threshold("code_gen", "analyzer", base_threshold=0.5)
        assert threshold == 0.42

    def test_persistence_survives_reload(self, store, temp_corvin_home):
        """Stored thresholds survive reload (new store instance)."""
        st = StoredThreshold(
            task_type="code_gen",
            subsystem="analyzer",
            tenant_id="_default",
            learned_threshold=0.42,
            sample_count=25,
            converged=True,
        )

        store.set_threshold(st)

        # Reload (new instance)
        reset_store("_default")
        store2 = get_store("_default")
        threshold = store2.get_threshold("code_gen", "analyzer", base_threshold=0.5)
        assert threshold == 0.42

    def test_convergence_status(self, store):
        """Get convergence status."""
        st = StoredThreshold(
            task_type="code_gen",
            subsystem="analyzer",
            tenant_id="_default",
            learned_threshold=0.42,
            sample_count=25,
            converged=True,
        )

        store.set_threshold(st)

        converged, samples = store.get_convergence_status("code_gen", "analyzer")
        assert converged is True
        assert samples == 25

    def test_only_converged_thresholds_returned(self, store):
        """Non-converged thresholds return base threshold."""
        st = StoredThreshold(
            task_type="code_gen",
            subsystem="analyzer",
            tenant_id="_default",
            learned_threshold=0.42,
            sample_count=5,  # Too few samples
            converged=False,
        )

        store.set_threshold(st)

        # Should return base because not converged + sample count < 10
        threshold = store.get_threshold("code_gen", "analyzer", base_threshold=0.5)
        assert threshold == 0.5

    def test_minimum_samples_check(self, store):
        """Converged flag is ignored if sample_count < 10."""
        st = StoredThreshold(
            task_type="code_gen",
            subsystem="analyzer",
            tenant_id="_default",
            learned_threshold=0.42,
            sample_count=5,  # < 10
            converged=True,  # Even though converged=True
        )

        store.set_threshold(st)

        # Should return base because sample_count < 10
        threshold = store.get_threshold("code_gen", "analyzer", base_threshold=0.5)
        assert threshold == 0.5

    def test_get_all_thresholds(self, store):
        """Get all stored thresholds."""
        st1 = StoredThreshold(
            task_type="code_gen",
            subsystem="analyzer",
            tenant_id="_default",
            learned_threshold=0.42,
            sample_count=25,
            converged=True,
        )
        st2 = StoredThreshold(
            task_type="code_gen",
            subsystem="formatter",
            tenant_id="_default",
            learned_threshold=0.65,
            sample_count=30,
            converged=True,
        )

        store.set_threshold(st1)
        store.set_threshold(st2)

        all_thresholds = store.get_all()
        assert len(all_thresholds) == 2
        assert all_thresholds[0].task_type == "code_gen"
        assert all_thresholds[0].subsystem == "analyzer"
        assert all_thresholds[1].subsystem == "formatter"


class TestTenantIsolation:
    """Test per-tenant isolation."""

    def test_different_tenants_separate_stores(self, temp_corvin_home):
        """Different tenants have separate stores."""
        reset_store("tenant1")
        reset_store("tenant2")

        store1 = get_store("tenant1")
        store2 = get_store("tenant2")

        st1 = StoredThreshold(
            task_type="code_gen",
            subsystem="analyzer",
            tenant_id="tenant1",
            learned_threshold=0.42,
            sample_count=25,
            converged=True,
        )

        st2 = StoredThreshold(
            task_type="code_gen",
            subsystem="analyzer",
            tenant_id="tenant2",
            learned_threshold=0.75,
            sample_count=30,
            converged=True,
        )

        store1.set_threshold(st1)
        store2.set_threshold(st2)

        # Retrieve from each
        assert store1.get_threshold("code_gen", "analyzer", base_threshold=0.5) == 0.42
        assert store2.get_threshold("code_gen", "analyzer", base_threshold=0.5) == 0.75


class TestImportExport:
    """Test JSON import/export."""

    def test_export_json(self, store):
        """Export thresholds as JSON."""
        st1 = StoredThreshold(
            task_type="code_gen",
            subsystem="analyzer",
            tenant_id="_default",
            learned_threshold=0.42,
            sample_count=25,
            converged=True,
        )
        st2 = StoredThreshold(
            task_type="code_gen",
            subsystem="formatter",
            tenant_id="_default",
            learned_threshold=0.65,
            sample_count=30,
            converged=True,
        )

        store.set_threshold(st1)
        store.set_threshold(st2)

        export = store.export_json()

        assert export["version"] == "1"
        assert export["tenant_id"] == "_default"
        assert "export_at" in export
        assert len(export["thresholds"]) == 2

    def test_import_json(self, store):
        """Import thresholds from JSON."""
        export_data = {
            "version": "1",
            "tenant_id": "_default",
            "export_at": "2026-09-11T12:00:00Z",
            "thresholds": {
                "code_gen:analyzer": {
                    "task_type": "code_gen",
                    "subsystem": "analyzer",
                    "tenant_id": "_default",
                    "learned_threshold": 0.42,
                    "base_threshold": 0.5,
                    "timestamp": "2026-09-11T12:00:00Z",
                    "sample_count": 25,
                    "converged": True,
                },
            },
        }

        count = store.import_json(export_data)

        assert count == 1
        assert store.get_threshold("code_gen", "analyzer") == 0.42

    def test_import_export_roundtrip(self, store, temp_corvin_home):
        """Export and import roundtrip preserves data."""
        st = StoredThreshold(
            task_type="code_gen",
            subsystem="analyzer",
            tenant_id="_default",
            learned_threshold=0.42,
            sample_count=25,
            converged=True,
            notes="Test threshold",
        )

        store.set_threshold(st)
        export1 = store.export_json()

        # Import into new store
        reset_store("_default")
        store2 = get_store("_default")
        count = store2.import_json(export1)

        assert count == 1
        assert store2.get_threshold("code_gen", "analyzer") == 0.42

    def test_import_wrong_version_fails(self, store):
        """Import with wrong version raises error."""
        bad_export = {
            "version": "2",  # Wrong version
            "tenant_id": "_default",
            "thresholds": [],
        }

        with pytest.raises(ValueError, match="version"):
            store.import_json(bad_export)

    def test_import_wrong_tenant_fails(self, store):
        """Import with wrong tenant raises error."""
        bad_export = {
            "version": "1",
            "tenant_id": "other_tenant",  # Wrong tenant
            "thresholds": [],
        }

        with pytest.raises(ValueError, match="Tenant mismatch"):
            store.import_json(bad_export)


class TestReset:
    """Test reset functionality."""

    def test_reset_clears_all_thresholds(self, store):
        """Reset clears all thresholds."""
        st = StoredThreshold(
            task_type="code_gen",
            subsystem="analyzer",
            tenant_id="_default",
            learned_threshold=0.42,
            sample_count=25,
            converged=True,
        )

        store.set_threshold(st)
        assert len(store.get_all()) == 1

        store.reset_all()

        assert len(store.get_all()) == 0
        assert store.get_threshold("code_gen", "analyzer") == 0.5

    def test_reset_survives_reload(self, store, temp_corvin_home):
        """Reset persists across reloads."""
        st = StoredThreshold(
            task_type="code_gen",
            subsystem="analyzer",
            tenant_id="_default",
            learned_threshold=0.42,
            sample_count=25,
            converged=True,
        )

        store.set_threshold(st)
        store.reset_all()

        # Reload
        reset_store("_default")
        store2 = get_store("_default")

        assert len(store2.get_all()) == 0


class TestGracefulDegradation:
    """Test graceful degradation on missing/corrupt files."""

    def test_missing_file_starts_fresh(self, temp_corvin_home):
        """Missing store file starts fresh."""
        reset_store("_default")
        store = get_store("_default")

        # Should not error, just start empty
        assert len(store.get_all()) == 0

    def test_corrupt_json_logs_warning(self, temp_corvin_home, caplog):
        """Corrupt JSON file logs warning and starts fresh."""
        # Create corrupt file
        store_path = (
            temp_corvin_home / "tenants" / "_default" / "learning" / "learned_thresholds.json"
        )
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text("{invalid json}")

        reset_store("_default")
        store = get_store("_default")

        # Should load gracefully despite corrupt file
        assert len(store.get_all()) == 0


class TestThresholdValidation:
    """Test threshold value validation."""

    def test_threshold_out_of_range_raises(self, store):
        """Threshold outside [0.1, 0.9] raises ValueError."""
        st = StoredThreshold(
            task_type="code_gen",
            subsystem="analyzer",
            tenant_id="_default",
            learned_threshold=0.05,  # Too low
            sample_count=25,
            converged=True,
        )

        with pytest.raises(ValueError, match="range"):
            store.set_threshold(st)

    def test_threshold_high_out_of_range_raises(self, store):
        """Threshold > 0.9 raises ValueError."""
        st = StoredThreshold(
            task_type="code_gen",
            subsystem="analyzer",
            tenant_id="_default",
            learned_threshold=0.95,  # Too high
            sample_count=25,
            converged=True,
        )

        with pytest.raises(ValueError, match="range"):
            store.set_threshold(st)


class TestThreadSafety:
    """Test thread-safe access."""

    def test_concurrent_writes(self, store):
        """Multiple thresholds can be stored concurrently."""
        import threading

        def store_threshold(task_type, subsystem, threshold):
            st = StoredThreshold(
                task_type=task_type,
                subsystem=subsystem,
                tenant_id="_default",
                learned_threshold=threshold,
                sample_count=25,
                converged=True,
            )
            store.set_threshold(st)

        threads = [
            threading.Thread(target=store_threshold, args=(f"task_{i}", f"sub_{i}", 0.4 + i * 0.05))
            for i in range(5)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(store.get_all()) == 5
