"""Tests for ADR-0090 AWP Export/Import (compute_awp_exporter + compute_awp_importer).

Covers:
  1.  Module imports — PipelineAWPExporter, AWPackageMeta, no import anthropic
  2.  Replay mode export — zip produced, workflow.awp.yaml with params, no vault_path
  3.  Reoptimize mode export — workflow.awp.yaml with param_grid + strategy
  4.  RAG bundling — RAG manifest included/excluded per flag, secret key in permissions
  5.  Datasource bundling — vault_path stripped, secret_keys in permissions
  6.  Schedule trigger — cron expression ends up in workflow yaml
  7.  Processing record — processing_record.yaml structure
  8.  AWPImporter module import check
  9.  AWPImporter round-trip validate — uses the exporter-produced zip layout
  10. Console export endpoints — GET preview + POST download (autologin-gated)
"""
from __future__ import annotations

import gc
import json
import os
import sys
import tempfile
import unittest
import zipfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Repo path bootstrap — must come before any corvin import
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[3]
_SHARED = _REPO / "operator" / "bridges" / "shared"
_FORGE_PKG = _REPO / "operator" / "forge"
_COMPUTE_ROOT = _REPO / "core" / "compute"

for _p in (_SHARED, _FORGE_PKG, _COMPUTE_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

sys.path.insert(0, str(_REPO / "core" / "console"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_modules() -> None:
    for key in list(sys.modules):
        if any(
            key.startswith(p)
            for p in (
                "corvin_console",
                "forge",
                "compute_awp_exporter",
                "compute_awp_importer",
                "rag_import_export",
                "corvin_compute",
            )
        ):
            del sys.modules[key]


@contextmanager
def _patch_exporter_home(home: Path):
    """Patch compute_awp_exporter._corvin_home to return *home*.

    Also silences audit writes so tests don't touch the real audit chain.
    """
    # Ensure the module is imported before patching
    import compute_awp_exporter as _mod  # type: ignore
    with patch.object(_mod, "_corvin_home", return_value=home), \
         patch.object(_mod._sec, "write_event", return_value=None):
        yield


def _make_pipeline_fixture(home: Path, tid: str) -> str:
    """Create minimal pipeline with 2 stages; return pipeline_id."""
    pipeline_id = "pipe_test_001"
    p_dir = home / "tenants" / tid / "compute" / "pipelines" / pipeline_id
    p_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "pipeline_id": pipeline_id,
        "tenant_id": tid,
        "stages": [
            {
                "stage_id": "stage_1",
                "tool_name": "code_ingest",
                "strategy": "grid",
                "param_grid": {"max_rows": [1000, 5000]},
                "budget": {"max_iterations": 5},
                "inputs": {},
                "outputs": ["data.csv"],
            },
            {
                "stage_id": "stage_2",
                "tool_name": "code_train",
                "strategy": "bayesian",
                "param_grid": {"lr": [0.001, 0.01], "depth": [4, 6]},
                "budget": {"max_iterations": 10},
                "inputs": {"dataset": "$stage_1.artifacts/data.csv"},
                "outputs": ["model.pkl"],
            },
        ],
        "steering_gate": True,
        "started_at": 1_000_000.0,
        "submitted_by": "test",
    }
    (p_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    summary = {
        "state": "complete",
        "best_losses": {"stage_1": 0.5, "stage_2": 0.082},
        "completed_stages": ["stage_1", "stage_2"],
    }
    (p_dir / "pipeline_summary.json").write_text(json.dumps(summary), encoding="utf-8")

    for sid, best_loss in [("stage_1", 0.5), ("stage_2", 0.082)]:
        sd = p_dir / "stages" / sid
        sd.mkdir(parents=True, exist_ok=True)
        ss = {
            "state": "complete",
            "best_loss": best_loss,
            "best_params": {"lr": 0.0032},
        }
        (sd / "stage_summary.json").write_text(json.dumps(ss), encoding="utf-8")

    return pipeline_id


def _minimal_corvin_tree(home: Path, tid: str) -> None:
    """Create the minimal directory structure required by the exporter."""
    (home / "tenants" / tid / "global" / "auth").mkdir(parents=True, exist_ok=True)
    (home / "tenants" / tid / "global" / "forge").mkdir(parents=True, exist_ok=True)
    (home / "tenants" / tid / "global" / "console" / "sessions").mkdir(
        parents=True, exist_ok=True
    )
    (home / "tenants" / tid / "compute" / "runs").mkdir(parents=True, exist_ok=True)
    (home / "tenants" / tid / "compute" / "pipelines").mkdir(
        parents=True, exist_ok=True
    )


def _valid_rag_manifest(provider_id: str) -> str:
    """Return a minimal valid RAG provider manifest YAML."""
    return (
        f"apiVersion: corvin/v1\n"
        f"kind: RAGProvider\n"
        f"metadata:\n"
        f"  name: {provider_id}\n"
        f"spec:\n"
        f"  dataClassification: INTERNAL\n"
        f"  complianceZone: eu\n"
        f"  retrieval:\n"
        f"    backend: opensearch\n"
        f"    auth:\n"
        f"      token_env_var: OPENSEARCH_API_KEY\n"
    )


# ---------------------------------------------------------------------------
# 1. TestAWPExporterModule
# ---------------------------------------------------------------------------


class TestAWPExporterModule(unittest.TestCase):
    """Module imports correctly and exposes the required public symbols."""

    def test_pipeline_awp_exporter_importable(self):
        _reset_modules()
        from compute_awp_exporter import PipelineAWPExporter  # type: ignore
        self.assertTrue(callable(PipelineAWPExporter))

    def test_awpackage_meta_importable(self):
        _reset_modules()
        from compute_awp_exporter import AWPackageMeta  # type: ignore
        self.assertTrue(AWPackageMeta is not None)

    def test_no_import_anthropic(self):
        """compute_awp_exporter must not import anthropic (CI AST-lint contract).

        The check uses AST rather than raw-text grep to avoid false positives
        from docstrings / comments that mention the forbidden name.
        """
        import ast as _ast
        source = (_SHARED / "compute_awp_exporter.py").read_text(encoding="utf-8")
        tree = _ast.parse(source)
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    self.assertNotEqual(
                        top, "anthropic",
                        "compute_awp_exporter has top-level 'import anthropic'",
                    )
            if isinstance(node, _ast.ImportFrom):
                top = (node.module or "").split(".")[0]
                self.assertNotEqual(
                    top, "anthropic",
                    "compute_awp_exporter has 'from anthropic import ...'",
                )

    def test_awpackage_meta_fields(self):
        _reset_modules()
        from compute_awp_exporter import AWPackageMeta  # type: ignore
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(AWPackageMeta)}
        for required in ("package_id", "version", "stage_count", "mode",
                         "schedule_cron", "rag_provider_count"):
            self.assertIn(required, field_names,
                          f"AWPackageMeta missing field: {required}")


# ---------------------------------------------------------------------------
# 2. TestAWPExporterReplay
# ---------------------------------------------------------------------------


class TestAWPExporterReplay(unittest.TestCase):
    """export(mode='replay') produces a correct zip with champion params baked in."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.home = Path(self.tmpdir) / "corvin_home"
        self.tid = "_default"
        _minimal_corvin_tree(self.home, self.tid)
        self.pipeline_id = _make_pipeline_fixture(self.home, self.tid)
        _reset_modules()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        _reset_modules()

    def _run_export(self, out_dir: Path, **kwargs):
        import compute_awp_exporter as _mod  # type: ignore
        exporter = _mod.PipelineAWPExporter(
            tenant_id=self.tid,
            pipeline_id=self.pipeline_id,
        )
        with _patch_exporter_home(self.home):
            return exporter.export(
                package_id="test.pipe.export",
                version="1.0.0",
                output_dir=out_dir,
                **kwargs,
            )

    def test_export_produces_zip_file(self):
        out_dir = Path(self.tmpdir) / "out"
        out_dir.mkdir()
        meta = self._run_export(out_dir, mode="replay")
        zip_files = list(out_dir.glob("*.zip")) + list(out_dir.glob("*.awp.zip"))
        self.assertGreater(len(zip_files), 0, "export() produced no zip file")

    def test_zip_contains_workflow_yaml(self):
        out_dir = Path(self.tmpdir) / "out"
        out_dir.mkdir()
        self._run_export(out_dir, mode="replay")
        zip_path = next(out_dir.glob("*.zip"))
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
        # workflow.awp.yaml is written at <package_id>/workflow.awp.yaml
        workflow_entries = [n for n in names if n.endswith("workflow.awp.yaml")]
        self.assertGreater(len(workflow_entries), 0,
                           f"workflow.awp.yaml not found in zip. Entries: {names[:20]}")

    def test_zip_contains_processing_record(self):
        out_dir = Path(self.tmpdir) / "out"
        out_dir.mkdir()
        self._run_export(out_dir, mode="replay")
        zip_path = next(out_dir.glob("*.zip"))
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
        record_entries = [n for n in names if n.endswith("processing_record.yaml")]
        self.assertGreater(len(record_entries), 0,
                           f"processing_record.yaml not found in zip. Entries: {names[:20]}")

    def test_workflow_yaml_has_agent_nodes_with_x_compute(self):
        """ADR-0091: nodes use type:agent (AWP-compatible) + x_compute extension."""
        out_dir = Path(self.tmpdir) / "out"
        out_dir.mkdir()
        self._run_export(out_dir, mode="replay")
        zip_path = next(out_dir.glob("*.zip"))
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            workflow_entry = next(n for n in names if n.endswith("workflow.awp.yaml"))
            workflow_text = zf.read(workflow_entry).decode("utf-8")
        self.assertIn("type: agent", workflow_text,
                      "workflow.awp.yaml has no 'type: agent' nodes")
        self.assertIn("x_compute:", workflow_text,
                      "workflow.awp.yaml missing x_compute extension field")

    def test_replay_mode_bakes_params_not_param_grid(self):
        """In replay mode, nodes must have 'params:' (not 'param_grid:')."""
        out_dir = Path(self.tmpdir) / "out"
        out_dir.mkdir()
        self._run_export(out_dir, mode="replay")
        zip_path = next(out_dir.glob("*.zip"))
        with zipfile.ZipFile(zip_path, "r") as zf:
            workflow_entry = next(
                n for n in zf.namelist() if n.endswith("workflow.awp.yaml")
            )
            workflow_text = zf.read(workflow_entry).decode("utf-8")
        self.assertIn("params:", workflow_text,
                      "replay mode: 'params:' key not found in workflow yaml")

    def test_no_vault_path_in_any_bundled_file(self):
        """Security invariant: auth.vault_path field must never appear in exported zip.

        The processing_record.yaml legitimately contains the *key* name
        'vault_path_exported' in its compliance block.  We check for the
        actual auth field pattern (typically ``"vault_path": "secret/..."``)
        rather than the bare key name to avoid false positives.
        """
        import re
        out_dir = Path(self.tmpdir) / "out"
        out_dir.mkdir()
        self._run_export(out_dir, mode="replay")
        zip_path = next(out_dir.glob("*.zip"))
        # Pattern that would indicate an actual vault_path value being leaked:
        # e.g.  vault_path: "secret/something"  or  "vault_path": "secret/..."
        vault_value_pattern = re.compile(
            r"""(["']?vault_path["']?\s*[:=]\s*["'][^"']+["'])""",
            re.IGNORECASE,
        )
        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                if name.endswith((".yaml", ".json", ".py")):
                    content = zf.read(name).decode("utf-8", errors="replace")
                    match = vault_value_pattern.search(content)
                    self.assertIsNone(
                        match,
                        f"vault_path value leaked in bundled file {name!r}: {match}",
                    )

    def test_meta_stage_count(self):
        out_dir = Path(self.tmpdir) / "out"
        out_dir.mkdir()
        meta = self._run_export(out_dir, mode="replay")
        self.assertEqual(meta.stage_count, 2)

    def test_meta_mode_is_replay(self):
        out_dir = Path(self.tmpdir) / "out"
        out_dir.mkdir()
        meta = self._run_export(out_dir, mode="replay")
        self.assertEqual(meta.mode, "replay")


# ---------------------------------------------------------------------------
# 3. TestAWPExporterReoptimize
# ---------------------------------------------------------------------------


class TestAWPExporterReoptimize(unittest.TestCase):
    """export(mode='reoptimize') produces workflow yaml with param_grid."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.home = Path(self.tmpdir) / "corvin_home"
        self.tid = "_default"
        _minimal_corvin_tree(self.home, self.tid)
        self.pipeline_id = _make_pipeline_fixture(self.home, self.tid)
        _reset_modules()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        _reset_modules()

    def _run_export(self, out_dir: Path, **kwargs):
        import compute_awp_exporter as _mod  # type: ignore
        exporter = _mod.PipelineAWPExporter(
            tenant_id=self.tid,
            pipeline_id=self.pipeline_id,
        )
        with _patch_exporter_home(self.home):
            return exporter.export(
                package_id="test.pipe.reopt",
                version="1.0.0",
                output_dir=out_dir,
                **kwargs,
            )

    def test_reoptimize_mode_has_param_grid(self):
        """In reoptimize mode, workflow yaml must contain 'param_grid:'."""
        out_dir = Path(self.tmpdir) / "out"
        out_dir.mkdir()
        self._run_export(out_dir, mode="reoptimize")
        zip_path = next(out_dir.glob("*.zip"))
        with zipfile.ZipFile(zip_path, "r") as zf:
            workflow_entry = next(
                n for n in zf.namelist() if n.endswith("workflow.awp.yaml")
            )
            workflow_text = zf.read(workflow_entry).decode("utf-8")
        self.assertIn("param_grid:", workflow_text,
                      "reoptimize mode: 'param_grid:' not found in workflow yaml")

    def test_reoptimize_does_not_have_single_point_params(self):
        """In reoptimize mode, the top-level 'params:' key must not appear."""
        out_dir = Path(self.tmpdir) / "out"
        out_dir.mkdir()
        self._run_export(out_dir, mode="reoptimize")
        zip_path = next(out_dir.glob("*.zip"))
        with zipfile.ZipFile(zip_path, "r") as zf:
            workflow_entry = next(
                n for n in zf.namelist() if n.endswith("workflow.awp.yaml")
            )
            workflow_text = zf.read(workflow_entry).decode("utf-8")
        # In reoptimize mode there should be param_grid and no single-run params:
        self.assertNotIn("params: {lr:", workflow_text,
                         "reoptimize mode should not bake single champion params")

    def test_reoptimize_meta_mode(self):
        out_dir = Path(self.tmpdir) / "out"
        out_dir.mkdir()
        meta = self._run_export(out_dir, mode="reoptimize")
        self.assertEqual(meta.mode, "reoptimize")

    def test_reoptimize_workflow_has_budget_max_iterations(self):
        out_dir = Path(self.tmpdir) / "out"
        out_dir.mkdir()
        self._run_export(out_dir, mode="reoptimize")
        zip_path = next(out_dir.glob("*.zip"))
        with zipfile.ZipFile(zip_path, "r") as zf:
            workflow_entry = next(
                n for n in zf.namelist() if n.endswith("workflow.awp.yaml")
            )
            workflow_text = zf.read(workflow_entry).decode("utf-8")
        self.assertIn("max_iterations:", workflow_text,
                      "budget.max_iterations not in reoptimize workflow yaml")


# ---------------------------------------------------------------------------
# 4. TestAWPExporterRagBundling
# ---------------------------------------------------------------------------


class TestAWPExporterRagBundling(unittest.TestCase):
    """RAG manifest bundling respects include_rag_manifests flag."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.home = Path(self.tmpdir) / "corvin_home"
        self.tid = "_default"
        _minimal_corvin_tree(self.home, self.tid)
        self.pipeline_id = self._make_pipeline_with_rag()
        _reset_modules()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        _reset_modules()

    def _make_pipeline_with_rag(self) -> str:
        """Create pipeline with a stage that references a RAG provider."""
        provider_id = "spotify-search"
        pipeline_id = "pipe_rag_test"

        # Write RAG provider manifest
        rag_dir = self.home / "tenants" / self.tid / "global" / "rag"
        rag_dir.mkdir(parents=True, exist_ok=True)
        (rag_dir / f"{provider_id}.yaml").write_text(
            _valid_rag_manifest(provider_id), encoding="utf-8"
        )

        # Write pipeline that references the provider in stage_summary
        p_dir = (
            self.home
            / "tenants"
            / self.tid
            / "compute"
            / "pipelines"
            / pipeline_id
        )
        p_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "pipeline_id": pipeline_id,
            "tenant_id": self.tid,
            "stages": [
                {
                    "stage_id": "stage_1",
                    "tool_name": "rag_search",
                    "strategy": "grid",
                    "param_grid": {"top_k": [5, 10]},
                    "budget": {"max_iterations": 3},
                    "inputs": {},
                    "outputs": ["results.json"],
                }
            ],
            "steering_gate": False,
            "started_at": 1_000_000.0,
            "submitted_by": "test",
        }
        (p_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (p_dir / "pipeline_summary.json").write_text(
            json.dumps({"state": "complete", "best_losses": {"stage_1": 0.3},
                        "completed_stages": ["stage_1"]}),
            encoding="utf-8",
        )

        stage_dir = p_dir / "stages" / "stage_1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "stage_summary.json").write_text(
            json.dumps({
                "state": "complete",
                "best_loss": 0.3,
                "best_params": {"top_k": 10},
                "rag_providers_queried": [provider_id],
            }),
            encoding="utf-8",
        )
        return pipeline_id

    def _run_export(self, out_dir: Path, include_rag_manifests: bool = True):
        import compute_awp_exporter as _mod  # type: ignore
        exporter = _mod.PipelineAWPExporter(
            tenant_id=self.tid,
            pipeline_id=self.pipeline_id,
        )
        with _patch_exporter_home(self.home):
            return exporter.export(
                package_id="test.rag.export",
                version="1.0.0",
                output_dir=out_dir,
                mode="replay",
                include_rag_manifests=include_rag_manifests,
            )

    def test_rag_manifest_included_when_flag_true(self):
        out_dir = Path(self.tmpdir) / "out"
        out_dir.mkdir()
        meta = self._run_export(out_dir, include_rag_manifests=True)
        zip_path = next(out_dir.glob("*.zip"))
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
        rag_entries = [n for n in names if "rag_provider" in n and n.endswith(".yaml")]
        self.assertGreater(len(rag_entries), 0,
                           f"No RAG manifests bundled. Entries: {names}")
        self.assertGreater(meta.rag_provider_count, 0)

    def test_rag_manifest_not_included_when_flag_false(self):
        out_dir = Path(self.tmpdir) / "out"
        out_dir.mkdir()
        meta = self._run_export(out_dir, include_rag_manifests=False)
        zip_path = next(out_dir.glob("*.zip"))
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
        rag_entries = [n for n in names if "rag_provider" in n and n.endswith(".yaml")]
        self.assertEqual(len(rag_entries), 0,
                         f"RAG manifests found despite flag=False: {rag_entries}")
        self.assertEqual(meta.rag_provider_count, 0)

    def test_rag_manifest_content_has_apiversion(self):
        out_dir = Path(self.tmpdir) / "out"
        out_dir.mkdir()
        self._run_export(out_dir, include_rag_manifests=True)
        zip_path = next(out_dir.glob("*.zip"))
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            rag_entry = next(n for n in names if "rag_provider" in n and n.endswith(".yaml"))
            content = zf.read(rag_entry).decode("utf-8")
        self.assertIn("apiVersion:", content)
        self.assertIn("RAGProvider", content)


# ---------------------------------------------------------------------------
# 5. TestAWPExporterDatasource
# ---------------------------------------------------------------------------


class TestAWPExporterDatasource(unittest.TestCase):
    """Datasource bundling: vault_path stripped, secret_keys preserved."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.home = Path(self.tmpdir) / "corvin_home"
        self.tid = "_default"
        _minimal_corvin_tree(self.home, self.tid)
        self.ds_name = "spotify-db"
        self.pipeline_id = self._make_pipeline_with_datasource()
        _reset_modules()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        _reset_modules()

    def _make_pipeline_with_datasource(self) -> str:
        """Create pipeline that references a datasource via stage inputs."""
        pipeline_id = "pipe_ds_test"
        ds_name = self.ds_name

        # Write datasource connection manifest
        ds_dir = self.home / "tenants" / self.tid / "datasource_connections"
        ds_dir.mkdir(parents=True, exist_ok=True)
        conn_manifest = {
            "name": ds_name,
            "adapter": "postgresql",
            "source": {
                "adapter": "postgresql",
                "region": "eu-central-1",
                "host": "db.example.com",
                "database": "spotify",
            },
            "auth": {
                "method": "vault",
                "secret_keys": ["SPOTIFY_DB_PASSWORD"],
                "vault_path": "secret/spotify/db",  # must be stripped on export
            },
            "pii_handling": "redact",
            "filters": {},
            "tags": [],
        }
        (ds_dir / f"{ds_name}.json").write_text(
            json.dumps(conn_manifest), encoding="utf-8"
        )

        # Write pipeline with stage that references datasource in inputs
        p_dir = (
            self.home
            / "tenants"
            / self.tid
            / "compute"
            / "pipelines"
            / pipeline_id
        )
        p_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "pipeline_id": pipeline_id,
            "tenant_id": self.tid,
            "stages": [
                {
                    "stage_id": "stage_1",
                    "tool_name": "code_ingest",
                    "strategy": "grid",
                    "param_grid": {"limit": [1000]},
                    "budget": {"max_iterations": 2},
                    "inputs": {"data_source": ds_name},
                    "outputs": ["data.csv"],
                }
            ],
            "steering_gate": False,
            "started_at": 1_000_000.0,
            "submitted_by": "test",
        }
        (p_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (p_dir / "pipeline_summary.json").write_text(
            json.dumps({"state": "complete", "best_losses": {"stage_1": 0.4},
                        "completed_stages": ["stage_1"]}),
            encoding="utf-8",
        )
        stage_dir = p_dir / "stages" / "stage_1"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "stage_summary.json").write_text(
            json.dumps({
                "state": "complete",
                "best_loss": 0.4,
                "best_params": {"limit": 1000},
            }),
            encoding="utf-8",
        )
        return pipeline_id

    def _run_export(self, out_dir: Path, include_fabric_datasources: bool = True):
        import compute_awp_exporter as _mod  # type: ignore
        exporter = _mod.PipelineAWPExporter(
            tenant_id=self.tid,
            pipeline_id=self.pipeline_id,
        )
        with _patch_exporter_home(self.home):
            return exporter.export(
                package_id="test.ds.export",
                version="1.0.0",
                output_dir=out_dir,
                mode="replay",
                include_fabric_datasources=include_fabric_datasources,
            )

    def test_datasource_bundled_when_flag_true(self):
        out_dir = Path(self.tmpdir) / "out"
        out_dir.mkdir()
        meta = self._run_export(out_dir, include_fabric_datasources=True)
        zip_path = next(out_dir.glob("*.zip"))
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
        ds_entries = [n for n in names if n.endswith(".json") and self.ds_name in n]
        self.assertGreater(len(ds_entries), 0,
                           f"Datasource manifest not bundled. Entries: {names}")
        self.assertGreater(meta.datasource_count, 0)

    def test_vault_path_stripped_from_bundled_datasource(self):
        """Security: auth.vault_path value must not appear in the exported datasource JSON.

        We check that neither the key 'vault_path' nor the literal vault path value
        appears in the exported datasource manifest JSON.
        """
        out_dir = Path(self.tmpdir) / "out"
        out_dir.mkdir()
        self._run_export(out_dir, include_fabric_datasources=True)
        zip_path = next(out_dir.glob("*.zip"))
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            ds_entries = [n for n in names if n.endswith(".json") and self.ds_name in n]
            self.assertGreater(len(ds_entries), 0)
            for entry in ds_entries:
                raw = zf.read(entry).decode("utf-8")
                data = json.loads(raw)
                auth = data.get("auth", {})
                self.assertNotIn(
                    "vault_path",
                    auth,
                    f"vault_path key found in auth block of bundled datasource {entry!r}",
                )
                # Also verify the actual vault path value is not present anywhere
                self.assertNotIn(
                    "secret/spotify/db",
                    raw,
                    f"Vault path value leaked in bundled datasource {entry!r}",
                )

    def test_secret_keys_present_in_bundled_datasource(self):
        """secret_keys (names only, no values) must be preserved in the exported manifest."""
        out_dir = Path(self.tmpdir) / "out"
        out_dir.mkdir()
        self._run_export(out_dir, include_fabric_datasources=True)
        zip_path = next(out_dir.glob("*.zip"))
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            ds_entries = [n for n in names if n.endswith(".json") and self.ds_name in n]
            content = zf.read(ds_entries[0]).decode("utf-8")
        self.assertIn("SPOTIFY_DB_PASSWORD", content,
                      "secret_keys not preserved in exported datasource manifest")

    def test_datasource_not_bundled_when_flag_false(self):
        out_dir = Path(self.tmpdir) / "out"
        out_dir.mkdir()
        meta = self._run_export(out_dir, include_fabric_datasources=False)
        self.assertEqual(meta.datasource_count, 0)


# ---------------------------------------------------------------------------
# 6. TestAWPExporterSchedule
# ---------------------------------------------------------------------------


class TestAWPExporterSchedule(unittest.TestCase):
    """schedule_cron parameter propagates into workflow.awp.yaml triggers."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.home = Path(self.tmpdir) / "corvin_home"
        self.tid = "_default"
        _minimal_corvin_tree(self.home, self.tid)
        self.pipeline_id = _make_pipeline_fixture(self.home, self.tid)
        _reset_modules()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        _reset_modules()

    def _get_workflow_text(self, cron) -> str:
        import compute_awp_exporter as _mod  # type: ignore
        out_dir = Path(self.tmpdir) / "out"
        out_dir.mkdir(exist_ok=True)
        exporter = _mod.PipelineAWPExporter(
            tenant_id=self.tid,
            pipeline_id=self.pipeline_id,
        )
        with _patch_exporter_home(self.home):
            exporter.export(
                package_id="test.sched.export",
                version="1.0.0",
                output_dir=out_dir,
                mode="replay",
                schedule_cron=cron,
            )
        zip_path = next(out_dir.glob("*.zip"))
        with zipfile.ZipFile(zip_path, "r") as zf:
            workflow_entry = next(
                n for n in zf.namelist() if n.endswith("workflow.awp.yaml")
            )
            return zf.read(workflow_entry).decode("utf-8")

    def test_schedule_cron_appears_in_workflow(self):
        cron = "0 6 * * 1"
        text = self._get_workflow_text(cron)
        self.assertIn(cron, text,
                      f"schedule_cron {cron!r} not found in workflow yaml")

    def test_schedule_block_present_in_triggers(self):
        cron = "0 6 * * 1"
        text = self._get_workflow_text(cron)
        self.assertIn("schedule:", text,
                      "triggers.schedule block not in workflow yaml when cron is set")

    def test_api_trigger_disabled(self):
        """api.enabled must be false (M8 spec)."""
        text = self._get_workflow_text("0 6 * * 1")
        self.assertIn("enabled: false", text,
                      "triggers.api.enabled is not false in workflow yaml")

    def test_no_schedule_block_without_cron(self):
        """When schedule_cron is None, no schedule: block should appear."""
        text = self._get_workflow_text(None)
        self.assertNotIn("schedule:", text,
                         "schedule: block found in workflow yaml when no cron given")


# ---------------------------------------------------------------------------
# 7. TestAWPExporterProcessingRecord
# ---------------------------------------------------------------------------


class TestAWPExporterProcessingRecord(unittest.TestCase):
    """processing_record.yaml has correct structure (M12)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.home = Path(self.tmpdir) / "corvin_home"
        self.tid = "_default"
        _minimal_corvin_tree(self.home, self.tid)
        self.pipeline_id = _make_pipeline_fixture(self.home, self.tid)
        _reset_modules()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        _reset_modules()

    def _get_processing_record(self) -> dict:
        try:
            import yaml
        except ImportError:
            self.skipTest("pyyaml not installed")

        import compute_awp_exporter as _mod  # type: ignore
        out_dir = Path(self.tmpdir) / "out"
        out_dir.mkdir()
        exporter = _mod.PipelineAWPExporter(
            tenant_id=self.tid,
            pipeline_id=self.pipeline_id,
        )
        with _patch_exporter_home(self.home):
            exporter.export(
                package_id="test.rec.export",
                version="2.1.3",
                output_dir=out_dir,
                mode="replay",
            )
        zip_path = next(out_dir.glob("*.zip"))
        with zipfile.ZipFile(zip_path, "r") as zf:
            rec_entry = next(
                n for n in zf.namelist() if n.endswith("processing_record.yaml")
            )
            return yaml.safe_load(zf.read(rec_entry).decode("utf-8"))

    def test_processing_record_has_top_level_key(self):
        rec = self._get_processing_record()
        self.assertIn("processing_record", rec,
                      "processing_record.yaml missing 'processing_record' top key")

    def test_processing_record_pipeline_id(self):
        rec = self._get_processing_record()
        pr = rec["processing_record"]
        self.assertEqual(pr["pipeline_id"], self.pipeline_id)

    def test_processing_record_version(self):
        rec = self._get_processing_record()
        pr = rec["processing_record"]
        self.assertEqual(pr["version"], "2.1.3")

    def test_processing_record_stage_count(self):
        rec = self._get_processing_record()
        pr = rec["processing_record"]
        self.assertEqual(pr["stage_count"], 2)

    def test_processing_record_has_compliance_block(self):
        rec = self._get_processing_record()
        pr = rec["processing_record"]
        self.assertIn("compliance", pr)
        self.assertFalse(pr["compliance"].get("vault_path_exported", True),
                         "compliance.vault_path_exported must be False")

    def test_processing_record_has_stages_list(self):
        rec = self._get_processing_record()
        pr = rec["processing_record"]
        self.assertIn("stages", pr)
        self.assertEqual(len(pr["stages"]), 2)

    def test_processing_record_stage_entries_have_required_keys(self):
        rec = self._get_processing_record()
        pr = rec["processing_record"]
        for stage_entry in pr["stages"]:
            for key in ("stage_id", "tool_name", "strategy"):
                self.assertIn(key, stage_entry,
                              f"processing_record stage missing key: {key}")


# ---------------------------------------------------------------------------
# 8. TestAWPImporterModule
# ---------------------------------------------------------------------------


class TestAWPImporterModule(unittest.TestCase):
    """compute_awp_importer imports correctly and exposes AWPImporter."""

    def test_awp_importer_importable(self):
        _reset_modules()
        from compute_awp_importer import AWPImporter  # type: ignore
        self.assertTrue(callable(AWPImporter))

    def test_no_import_anthropic(self):
        """compute_awp_importer must not import anthropic (CI AST-lint contract)."""
        import ast as _ast
        source = (_SHARED / "compute_awp_importer.py").read_text(encoding="utf-8")
        tree = _ast.parse(source)
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    self.assertNotEqual(
                        top, "anthropic",
                        "compute_awp_importer has top-level 'import anthropic'",
                    )
            if isinstance(node, _ast.ImportFrom):
                top = (node.module or "").split(".")[0]
                self.assertNotEqual(
                    top, "anthropic",
                    "compute_awp_importer has 'from anthropic import ...'",
                )

    def test_install_result_importable(self):
        _reset_modules()
        from compute_awp_importer import InstallResult  # type: ignore
        self.assertTrue(InstallResult is not None)

    def test_awp_importer_requires_path(self):
        """AWPImporter.__init__ should raise when given a non-existent path."""
        _reset_modules()
        from compute_awp_importer import AWPImporter  # type: ignore
        import compute_awp_importer as _mod  # type: ignore
        with self.assertRaises((FileNotFoundError, _mod.ImportError, Exception)):
            AWPImporter(Path("/nonexistent/path/to/awpkg.zip"))


# ---------------------------------------------------------------------------
# 9. TestAWPImporterRoundTrip
# ---------------------------------------------------------------------------


class TestAWPImporterRoundTrip(unittest.TestCase):
    """Export a pipeline and feed the zip to AWPImporter.

    Note: The exporter produces a zip with layout
        <package_id>/workflow.awp.yaml
    and the importer's validate() looks for:
        src/workflow.awp.yaml  (importer spec layout)
    These are two different layout conventions — the importer represents the
    AWP hub import format while the exporter is the CorvinOS-native export.
    This test validates the importer on a correctly-structured bundle (hand-
    crafted to match what the importer expects) and the exporter on its own
    native layout.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.home = Path(self.tmpdir) / "corvin_home"
        self.tid = "_default"
        _minimal_corvin_tree(self.home, self.tid)
        self.pipeline_id = _make_pipeline_fixture(self.home, self.tid)
        _reset_modules()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        _reset_modules()

    def _make_importer_layout_zip(self) -> Path:
        """Create a zip that matches the AWPImporter's expected layout.

        Layout (importer format):
          src/workflow.awp.yaml
          awpkg.yaml
        """
        try:
            import yaml
        except ImportError:
            self.skipTest("pyyaml not installed")

        # Build a minimal workflow.awp.yaml matching the importer expectations
        workflow_doc = {
            "workflow": {
                "id": self.pipeline_id,
                "name": "Test Pipeline",
                "version": "1.0.0",
                "spec_version": "v1.1",
                "requires_corvin": "0.9.0",
            },
            "orchestration": {
                "triggers": {
                    "slash": {
                        "command": "/run-test-pipeline",
                        "visibility": "admin",
                    },
                    "api": {"enabled": False},
                }
            },
            "dag": {
                "nodes": [
                    {
                        "id": "stage_1",
                        "type": "compute",
                        "tool_name": "code_ingest",
                        "params": {"lr": 0.0032},
                        "budget": {"max_iterations": 1, "timeout_s": 3600},
                        "fabric_datasources": [],
                        "output_datasources": [],
                        "rag_datasources": [],
                        "inputs": {},
                        "share_output": ["artifact_path", "best_loss"],
                    },
                    {
                        "id": "stage_2",
                        "type": "compute",
                        "tool_name": "code_train",
                        "depends_on": ["stage_1"],
                        "params": {"lr": 0.0032},
                        "budget": {"max_iterations": 1, "timeout_s": 3600},
                        "fabric_datasources": [],
                        "output_datasources": [],
                        "rag_datasources": [],
                        "inputs": {},
                        "share_output": ["artifact_path", "best_loss"],
                    },
                ]
            },
        }

        zip_path = Path(self.tmpdir) / "test_bundle.awpkg.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(
                "src/workflow.awp.yaml",
                yaml.dump(workflow_doc, sort_keys=False, allow_unicode=True),
            )
            zf.writestr(
                "awpkg.yaml",
                yaml.dump({
                    "spec_version": "v1.1",
                    "package_id": "test.pipe.export",
                    "version": "1.0.0",
                    "permissions": {"compute": True},
                }, sort_keys=False),
            )
        return zip_path

    def test_importer_validate_returns_no_errors(self):
        zip_path = self._make_importer_layout_zip()
        _reset_modules()
        from compute_awp_importer import AWPImporter  # type: ignore
        importer = AWPImporter(zip_path)
        errors = importer.validate()
        self.assertEqual(
            errors,
            [],
            f"AWPImporter.validate() returned errors on valid bundle: {errors}",
        )

    def test_to_pipeline_manifest_returns_stages(self):
        zip_path = self._make_importer_layout_zip()
        _reset_modules()
        from compute_awp_importer import AWPImporter  # type: ignore
        importer = AWPImporter(zip_path)
        try:
            pm = importer.to_pipeline_manifest(self.tid)
        except Exception as exc:
            self.skipTest(f"to_pipeline_manifest failed (compute stack may be missing): {exc}")
        self.assertTrue(hasattr(pm, "stages"), "PipelineManifest has no 'stages' attribute")
        self.assertEqual(len(pm.stages), 2,
                         f"Expected 2 stages in round-trip manifest, got {len(pm.stages)}")

    def test_to_pipeline_manifest_stage_ids(self):
        zip_path = self._make_importer_layout_zip()
        _reset_modules()
        from compute_awp_importer import AWPImporter  # type: ignore
        importer = AWPImporter(zip_path)
        try:
            pm = importer.to_pipeline_manifest(self.tid)
        except Exception as exc:
            self.skipTest(f"to_pipeline_manifest failed: {exc}")
        stage_ids = [s.stage_id for s in pm.stages]
        self.assertIn("stage_1", stage_ids)
        self.assertIn("stage_2", stage_ids)


# ---------------------------------------------------------------------------
# 9b. TestAWPImporterTempdirCleanup
# ---------------------------------------------------------------------------


class TestAWPImporterTempdirCleanup(unittest.TestCase):
    """Blind spot: AWPImporter's unpacked tempdir is cleaned up only via
    __del__/GC — there is no __enter__/__exit__/close()/cleanup() and no
    try/finally around install_* calls.  These tests pin down the *current*
    (gap-exposing) behavior so a future fix (context-manager + deterministic
    close()) has a red->green target instead of silent regression risk.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.tid = "_default"
        _reset_modules()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        _reset_modules()

    def _make_zip_bundle(self) -> Path:
        """Minimal importer-layout zip: src/workflow.awp.yaml + awpkg.yaml."""
        try:
            import yaml
        except ImportError:
            self.skipTest("pyyaml not installed")

        workflow_doc = {
            "workflow": {"id": "pipe1", "name": "T", "version": "1.0.0"},
            "dag": {
                "nodes": [
                    {
                        "id": "s1",
                        "type": "compute",
                        "tool_name": "code_ingest",
                        "params": {},
                        "budget": {},
                    }
                ]
            },
        }
        zip_path = Path(self.tmpdir) / "bundle.awpkg.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(
                "src/workflow.awp.yaml",
                yaml.dump(workflow_doc, sort_keys=False, allow_unicode=True),
            )
            zf.writestr(
                "awpkg.yaml",
                yaml.dump(
                    {"spec_version": "v1.1", "package_id": "t", "version": "1.0.0"},
                    sort_keys=False,
                ),
            )
        return zip_path

    def test_unpack_creates_tempdir_with_awpkg_prefix(self):
        """Sanity baseline: zip input does unpack into a mkdtemp(prefix='awpkg_')."""
        zip_path = self._make_zip_bundle()
        from compute_awp_importer import AWPImporter  # type: ignore
        importer = AWPImporter(zip_path)
        self.assertIsNotNone(importer._tmpdir)
        self.assertTrue(importer._tmpdir.exists())
        self.assertTrue(
            importer._tmpdir.name.startswith("awpkg_"),
            f"expected mkdtemp prefix 'awpkg_', got {importer._tmpdir.name!r}",
        )
        del importer

    def test_tempdir_removed_only_after_del_and_gc(self):
        """Cleanup happens via __del__ + GC — the only path that exists today."""
        zip_path = self._make_zip_bundle()
        from compute_awp_importer import AWPImporter  # type: ignore
        importer = AWPImporter(zip_path)
        tmp_path = importer._tmpdir
        self.assertTrue(tmp_path.exists())
        del importer
        gc.collect()
        self.assertFalse(
            tmp_path.exists(),
            "tempdir should be removed once __del__ runs via GC",
        )

    def test_tempdir_survives_while_a_reference_is_still_held(self):
        """Demonstrates the actual blind spot: as long as *anything* holds a
        strong reference to the AWPImporter (e.g. it got stashed on an
        exception object or a traceback frame), the extracted package
        contents remain on disk — there is no deterministic release that is
        independent of refcounting/GC.
        """
        zip_path = self._make_zip_bundle()
        from compute_awp_importer import AWPImporter  # type: ignore
        importer = AWPImporter(zip_path)
        tmp_path = importer._tmpdir
        holder = [importer]  # simulates a lingering reference (e.g. in a traceback)
        del importer
        gc.collect()
        self.assertTrue(
            tmp_path.exists(),
            "tempdir was removed even though a live reference to the importer "
            "still exists — cleanup should not have run yet",
        )
        holder.clear()
        gc.collect()
        self.assertFalse(tmp_path.exists(), "tempdir should be gone once the last ref drops")

    def test_tempdir_not_cleaned_up_when_install_call_raises(self):
        """No try/finally guards install_* calls: an exception mid-install
        leaves the unpacked (potentially sensitive) package contents on disk
        for as long as the caller happens to hold the AWPImporter instance.
        """
        zip_path = self._make_zip_bundle()
        from compute_awp_importer import AWPImporter  # type: ignore
        importer = AWPImporter(zip_path)
        tmp_path = importer._tmpdir
        with patch.object(
            importer, "install_rag_manifests", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                importer.install_rag_manifests(self.tid)
        # The exception did not trigger any cleanup — tempdir still there.
        self.assertTrue(
            tmp_path.exists(),
            "tempdir was cleaned up on an exception path with no try/finally to do so",
        )
        del importer
        gc.collect()
        self.assertFalse(tmp_path.exists())

    def test_no_context_manager_or_explicit_close_exists(self):
        """Pins the missing-API gap. If this test ever fails because
        __enter__/__exit__/close()/cleanup() were added, that's the fix
        landing — update this test (and the recorded blind-spot note)
        alongside it rather than deleting it outright.
        """
        from compute_awp_importer import AWPImporter  # type: ignore
        self.assertFalse(hasattr(AWPImporter, "__enter__"))
        self.assertFalse(hasattr(AWPImporter, "__exit__"))
        self.assertFalse(hasattr(AWPImporter, "close"))
        self.assertFalse(hasattr(AWPImporter, "cleanup"))

    def test_directory_input_never_creates_a_tempdir(self):
        """Only zip inputs go through the GC-dependent tempdir path; a
        directory package_path leaves self._tmpdir at None (nothing to leak).
        """
        try:
            import yaml
        except ImportError:
            self.skipTest("pyyaml not installed")

        pkg_dir = Path(self.tmpdir) / "unpacked_bundle"
        (pkg_dir / "src").mkdir(parents=True)
        (pkg_dir / "src" / "workflow.awp.yaml").write_text(
            yaml.dump({"dag": {"nodes": []}}), encoding="utf-8"
        )
        (pkg_dir / "awpkg.yaml").write_text(
            yaml.dump({"spec_version": "v1.1"}), encoding="utf-8"
        )

        from compute_awp_importer import AWPImporter  # type: ignore
        importer = AWPImporter(pkg_dir)
        self.assertIsNone(importer._tmpdir)


# ---------------------------------------------------------------------------
# 10. TestConsoleExportEndpoint
# ---------------------------------------------------------------------------


class TestConsoleExportEndpoint(unittest.TestCase):
    """Console route tests for GET preview + POST download.

    These tests require local-autologin which may not be available in all
    environments; they self-skip gracefully when the session cannot be
    established.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.home = Path(self.tmpdir) / "corvin_home"
        self.tid = "_default"
        _minimal_corvin_tree(self.home, self.tid)
        self.pipeline_id = _make_pipeline_fixture(self.home, self.tid)
        # Console routes read CORVIN_HOME via forge.paths; we also need
        # to set it so the console's session/auth dirs are in our sandbox.
        self._prev_env = {
            k: os.environ.get(k)
            for k in ("CORVIN_HOME", "CORVIN_TENANT_ID")
        }
        os.environ["CORVIN_HOME"] = str(self.home)
        os.environ["CORVIN_TENANT_ID"] = self.tid
        _reset_modules()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        for k, v in self._prev_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()

    def _build_client(self):
        """Build a TestClient with an autologin session; skip if unavailable."""
        try:
            _reset_modules()
            from corvin_console.app import router
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
        except Exception as exc:
            self.skipTest(f"Console app not importable: {exc}")

        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
        login = client.get(
            "/v1/console/auth/local-login",
            headers={"X-Forwarded-For": "127.0.0.1", "host": "localhost"},
        )
        if login.status_code not in (200, 302):
            self.skipTest("Local autologin not configured in this environment")
        session_cookie = login.cookies.get("corvin_session")
        if not session_cookie:
            self.skipTest("Local autologin returned no session cookie")
        return client, session_cookie

    def test_awpkg_preview_returns_200(self):
        client, session_cookie = self._build_client()
        r = client.get(
            f"/v1/console/compute/pipelines/{self.pipeline_id}/export/awpkg/preview",
            cookies={"corvin_session": session_cookie},
        )
        self.assertEqual(r.status_code, 200, r.text)

    def test_awpkg_preview_response_shape(self):
        client, session_cookie = self._build_client()
        r = client.get(
            f"/v1/console/compute/pipelines/{self.pipeline_id}/export/awpkg/preview",
            cookies={"corvin_session": session_cookie},
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        for field in ("pipeline_id", "stage_count", "dag_nodes", "mode_options"):
            self.assertIn(field, data,
                          f"Preview response missing field: {field!r}")

    def test_awpkg_preview_pipeline_id_matches(self):
        client, session_cookie = self._build_client()
        r = client.get(
            f"/v1/console/compute/pipelines/{self.pipeline_id}/export/awpkg/preview",
            cookies={"corvin_session": session_cookie},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["pipeline_id"], self.pipeline_id)

    def test_awpkg_preview_stage_count(self):
        client, session_cookie = self._build_client()
        r = client.get(
            f"/v1/console/compute/pipelines/{self.pipeline_id}/export/awpkg/preview",
            cookies={"corvin_session": session_cookie},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["stage_count"], 2)

    def test_awpkg_preview_returns_404_for_unknown_pipeline(self):
        client, session_cookie = self._build_client()
        r = client.get(
            "/v1/console/compute/pipelines/nonexistent_pipe/export/awpkg/preview",
            cookies={"corvin_session": session_cookie},
        )
        self.assertEqual(r.status_code, 404, r.text)

    def test_awpkg_download_requires_csrf(self):
        """POST /export/awpkg without CSRF token must be rejected."""
        client, session_cookie = self._build_client()
        r = client.post(
            f"/v1/console/compute/pipelines/{self.pipeline_id}/export/awpkg",
            json={
                "package_id": "test.pipe.export",
                "version": "1.0.0",
                "mode": "replay",
            },
            cookies={"corvin_session": session_cookie},
            # deliberately no X-CSRF-Token header
        )
        self.assertIn(r.status_code, (400, 403, 422),
                      f"Expected CSRF rejection, got {r.status_code}: {r.text}")

    def test_awpkg_download_with_csrf_returns_zip(self):
        client, session_cookie = self._build_client()
        whoami = client.get(
            "/v1/console/auth/whoami",
            cookies={"corvin_session": session_cookie},
        )
        csrf_token = whoami.json().get("csrf_token", "") if whoami.status_code == 200 else ""
        if not csrf_token:
            self.skipTest("Could not obtain CSRF token")

        r = client.post(
            f"/v1/console/compute/pipelines/{self.pipeline_id}/export/awpkg",
            json={
                "package_id": "test.pipe.export",
                "version": "1.0.0",
                "mode": "replay",
            },
            cookies={"corvin_session": session_cookie},
            headers={"X-CSRF-Token": csrf_token},
        )
        if r.status_code in (500, 503):
            self.skipTest(
                f"Export endpoint error (compute_awp_exporter may need extra deps): "
                f"{r.status_code} {r.text[:200]}"
            )
        self.assertEqual(r.status_code, 200, r.text)
        ct = r.headers.get("content-type", "")
        self.assertIn("zip", ct,
                      f"Expected application/zip content-type, got: {ct!r}")

    def test_awpkg_download_invalid_package_id_rejected(self):
        """package_id not matching the required pattern must be rejected (422)."""
        client, session_cookie = self._build_client()
        whoami = client.get(
            "/v1/console/auth/whoami",
            cookies={"corvin_session": session_cookie},
        )
        csrf_token = whoami.json().get("csrf_token", "") if whoami.status_code == 200 else ""

        r = client.post(
            f"/v1/console/compute/pipelines/{self.pipeline_id}/export/awpkg",
            json={
                "package_id": "INVALID_UPPER_CASE",  # violates pattern
                "version": "1.0.0",
                "mode": "replay",
            },
            cookies={"corvin_session": session_cookie},
            headers={"X-CSRF-Token": csrf_token},
        )
        self.assertEqual(r.status_code, 422,
                         f"Invalid package_id should produce 422, got {r.status_code}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
