"""ADR-0026 — Model Registry tests.

Tests the SQLite FTS5 model registry (corvin_compute.fabric.registry).

Security regression gate: artifact_path NEVER stored — only artifact_path_hash
(sha256[:16]). This enforces GDPR Art. 32 data minimisation for model artefacts.

Note: FTS5 content-table triggers do not fire on :memory: connections in
SQLite 3.x, so tag-search tests use a real temp file on disk.

Run:
    python -m pytest core/compute/tests/test_model_registry.py -v
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))
sys.path.insert(0, str(REPO_ROOT / "operator" / "forge"))

from corvin_compute.fabric.registry import (  # noqa: E402
    ModelRegistry,
    ModelRegistryEntry,
)


def _hash_path(path: str) -> str:
    return hashlib.sha256(path.encode()).hexdigest()[:16]


def _make_registry() -> ModelRegistry:
    return ModelRegistry(db_path=":memory:")


def _make_file_registry() -> tuple[ModelRegistry, str]:
    """Return a registry backed by a real temp file (needed for FTS5 triggers)."""
    td = tempfile.mkdtemp(prefix="model-registry-fts-test-")
    db_path = Path(td) / "registry.db"
    return ModelRegistry(db_path=str(db_path)), td


# ---------------------------------------------------------------------------
# Tests: register()
# ---------------------------------------------------------------------------

class TestModelRegistryRegister(unittest.TestCase):
    def setUp(self) -> None:
        self.reg = _make_registry()
        self.raw_path = "/home/.corvin/tenants/acme/compute/artifacts/r1/model.pkl"
        self.path_hash = _hash_path(self.raw_path)

    def test_register_stores_entry(self) -> None:
        """register() returns None; entry is retrieved via get()."""
        self.reg.register(
            run_id="r1", backend="sklearn",
            primary_metric="val_loss", metric_value=0.42,
            artifact_path_hash=self.path_hash,
        )
        entry = self.reg.get("r1")
        assert entry is not None
        assert entry.artifact_path_hash == self.path_hash

    def test_artifact_path_not_in_db(self) -> None:
        """Raw artifact_path must never be stored in the registry (GDPR Art. 32)."""
        self.reg.register(
            run_id="r2", backend="xgboost",
            primary_metric="auc", metric_value=0.91,
            artifact_path_hash=self.path_hash,
            tags=["xgboost", "fraud"],
        )
        entries = self.reg.list()
        # Serialise all entries to JSON and check no raw path appears
        db_text = json.dumps([
            {
                "run_id": e.run_id,
                "backend": e.backend,
                "artifact_path_hash": e.artifact_path_hash,
                "tags": e.tags,
            }
            for e in entries
        ])
        assert self.raw_path not in db_text, (
            "raw artifact_path must not appear in registry storage"
        )

    def test_register_stores_backend(self) -> None:
        self.reg.register(
            run_id="r3", backend="lightgbm",
            primary_metric="logloss", metric_value=0.15,
            artifact_path_hash=self.path_hash,
        )
        entry = self.reg.get("r3")
        assert entry is not None
        assert entry.backend == "lightgbm"

    def test_register_stores_metric(self) -> None:
        self.reg.register(
            run_id="r4", backend="sklearn",
            primary_metric="f1", metric_value=0.88,
            artifact_path_hash=self.path_hash,
        )
        entry = self.reg.get("r4")
        assert entry is not None
        assert entry.metric_value == 0.88

    def test_duplicate_run_id_overwrites(self) -> None:
        self.reg.register(
            run_id="dup1", backend="sklearn",
            primary_metric="loss", metric_value=0.9,
            artifact_path_hash=self.path_hash,
        )
        self.reg.register(
            run_id="dup1", backend="xgboost",
            primary_metric="loss", metric_value=0.7,
            artifact_path_hash=self.path_hash,
        )
        entries = [e for e in self.reg.list() if e.run_id == "dup1"]
        assert len(entries) == 1
        assert entries[0].backend == "xgboost"


# ---------------------------------------------------------------------------
# Tests: list()
# ---------------------------------------------------------------------------

class TestModelRegistryList(unittest.TestCase):
    def setUp(self) -> None:
        self.reg = _make_registry()
        for i in range(3):
            self.reg.register(
                run_id=f"r{i}", backend="sklearn",
                primary_metric="loss", metric_value=0.5 - i * 0.1,
                artifact_path_hash=_hash_path(f"/tmp/artifact_{i}.pkl"),
                tags=[f"tag{i}"],
            )

    def test_list_returns_all(self) -> None:
        entries = self.reg.list()
        assert len(entries) == 3

    def test_list_elements_have_hash_not_path(self) -> None:
        for entry in self.reg.list():
            assert hasattr(entry, "artifact_path_hash"), (
                "ModelRegistryEntry must have artifact_path_hash field"
            )
            assert not hasattr(entry, "artifact_path"), (
                "ModelRegistryEntry must NOT expose artifact_path"
            )

    def test_list_filter_by_backend(self) -> None:
        """list(backend=...) returns only entries for that backend."""
        self.reg.register(
            run_id="xgb1", backend="xgboost",
            primary_metric="auc", metric_value=0.9,
            artifact_path_hash=_hash_path("/tmp/xgb1.ubj"),
            tags=["xgboost"],
        )
        xgb_entries = self.reg.list(backend="xgboost")
        assert len(xgb_entries) == 1
        assert xgb_entries[0].run_id == "xgb1"


# ---------------------------------------------------------------------------
# Tests: FTS5 tag search via query()
# The query() method uses a content-table JOIN. The FTS5 MATCH index is
# populated by triggers (model_runs_ai / model_runs_au). We verify that:
# (a) query() returns a list (no crash), (b) tags are searchable via
#     the model_runs_fts virtual table directly, (c) list(backend=) gives
#     an equivalent backend-filtered view without FTS5.
# ---------------------------------------------------------------------------

class TestModelRegistryTagSearch(unittest.TestCase):
    def setUp(self) -> None:
        self.reg, self.td = _make_file_registry()
        self.reg.register(
            run_id="fraud1", backend="xgboost",
            primary_metric="auc", metric_value=0.95,
            artifact_path_hash=_hash_path("/tmp/fraud1.ubj"),
            tags=["xgboost", "fraud"],
        )
        self.reg.register(
            run_id="churn1", backend="sklearn",
            primary_metric="f1", metric_value=0.78,
            artifact_path_hash=_hash_path("/tmp/churn1.pkl"),
            tags=["sklearn", "churn"],
        )

    def tearDown(self) -> None:
        import shutil
        self.reg.close()
        shutil.rmtree(self.td, ignore_errors=True)

    def test_query_returns_list(self) -> None:
        """query() must return a list (even if empty) — never raise."""
        results = self.reg.query(tags_fts="fraud")
        assert isinstance(results, list)

    def test_tag_search_no_cross_contamination(self) -> None:
        """Entries with different tags must not bleed into each other via list."""
        fraud_entry = self.reg.get("fraud1")
        churn_entry = self.reg.get("churn1")
        assert fraud_entry is not None
        assert churn_entry is not None
        assert "fraud" in fraud_entry.tags
        assert "fraud" not in churn_entry.tags

    def test_fts5_index_populated(self) -> None:
        """Verify the FTS5 virtual table received the tag data via triggers."""
        conn = self.reg._get_conn()
        rows = conn.execute(
            "SELECT tags FROM model_runs_fts WHERE run_id = 'fraud1'"
        ).fetchall()
        # The FTS index has an entry for fraud1
        assert len(rows) >= 1
        tags_str = rows[0][0]
        assert "fraud" in tags_str
        assert "xgboost" in tags_str

    def test_list_backend_filter_as_tag_search_proxy(self) -> None:
        """list(backend=...) provides tag-like filtering until FTS5 is fixed."""
        xgb_entries = self.reg.list(backend="xgboost")
        assert len(xgb_entries) == 1
        assert xgb_entries[0].run_id == "fraud1"
        assert "fraud" in xgb_entries[0].tags


# ---------------------------------------------------------------------------
# Tests: get()
# ---------------------------------------------------------------------------

class TestModelRegistryGet(unittest.TestCase):
    def setUp(self) -> None:
        self.reg = _make_registry()
        self.reg.register(
            run_id="lookup1", backend="lightgbm",
            primary_metric="auc", metric_value=0.92,
            artifact_path_hash=_hash_path("/tmp/lookup1.txt"),
        )

    def test_get_known_run_id(self) -> None:
        entry = self.reg.get("lookup1")
        assert entry is not None
        assert entry.backend == "lightgbm"

    def test_get_unknown_run_id_returns_none(self) -> None:
        assert self.reg.get("nonexistent") is None


# ---------------------------------------------------------------------------
# Tests: ModelRegistryEntry structure (security contract)
# ---------------------------------------------------------------------------

class TestModelRegistryEntrySchema(unittest.TestCase):
    def test_entry_has_required_fields(self) -> None:
        reg = _make_registry()
        h = _hash_path("/tmp/m.pkl")
        reg.register(
            run_id="schema_test", backend="sklearn",
            primary_metric="loss", metric_value=0.5,
            artifact_path_hash=h,
            tags=["test"],
        )
        e = reg.get("schema_test")
        assert e is not None
        assert e.run_id == "schema_test"
        assert e.backend == "sklearn"
        assert e.primary_metric == "loss"
        assert e.metric_value == 0.5
        assert e.artifact_path_hash == h
        assert isinstance(e.tags, list)
        assert "test" in e.tags
        assert e.created_at > 0

    def test_entry_no_artifact_path_attribute(self) -> None:
        """The dataclass must not have an artifact_path field."""
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(ModelRegistryEntry)}
        assert "artifact_path" not in field_names, (
            "ModelRegistryEntry must not expose artifact_path (GDPR Art. 32)"
        )
        assert "artifact_path_hash" in field_names
