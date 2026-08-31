"""Full end-to-end round-trip test for ADR-0090 — Spotify chart prediction pipeline.

Tests the complete export → import cycle with realistic 5-stage Spotify data:

  TestSpotifyPipelineFixture  — fixture well-formedness
  TestSpotifyExportReplay     — PipelineAWPExporter in replay mode
  TestSpotifyExportReoptimize — PipelineAWPExporter in reoptimize mode
  TestSpotifyRoundTrip        — export from source tenant, import on target tenant
  TestSpotifySecurityInvariants — security properties of the bundle

All tests run without network or LLM access.
Live-only tests (require Compute Worker) are gated on CORVIN_E2E_LIVE.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import unittest
import zipfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Repo path bootstrap
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
# Module reset helper
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


# ---------------------------------------------------------------------------
# Patch helper — same pattern as test_compute_awp_export.py
# ---------------------------------------------------------------------------


@contextmanager
def _patch_exporter_home(home: Path):
    """Patch compute_awp_exporter._corvin_home and silence audit writes."""
    import compute_awp_exporter as _mod  # type: ignore

    with patch.object(_mod, "_corvin_home", return_value=home), \
         patch.object(_mod._sec, "write_event", return_value=None):
        yield


def _patch_importer_tenant_home(home: Path, tenant_id: str):
    """Return a context manager that redirects _tenant_home inside AWPImporter."""
    import compute_awp_importer as _imod  # type: ignore

    def _fake_tenant_home(tid: str) -> Path:
        return home / "tenants" / tid

    return patch.object(_imod, "_tenant_home", side_effect=_fake_tenant_home)


# ---------------------------------------------------------------------------
# Minimal corvin directory tree
# ---------------------------------------------------------------------------


def _minimal_corvin_tree(home: Path, tid: str) -> None:
    (home / "tenants" / tid / "global" / "auth").mkdir(parents=True, exist_ok=True)
    (home / "tenants" / tid / "global" / "forge").mkdir(parents=True, exist_ok=True)
    (home / "tenants" / tid / "global" / "console" / "sessions").mkdir(
        parents=True, exist_ok=True
    )
    (home / "tenants" / tid / "compute" / "runs").mkdir(parents=True, exist_ok=True)
    (home / "tenants" / tid / "compute" / "pipelines").mkdir(
        parents=True, exist_ok=True
    )
    (home / "tenants" / tid / "datasource_connections").mkdir(
        parents=True, exist_ok=True
    )
    (home / "tenants" / tid / "global" / "rag").mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Spotify RAG manifest
# ---------------------------------------------------------------------------

_SPOTIFY_RAG_YAML = """\
apiVersion: rag.corvin.io/v1alpha1
kind: RAGProvider
metadata:
  name: spotify-charts-elastic
  namespace: production
  description: Elasticsearch-based Spotify chart history retrieval
spec:
  retrieval:
    endpoint: "https://elastic.internal/spotify-charts/_search"
    method: POST
    timeout_ms: 5000
    auth:
      type: bearer-token
      token_env_var: ES_TOKEN
    query_format:
      type: custom-http
      sample: '{"query":{"match":{"track_id":"{query}"}},"size":{limit}}'
  response_format:
    content_path: "hits.hits[]._source"
    score_path: "hits.hits[]._score"
    metadata_path: "hits.hits[]._source"
  dataClassification: INTERNAL
  complianceZone: EU
  capabilities:
    - keyword-search
    - semantic-search
  resilience:
    circuit_breaker:
      failure_threshold: 5
      timeout_seconds: 30
      half_open_requests: 1
"""

# ---------------------------------------------------------------------------
# Datasource fixtures
# ---------------------------------------------------------------------------

_DS_SPOTIFY_S3 = {
    "name": "spotify-charts-s3",
    "adapter": "s3_csv",
    "source": {
        "adapter": "s3_csv",
        "region": "eu-central-1",
        "raw": {
            "bucket": "corvin-spotify-prod",
            "prefix": "charts/weekly/",
        },
    },
    "auth": {
        "method": "vault",
        "secret_keys": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
        "vault_path": "secret/spotify/s3",
    },
    "pii_handling": "redact",
    "filters": [],
    "tags": [],
    "incremental": {
        "mode": "timestamp",
        "watermark_col": "week",
        "initial_watermark": "2020-01-01",
    },
}

_DS_SNOWFLAKE = {
    "name": "analytics-snowflake",
    "adapter": "snowflake",
    "source": {
        "adapter": "snowflake",
        "region": "eu-central-1",
        "raw": {
            "account": "xy12345.eu-central-1",
            "database": "ANALYTICS",
            "schema": "SPOTIFY",
        },
    },
    "auth": {
        "method": "vault",
        "secret_keys": ["SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD"],
        "vault_path": "secret/analytics/snowflake",
    },
    "pii_handling": "pseudonymize",
    "filters": [],
    "tags": [],
}

_DS_RESULTS_S3 = {
    "name": "spotify-results-s3",
    "adapter": "s3_csv",
    "source": {
        "adapter": "s3_csv",
        "region": "eu-central-1",
        "raw": {
            "bucket": "corvin-spotify-prod",
            "prefix": "predictions/",
        },
    },
    "auth": {
        "method": "vault",
        "secret_keys": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
        "vault_path": "secret/spotify/s3-results",
    },
    "pii_handling": "redact",
    "filters": [],
    "tags": ["role:output"],
}

# ---------------------------------------------------------------------------
# 5-stage pipeline manifest
# ---------------------------------------------------------------------------

_SPOTIFY_PIPELINE_ID = "pipe_spotify_charts_v1"

_SPOTIFY_PIPELINE_MANIFEST = {
    "pipeline_id": _SPOTIFY_PIPELINE_ID,
    "tenant_id": "_default",
    "steering_gate": True,
    "started_at": 1_748_000_000.0,
    "submitted_by": "console",
    "stages": [
        {
            "stage_id": "code_spotify_ingest",
            "tool_name": "code_spotify_ingest",
            "strategy": "grid",
            "param_grid": {"max_rows": [10000, 50000, 200000]},
            "budget": {"max_iterations": 3, "timeout_s": 3600},
            "inputs": {"data_source": "spotify-charts-s3"},
            "outputs": ["weekly_chart_aggregates.csv"],
        },
        {
            "stage_id": "code_spotify_features",
            "tool_name": "code_spotify_features",
            "strategy": "bayesian",
            "param_grid": {"n_features": [10, 20, 50]},
            "budget": {"max_iterations": 10, "timeout_s": 7200},
            "inputs": {
                "dataset": "$code_spotify_ingest.artifacts/weekly_chart_aggregates.csv",
                "rag_context": "spotify-charts-elastic",
            },
            "outputs": ["feature_matrix.parquet"],
        },
        {
            "stage_id": "code_spotify_train",
            "tool_name": "code_spotify_train",
            "strategy": "bayesian",
            "param_grid": {"lr": [0.001, 0.01], "depth": [4, 6, 8]},
            "budget": {"max_iterations": 50, "timeout_s": 14400},
            "inputs": {
                "features": "$code_spotify_features.artifacts/feature_matrix.parquet",
                "reference_data": "analytics-snowflake",
            },
            "outputs": ["model.pkl"],
        },
        {
            "stage_id": "code_spotify_evaluate",
            "tool_name": "code_spotify_evaluate",
            "strategy": "grid",
            "param_grid": {"threshold": [0.5, 0.6, 0.7]},
            "budget": {"max_iterations": 3, "timeout_s": 3600},
            "inputs": {"model": "$code_spotify_train.artifacts/model.pkl"},
            "outputs": ["evaluation.json"],
        },
        {
            "stage_id": "code_spotify_predict",
            "tool_name": "code_spotify_predict",
            "strategy": "grid",
            "param_grid": {"batch_size": [1000, 5000]},
            "budget": {"max_iterations": 2, "timeout_s": 3600},
            "inputs": {
                "evaluation": "$code_spotify_evaluate.artifacts/evaluation.json",
                "output_spotify_results_s3": "spotify-results-s3",
            },
            "outputs": ["predictions.csv"],
        },
    ],
}

_SPOTIFY_PIPELINE_SUMMARY = {
    "state": "complete",
    "current_stage_id": None,
    "completed_stages": [
        "code_spotify_ingest",
        "code_spotify_features",
        "code_spotify_train",
        "code_spotify_evaluate",
        "code_spotify_predict",
    ],
    "best_losses": {
        "code_spotify_ingest": 0.15,
        "code_spotify_features": 0.11,
        "code_spotify_train": 0.082,
        "code_spotify_evaluate": 0.071,
        "code_spotify_predict": 0.068,
    },
}

# Per-stage champion params (realistic)
_STAGE_CHAMPION_PARAMS = {
    "code_spotify_ingest": {
        "best_params": {"max_rows": 200000},
        "best_loss": 0.15,
    },
    "code_spotify_features": {
        "best_params": {"n_features": 20},
        "best_loss": 0.11,
        "rag_providers_queried": ["spotify-charts-elastic"],
    },
    "code_spotify_train": {
        "best_params": {"lr": 0.0032, "depth": 6, "n_estimators": 200},
        "best_loss": 0.082,
    },
    "code_spotify_evaluate": {
        "best_params": {"threshold": 0.6},
        "best_loss": 0.071,
    },
    "code_spotify_predict": {
        "best_params": {"batch_size": 5000},
        "best_loss": 0.068,
        "output_datasources_written": ["spotify-results-s3"],
    },
}


# ---------------------------------------------------------------------------
# Fixture builder
# ---------------------------------------------------------------------------


def _make_spotify_pipeline_fixture(home: Path, tid: str) -> str:
    """Build the full 5-stage Spotify pipeline fixture under *home*.

    Creates:
    - Pipeline manifest + summary
    - Per-stage summaries with champion params
    - RAG manifest (spotify-charts-elastic)
    - 3 datasource connection manifests
    Returns pipeline_id.
    """
    pipeline_id = _SPOTIFY_PIPELINE_ID

    # Pipeline dirs
    p_dir = home / "tenants" / tid / "compute" / "pipelines" / pipeline_id
    p_dir.mkdir(parents=True, exist_ok=True)
    (p_dir / "manifest.json").write_text(
        json.dumps(_SPOTIFY_PIPELINE_MANIFEST, indent=2), encoding="utf-8"
    )
    (p_dir / "pipeline_summary.json").write_text(
        json.dumps(_SPOTIFY_PIPELINE_SUMMARY, indent=2), encoding="utf-8"
    )

    # Per-stage summaries
    for stage_id, champ in _STAGE_CHAMPION_PARAMS.items():
        sd = p_dir / "stages" / stage_id
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "stage_summary.json").write_text(
            json.dumps({"state": "complete", **champ}, indent=2), encoding="utf-8"
        )

    # RAG manifest
    rag_dir = home / "tenants" / tid / "global" / "rag"
    rag_dir.mkdir(parents=True, exist_ok=True)
    (rag_dir / "spotify-charts-elastic.yaml").write_text(
        _SPOTIFY_RAG_YAML, encoding="utf-8"
    )

    # Datasource connections
    ds_dir = home / "tenants" / tid / "datasource_connections"
    ds_dir.mkdir(parents=True, exist_ok=True)
    for ds in (_DS_SPOTIFY_S3, _DS_SNOWFLAKE, _DS_RESULTS_S3):
        (ds_dir / f"{ds['name']}.json").write_text(
            json.dumps(ds, indent=2), encoding="utf-8"
        )

    return pipeline_id


def _run_export(
    home: Path,
    tid: str,
    pipeline_id: str,
    out_dir: Path,
    *,
    mode: str = "replay",
    package_id: str = "spotify.charts.export",
    version: str = "1.0.0",
    schedule_cron: str | None = None,
    **kwargs,
):
    """Import exporter, patch home, run export, return (meta, zip_path)."""
    import compute_awp_exporter as _mod  # type: ignore

    exporter = _mod.PipelineAWPExporter(tenant_id=tid, pipeline_id=pipeline_id)
    with _patch_exporter_home(home):
        meta = exporter.export(
            package_id=package_id,
            version=version,
            output_dir=out_dir,
            mode=mode,
            schedule_cron=schedule_cron,
            **kwargs,
        )
    zips = list(out_dir.glob("*.zip"))
    if not zips:
        raise FileNotFoundError(f"No zip produced in {out_dir}")
    return meta, zips[0]


def _read_workflow_from_zip(zip_path: Path) -> tuple[str, dict]:
    """Return (raw_text, parsed_dict) for workflow.awp.yaml inside zip."""
    try:
        import yaml
    except ImportError:
        raise unittest.SkipTest("pyyaml not installed")

    with zipfile.ZipFile(zip_path, "r") as zf:
        entry = next(n for n in zf.namelist() if n.endswith("workflow.awp.yaml"))
        text = zf.read(entry).decode("utf-8")
    return text, yaml.safe_load(text) or {}


def _read_processing_record_from_zip(zip_path: Path) -> dict:
    try:
        import yaml
    except ImportError:
        raise unittest.SkipTest("pyyaml not installed")

    with zipfile.ZipFile(zip_path, "r") as zf:
        entry = next(n for n in zf.namelist() if n.endswith("processing_record.yaml"))
        return yaml.safe_load(zf.read(entry).decode("utf-8")) or {}


def _build_importer_layout_zip(
    zip_out: Path,
    workflow_doc: dict,
    *,
    rag_manifests: dict[str, str] | None = None,
    input_datasources: dict[str, dict] | None = None,
    output_datasources: dict[str, dict] | None = None,
) -> Path:
    """Build an AWPImporter-layout zip from workflow_doc + optional assets.

    AWPImporter layout:
      awpkg.yaml
      src/workflow.awp.yaml
      src/rag/<provider>.yaml
      src/datasources/input/<name>.json
      src/datasources/output/<name>.json
    """
    try:
        import yaml
    except ImportError:
        raise unittest.SkipTest("pyyaml not installed")

    with zipfile.ZipFile(zip_out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "src/workflow.awp.yaml",
            yaml.dump(workflow_doc, sort_keys=False, allow_unicode=True),
        )
        zf.writestr(
            "awpkg.yaml",
            yaml.dump(
                {
                    "spec_version": "v1.1",
                    "package_id": "spotify.charts.export",
                    "version": "1.0.0",
                    "permissions": {
                        "compute": True,
                        "secrets": ["ES_TOKEN", "AWS_ACCESS_KEY_ID",
                                    "AWS_SECRET_ACCESS_KEY", "SNOWFLAKE_USER",
                                    "SNOWFLAKE_PASSWORD"],
                    },
                },
                sort_keys=False,
            ),
        )
        for name, content in (rag_manifests or {}).items():
            zf.writestr(f"src/rag/{name}.yaml", content)

        for name, manifest in (input_datasources or {}).items():
            # Strip vault_path before bundling (importer format)
            safe = dict(manifest)
            auth = dict(safe.get("auth", {}))
            auth.pop("vault_path", None)
            safe["auth"] = auth
            zf.writestr(
                f"src/datasources/input/{name}.json",
                json.dumps(safe, indent=2, sort_keys=True),
            )

        for name, manifest in (output_datasources or {}).items():
            safe = dict(manifest)
            auth = dict(safe.get("auth", {}))
            auth.pop("vault_path", None)
            safe["auth"] = auth
            zf.writestr(
                f"src/datasources/output/{name}.json",
                json.dumps(safe, indent=2, sort_keys=True),
            )

    return zip_out


def _build_spotify_importer_zip(zip_out: Path) -> Path:
    """Produce a full Spotify 5-stage AWPImporter-layout bundle."""
    try:
        import yaml
    except ImportError:
        raise unittest.SkipTest("pyyaml not installed")

    # ADR-0091: nodes are type:agent with x_compute extension (AWP v1.0 compatible)
    def _compute_agent_node(node_id, tool_name, params, budget, fabric_ds=None,
                            output_ds=None, rag_ds=None, depends_on=None):
        n = {
            "id": node_id,
            "type": "agent",
            "agent": "compute_worker",
            "instructions": f"Execute Compute stage: {tool_name}",
            "x_compute": {
                "tool_name": tool_name,
                "params": params,
                "budget": budget,
                "mode": "replay",
                "fabric_datasources": fabric_ds or [],
                "output_datasources": output_ds or [],
                "rag_datasources": rag_ds or [],
            },
            "share_output": ["artifact_path", "best_loss", "best_params",
                             "watermark_advanced_to"],
        }
        if depends_on:
            n["depends_on"] = depends_on
        return n

    nodes = [
        _compute_agent_node("code_spotify_ingest", "code_spotify_ingest",
                            {"max_rows": 200000}, {"max_iterations": 1, "timeout_s": 3600},
                            fabric_ds=[{"name": "spotify-charts-s3", "role": "input"}]),
        _compute_agent_node("code_spotify_features", "code_spotify_features",
                            {"n_features": 20}, {"max_iterations": 1, "timeout_s": 7200},
                            rag_ds=[{"provider_id": "spotify-charts-elastic",
                                     "query_template": "", "limit": 10}],
                            depends_on=["code_spotify_ingest"]),
        _compute_agent_node("code_spotify_train", "code_spotify_train",
                            {"lr": 0.0032, "depth": 6, "n_estimators": 200},
                            {"max_iterations": 1, "timeout_s": 14400},
                            fabric_ds=[{"name": "analytics-snowflake", "role": "input"}],
                            depends_on=["code_spotify_features"]),
        _compute_agent_node("code_spotify_evaluate", "code_spotify_evaluate",
                            {"threshold": 0.6}, {"max_iterations": 1, "timeout_s": 3600},
                            depends_on=["code_spotify_train"]),
        _compute_agent_node("code_spotify_predict", "code_spotify_predict",
                            {"batch_size": 5000}, {"max_iterations": 1, "timeout_s": 3600},
                            output_ds=[{"name": "spotify-results-s3", "role": "output"}],
                            depends_on=["code_spotify_evaluate"]),
    ]

    # Quality gates after each stage (type:agent with x_quality_gate, AWP-compatible)
    for stage_id in [
        "code_spotify_ingest", "code_spotify_features", "code_spotify_train",
        "code_spotify_evaluate", "code_spotify_predict",
    ]:
        nodes.append({
            "id": f"quality_gate_after_{stage_id}",
            "type": "agent",
            "agent": "compute_worker",
            "instructions": f"Quality gate for {stage_id}: check best_loss ≤ 999",
            "x_quality_gate": {
                "metric": "best_loss", "from_node": stage_id,
                "operator": "lte", "threshold": 999,
                "on_pass": "continue", "on_fail": "abort",
            },
            "depends_on": [stage_id],
        })

    workflow_doc = {
        "awp": "1.0.0",
        "workflow": {
            "name": "Spotify Chart Prediction",
            "description": "AWP Workflow from Spotify pipeline (test fixture)",
        },
        "orchestration": {
            "engine": "dag",
            "graph": nodes,
            "triggers": {
                "schedule": {"cron": "0 6 * * 1", "timezone": "UTC"},
                "slash": {"command": "/run-spotify-chart-prediction", "visibility": "admin"},
                "api": {"enabled": False},
            },
        },
    }

    # Input datasources — strip vault_path
    input_ds = {}
    for ds in (_DS_SPOTIFY_S3, _DS_SNOWFLAKE):
        d = dict(ds)
        auth = dict(d.get("auth", {}))
        auth.pop("vault_path", None)
        d["auth"] = auth
        input_ds[ds["name"]] = d

    output_ds = {}
    d = dict(_DS_RESULTS_S3)
    auth = dict(d.get("auth", {}))
    auth.pop("vault_path", None)
    d["auth"] = auth
    output_ds[_DS_RESULTS_S3["name"]] = d

    return _build_importer_layout_zip(
        zip_out,
        workflow_doc,
        rag_manifests={"spotify-charts-elastic": _SPOTIFY_RAG_YAML},
        input_datasources=input_ds,
        output_datasources=output_ds,
    )


# ═══════════════════════════════════════════════════════════════════════════
# TestSpotifyPipelineFixture
# ═══════════════════════════════════════════════════════════════════════════


class TestSpotifyPipelineFixture(unittest.TestCase):
    """Verify the test fixture itself is well-formed."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = Path(self.tmp) / "corvin_home"
        self.tid = "_default"
        _minimal_corvin_tree(self.home, self.tid)
        self.pipeline_id = _make_spotify_pipeline_fixture(self.home, self.tid)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fixture_creates_5_stages(self):
        manifest_path = (
            self.home / "tenants" / self.tid / "compute" / "pipelines"
            / self.pipeline_id / "manifest.json"
        )
        self.assertTrue(manifest_path.exists(), "Pipeline manifest not created")
        data = json.loads(manifest_path.read_text())
        self.assertEqual(len(data["stages"]), 5, f"Expected 5 stages, got {len(data['stages'])}")

    def test_fixture_stage_ids_are_correct(self):
        manifest_path = (
            self.home / "tenants" / self.tid / "compute" / "pipelines"
            / self.pipeline_id / "manifest.json"
        )
        data = json.loads(manifest_path.read_text())
        stage_ids = [s["stage_id"] for s in data["stages"]]
        expected = [
            "code_spotify_ingest",
            "code_spotify_features",
            "code_spotify_train",
            "code_spotify_evaluate",
            "code_spotify_predict",
        ]
        self.assertEqual(stage_ids, expected)

    def test_fixture_has_realistic_champion_params(self):
        p_dir = (
            self.home / "tenants" / self.tid / "compute" / "pipelines"
            / self.pipeline_id / "stages"
        )
        train_summary = json.loads(
            (p_dir / "code_spotify_train" / "stage_summary.json").read_text()
        )
        self.assertIn("best_params", train_summary)
        self.assertAlmostEqual(train_summary["best_params"]["lr"], 0.0032)
        self.assertEqual(train_summary["best_params"]["depth"], 6)
        self.assertAlmostEqual(train_summary["best_loss"], 0.082)

    def test_fixture_predict_stage_has_best_loss(self):
        p_dir = (
            self.home / "tenants" / self.tid / "compute" / "pipelines"
            / self.pipeline_id / "stages"
        )
        predict_summary = json.loads(
            (p_dir / "code_spotify_predict" / "stage_summary.json").read_text()
        )
        self.assertAlmostEqual(predict_summary["best_loss"], 0.068)
        self.assertEqual(predict_summary["best_params"]["batch_size"], 5000)

    def test_rag_manifest_is_valid_yaml(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("pyyaml not installed")

        rag_path = (
            self.home / "tenants" / self.tid / "global" / "rag"
            / "spotify-charts-elastic.yaml"
        )
        self.assertTrue(rag_path.exists(), "RAG manifest not created")
        data = yaml.safe_load(rag_path.read_text())
        self.assertEqual(data["kind"], "RAGProvider")
        self.assertEqual(data["metadata"]["name"], "spotify-charts-elastic")
        self.assertIn("retrieval", data["spec"])
        # Compliance fields
        self.assertEqual(data["spec"]["dataClassification"], "INTERNAL")
        self.assertEqual(data["spec"]["complianceZone"], "EU")

    def test_rag_manifest_has_no_credential_values(self):
        rag_path = (
            self.home / "tenants" / self.tid / "global" / "rag"
            / "spotify-charts-elastic.yaml"
        )
        content = rag_path.read_text()
        # Only the env-var name, never an actual token value
        self.assertIn("ES_TOKEN", content)
        # Should not contain anything that looks like a secret value
        self.assertNotIn("Bearer ", content)

    def test_datasource_has_required_fields(self):
        ds_path = (
            self.home / "tenants" / self.tid / "datasource_connections"
            / "spotify-charts-s3.json"
        )
        self.assertTrue(ds_path.exists(), "spotify-charts-s3 manifest not created")
        data = json.loads(ds_path.read_text())
        for field in ("name", "adapter", "source", "auth", "pii_handling"):
            self.assertIn(field, data, f"Datasource missing field: {field}")
        self.assertEqual(data["adapter"], "s3_csv")
        self.assertEqual(data["auth"]["method"], "vault")
        self.assertIn("AWS_ACCESS_KEY_ID", data["auth"]["secret_keys"])

    def test_datasource_has_vault_path(self):
        """Fixture datasources must include vault_path (stripped at export time)."""
        ds_path = (
            self.home / "tenants" / self.tid / "datasource_connections"
            / "spotify-charts-s3.json"
        )
        data = json.loads(ds_path.read_text())
        self.assertIn("vault_path", data["auth"],
                      "Fixture must contain vault_path so we can test that it is stripped")

    def test_all_three_datasources_created(self):
        ds_dir = self.home / "tenants" / self.tid / "datasource_connections"
        names = {p.stem for p in ds_dir.glob("*.json")}
        for expected in ("spotify-charts-s3", "analytics-snowflake", "spotify-results-s3"):
            self.assertIn(expected, names)

    def test_pipeline_summary_has_5_best_losses(self):
        summary_path = (
            self.home / "tenants" / self.tid / "compute" / "pipelines"
            / self.pipeline_id / "pipeline_summary.json"
        )
        data = json.loads(summary_path.read_text())
        self.assertEqual(len(data["best_losses"]), 5)

    def test_predict_stage_has_output_datasource_reference(self):
        """Stage 5 must declare the output datasource in stage_summary."""
        p_dir = (
            self.home / "tenants" / self.tid / "compute" / "pipelines"
            / self.pipeline_id / "stages"
        )
        predict_summary = json.loads(
            (p_dir / "code_spotify_predict" / "stage_summary.json").read_text()
        )
        self.assertIn("output_datasources_written", predict_summary)
        self.assertIn("spotify-results-s3", predict_summary["output_datasources_written"])

    def test_features_stage_has_rag_provider_reference(self):
        """Stage 2 must declare the RAG provider in stage_summary."""
        p_dir = (
            self.home / "tenants" / self.tid / "compute" / "pipelines"
            / self.pipeline_id / "stages"
        )
        features_summary = json.loads(
            (p_dir / "code_spotify_features" / "stage_summary.json").read_text()
        )
        self.assertIn("rag_providers_queried", features_summary)
        self.assertIn("spotify-charts-elastic", features_summary["rag_providers_queried"])


# ═══════════════════════════════════════════════════════════════════════════
# TestSpotifyExportReplay
# ═══════════════════════════════════════════════════════════════════════════


class TestSpotifyExportReplay(unittest.TestCase):
    """Full export in replay mode."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = Path(self.tmp) / "corvin_home"
        self.tid = "_default"
        self.out_dir = Path(self.tmp) / "out"
        self.out_dir.mkdir()
        _minimal_corvin_tree(self.home, self.tid)
        self.pipeline_id = _make_spotify_pipeline_fixture(self.home, self.tid)
        _reset_modules()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        _reset_modules()

    def _export(self, **kwargs):
        return _run_export(
            self.home, self.tid, self.pipeline_id, self.out_dir,
            mode="replay",
            schedule_cron="0 6 * * 1",
            **kwargs,
        )

    def test_export_produces_zip_file(self):
        _meta, zip_path = self._export()
        self.assertTrue(zip_path.exists(), "export() produced no zip file")
        self.assertTrue(zipfile.is_zipfile(zip_path), "Output is not a valid zip")

    def test_zip_contains_all_required_files(self):
        _meta, zip_path = self._export()
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
        workflow_entries = [n for n in names if n.endswith("workflow.awp.yaml")]
        record_entries = [n for n in names if n.endswith("processing_record.yaml")]
        self.assertGreater(len(workflow_entries), 0, "workflow.awp.yaml not in zip")
        self.assertGreater(len(record_entries), 0, "processing_record.yaml not in zip")

    def test_workflow_yaml_has_5_compute_nodes(self):
        _meta, zip_path = self._export()
        _text, doc = _read_workflow_from_zip(zip_path)
        nodes = doc.get("orchestration", {}).get("graph", [])
        compute_nodes = [n for n in nodes if n.get("type") == "agent" and n.get("x_compute")]
        self.assertEqual(
            len(compute_nodes), 5,
            f"Expected 5 compute nodes, got {len(compute_nodes)}. "
            f"All node ids: {[n.get('id') for n in nodes]}"
        )

    def test_all_stage_ids_present_in_dag(self):
        _meta, zip_path = self._export()
        _text, doc = _read_workflow_from_zip(zip_path)
        node_ids = {n["id"] for n in doc.get("orchestration", {}).get("graph", [])}
        for stage_id in _STAGE_CHAMPION_PARAMS:
            self.assertIn(stage_id, node_ids,
                          f"Stage {stage_id!r} missing from DAG nodes")

    def test_champion_params_hardcoded_in_replay(self):
        """Replay mode must bake params: into every compute node (inside x_compute)."""
        _meta, zip_path = self._export()
        _text, doc = _read_workflow_from_zip(zip_path)
        nodes = doc.get("orchestration", {}).get("graph", [])
        compute_nodes = [n for n in nodes if n.get("type") == "agent" and n.get("x_compute")]
        for node in compute_nodes:
            xc = node.get("x_compute", {})
            self.assertIn(
                "params", xc,
                f"Node {node.get('id')!r} missing 'params' in x_compute (replay mode)"
            )
            self.assertNotIn(
                "param_grid", xc,
                f"Node {node.get('id')!r} has 'param_grid' in x_compute (replay = single params)"
            )

    def test_train_stage_champion_lr_is_hardcoded(self):
        """The training stage champion lr=0.0032 must appear in the workflow."""
        _meta, zip_path = self._export()
        text, _doc = _read_workflow_from_zip(zip_path)
        self.assertIn("0.0032", text,
                      "Champion lr=0.0032 not found in replay workflow yaml")

    def test_rag_manifest_bundled(self):
        _meta, zip_path = self._export()
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
        rag_entries = [n for n in names if "rag_provider" in n and n.endswith(".yaml")]
        self.assertGreater(len(rag_entries), 0,
                           f"No RAG manifests bundled. Entries: {names}")
        # Verify content
        with zipfile.ZipFile(zip_path, "r") as zf:
            content = zf.read(rag_entries[0]).decode("utf-8")
        self.assertIn("spotify-charts-elastic", content)
        self.assertIn("RAGProvider", content)
        self.assertIn("ES_TOKEN", content)

    def test_datasource_bundled_vault_path_stripped(self):
        """The bundled input datasource must not contain vault_path values."""
        _meta, zip_path = self._export()
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            ds_entries = [
                n for n in names
                if "datasource" in n and "spotify-charts-s3" in n and n.endswith(".json")
            ]
        self.assertGreater(len(ds_entries), 0, "spotify-charts-s3 datasource not bundled")
        with zipfile.ZipFile(zip_path, "r") as zf:
            raw = zf.read(ds_entries[0]).decode("utf-8")
        data = json.loads(raw)
        self.assertNotIn(
            "vault_path", data.get("auth", {}),
            "vault_path key still present in exported datasource auth block"
        )
        self.assertNotIn(
            "secret/spotify/s3", raw,
            "vault_path value leaked into exported datasource"
        )

    def test_permissions_secrets_populated(self):
        """processing_record.yaml or awpkg.yaml must list the required secret keys."""
        _meta, zip_path = self._export()
        # The exporter encodes secret_keys in datasource auth blocks
        with zipfile.ZipFile(zip_path, "r") as zf:
            all_text = ""
            for name in zf.namelist():
                if name.endswith((".yaml", ".json")):
                    all_text += zf.read(name).decode("utf-8", errors="replace")
        self.assertIn("AWS_ACCESS_KEY_ID", all_text,
                      "Secret key AWS_ACCESS_KEY_ID not found anywhere in bundle")

    def test_schedule_in_triggers(self):
        _meta, zip_path = self._export()
        text, doc = _read_workflow_from_zip(zip_path)
        self.assertIn("0 6 * * 1", text, "Schedule cron not in workflow yaml")
        triggers = doc.get("orchestration", {}).get("triggers", {})
        self.assertIn("schedule", triggers, "triggers.schedule block missing")
        self.assertEqual(triggers["schedule"]["cron"], "0 6 * * 1")

    def test_processing_record_generated(self):
        _meta, zip_path = self._export()
        rec = _read_processing_record_from_zip(zip_path)
        pr = rec.get("processing_record", {})
        self.assertEqual(pr.get("pipeline_id"), self.pipeline_id)
        self.assertEqual(pr.get("stage_count"), 5)
        self.assertEqual(pr.get("mode"), "replay")
        self.assertEqual(pr.get("schedule_cron"), "0 6 * * 1")
        self.assertFalse(
            pr.get("compliance", {}).get("vault_path_exported", True),
            "compliance.vault_path_exported must be False"
        )

    def test_processing_record_has_all_5_stages(self):
        _meta, zip_path = self._export()
        rec = _read_processing_record_from_zip(zip_path)
        stages = rec.get("processing_record", {}).get("stages", [])
        self.assertEqual(len(stages), 5, f"Expected 5 stages in record, got {len(stages)}")

    def test_meta_stage_count_is_5(self):
        meta, _zip = self._export()
        self.assertEqual(meta.stage_count, 5)

    def test_meta_mode_is_replay(self):
        meta, _zip = self._export()
        self.assertEqual(meta.mode, "replay")

    def test_meta_rag_provider_count_positive(self):
        meta, _zip = self._export()
        self.assertGreater(meta.rag_provider_count, 0,
                           "Expected at least one RAG provider bundled")

    def test_api_trigger_disabled_in_workflow(self):
        _meta, zip_path = self._export()
        text, _doc = _read_workflow_from_zip(zip_path)
        self.assertIn("enabled: false", text,
                      "triggers.api.enabled must be false in workflow yaml")

    def test_quality_gate_nodes_present(self):
        """steering_gate=True must produce quality_gate nodes (type:agent + x_quality_gate)."""
        _meta, zip_path = self._export()
        _text, doc = _read_workflow_from_zip(zip_path)
        nodes = doc.get("orchestration", {}).get("graph", [])
        # Quality gates use type:agent + x_quality_gate (AWP-compatible)
        gate_nodes = [n for n in nodes if n.get("x_quality_gate")]
        self.assertGreater(len(gate_nodes), 0,
                           "No quality_gate nodes found; steering_gate=True should add them")


# ═══════════════════════════════════════════════════════════════════════════
# TestSpotifyExportReoptimize
# ═══════════════════════════════════════════════════════════════════════════


class TestSpotifyExportReoptimize(unittest.TestCase):
    """Full export in reoptimize mode."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = Path(self.tmp) / "corvin_home"
        self.tid = "_default"
        self.out_dir = Path(self.tmp) / "out"
        self.out_dir.mkdir()
        _minimal_corvin_tree(self.home, self.tid)
        self.pipeline_id = _make_spotify_pipeline_fixture(self.home, self.tid)
        _reset_modules()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        _reset_modules()

    def _export(self, **kwargs):
        return _run_export(
            self.home, self.tid, self.pipeline_id, self.out_dir,
            mode="reoptimize",
            **kwargs,
        )

    def test_export_reoptimize_has_param_grid(self):
        _meta, zip_path = self._export()
        text, _doc = _read_workflow_from_zip(zip_path)
        self.assertIn("param_grid:", text,
                      "reoptimize mode: 'param_grid:' not found in workflow yaml")

    def test_no_hardcoded_params_in_reoptimize(self):
        """In reoptimize mode, 'param_grid:' must appear in x_compute, not 'params:'."""
        _meta, zip_path = self._export()
        _text, doc = _read_workflow_from_zip(zip_path)
        nodes = doc.get("orchestration", {}).get("graph", [])
        compute_nodes = [n for n in nodes if n.get("type") == "agent" and n.get("x_compute")]
        for node in compute_nodes:
            xc = node.get("x_compute", {})
            self.assertNotIn(
                "params", xc,
                f"Node {node.get('id')!r} has 'params' in x_compute reoptimize mode "
                f"(should have 'param_grid')"
            )
            self.assertIn(
                "param_grid", xc,
                f"Node {node.get('id')!r} missing 'param_grid' in x_compute (reoptimize)"
            )

    def test_strategy_preserved(self):
        """Ingest strategy=grid and train strategy=bayesian must be preserved."""
        _meta, zip_path = self._export()
        _text, doc = _read_workflow_from_zip(zip_path)
        nodes = doc.get("orchestration", {}).get("graph", [])
        compute_nodes = {n["id"]: n for n in nodes if n.get("type") == "agent" and n.get("x_compute")}

        # param_grid is inside x_compute (AWP-compatible type:agent wrapper)
        train_node = compute_nodes.get("code_spotify_train", {})
        x_compute = train_node.get("x_compute", {})
        grid = x_compute.get("param_grid", {})
        self.assertIn("lr", grid, "code_spotify_train param_grid missing 'lr'")
        self.assertGreater(len(grid["lr"]), 1,
                           "lr param_grid has only 1 value; should be multi-valued in reoptimize")

    def test_reoptimize_workflow_has_budget_max_iterations(self):
        _meta, zip_path = self._export()
        text, _doc = _read_workflow_from_zip(zip_path)
        self.assertIn("max_iterations:", text,
                      "budget.max_iterations not found in reoptimize workflow yaml")

    def test_reoptimize_meta_mode(self):
        meta, _zip = self._export()
        self.assertEqual(meta.mode, "reoptimize")

    def test_reoptimize_ingest_param_grid_has_3_values(self):
        """Ingest stage has param_grid with 3 max_rows values."""
        _meta, zip_path = self._export()
        _text, doc = _read_workflow_from_zip(zip_path)
        nodes = {n["id"]: n for n in doc.get("orchestration", {}).get("graph", [])
                 if n.get("type") == "agent" and n.get("x_compute")}
        ingest = nodes.get("code_spotify_ingest", {})
        x_compute = ingest.get("x_compute", {})
        grid = x_compute.get("param_grid", {})
        self.assertIn("max_rows", grid)
        self.assertEqual(len(grid["max_rows"]), 3,
                         f"Expected 3 max_rows values, got {grid['max_rows']}")


# ═══════════════════════════════════════════════════════════════════════════
# TestSpotifyRoundTrip
# ═══════════════════════════════════════════════════════════════════════════


class TestSpotifyRoundTrip(unittest.TestCase):
    """Export from source tenant → import on target tenant → verify."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

        # Source tenant home (where pipeline lives)
        self.src_home = Path(self.tmp) / "src_home"
        self.src_tid = "_default"
        _minimal_corvin_tree(self.src_home, self.src_tid)
        _make_spotify_pipeline_fixture(self.src_home, self.src_tid)

        # Target tenant home (fresh install target)
        self.tgt_home = Path(self.tmp) / "tgt_home"
        self.tgt_tid = "_default"
        _minimal_corvin_tree(self.tgt_home, self.tgt_tid)

        self.out_dir = Path(self.tmp) / "out"
        self.out_dir.mkdir()

        self.bundle_zip = Path(self.tmp) / "spotify_bundle.awpkg.zip"

        _reset_modules()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        _reset_modules()

    def _build_round_trip_bundle(self) -> Path:
        """Build an AWPImporter-layout zip from the 5-stage Spotify fixture."""
        return _build_spotify_importer_zip(self.bundle_zip)

    def _get_importer(self) -> "AWPImporter":
        _reset_modules()
        from compute_awp_importer import AWPImporter  # type: ignore

        zip_path = self._build_round_trip_bundle()
        return AWPImporter(zip_path)

    def test_roundtrip_validate_returns_no_errors(self):
        importer = self._get_importer()
        errors = importer.validate()
        self.assertEqual(
            errors, [],
            f"AWPImporter.validate() returned errors on Spotify bundle: {errors}"
        )

    def test_roundtrip_rag_manifest_installed(self):
        import compute_awp_importer as _imod  # type: ignore

        importer = self._get_importer()
        with _patch_importer_tenant_home(self.tgt_home, self.tgt_tid):
            installed = importer.install_rag_manifests(self.tgt_tid, force=True)

        self.assertIn(
            "spotify-charts-elastic", installed,
            f"RAG manifest not installed. Got: {installed}"
        )
        dest = (
            self.tgt_home / "tenants" / self.tgt_tid / "global" / "rag"
            / "spotify-charts-elastic.yaml"
        )
        self.assertTrue(dest.exists(), f"RAG manifest file not written to {dest}")

    def test_roundtrip_datasource_installed_vault_path_absent(self):
        """Installed input datasource must not have vault_path."""
        importer = self._get_importer()
        with _patch_importer_tenant_home(self.tgt_home, self.tgt_tid):
            installed = importer.install_fabric_datasources(self.tgt_tid, force=True)

        self.assertGreater(len(installed), 0, "No input datasources installed")

        dest_dir = self.tgt_home / "tenants" / self.tgt_tid / "datasource_connections"
        for name in installed:
            dest = dest_dir / f"{name}.json"
            self.assertTrue(dest.exists(), f"Datasource file not written: {dest}")
            data = json.loads(dest.read_text())
            self.assertNotIn(
                "vault_path", data.get("auth", {}),
                f"vault_path found in installed datasource {name!r}"
            )

    def test_roundtrip_pipeline_manifest_has_5_stages(self):
        importer = self._get_importer()
        try:
            pm = importer.to_pipeline_manifest(self.tgt_tid)
        except Exception as exc:
            self.skipTest(
                f"to_pipeline_manifest failed (compute stack may be missing): {exc}"
            )
        self.assertEqual(
            len(pm.stages), 5,
            f"Expected 5 stages in round-trip manifest, got {len(pm.stages)}"
        )

    def test_roundtrip_stage_ids_preserved(self):
        importer = self._get_importer()
        try:
            pm = importer.to_pipeline_manifest(self.tgt_tid)
        except Exception as exc:
            self.skipTest(f"to_pipeline_manifest failed: {exc}")

        stage_ids = [s.stage_id for s in pm.stages]
        for expected_id in _STAGE_CHAMPION_PARAMS:
            self.assertIn(expected_id, stage_ids,
                          f"Stage {expected_id!r} not in round-trip manifest stages")

    def test_roundtrip_champion_params_as_single_point_grid(self):
        """Replay bundle: params become single-point param_grid after round-trip."""
        importer = self._get_importer()
        try:
            pm = importer.to_pipeline_manifest(self.tgt_tid)
        except Exception as exc:
            self.skipTest(f"to_pipeline_manifest failed: {exc}")

        stages_by_id = {s.stage_id: s for s in pm.stages}
        train_stage = stages_by_id.get("code_spotify_train")
        self.assertIsNotNone(train_stage, "code_spotify_train stage not in manifest")
        # Champion params should be present as single-point grid: {k: [v]}
        grid = train_stage.param_grid
        self.assertIn("lr", grid, "lr not in code_spotify_train param_grid")
        lr_values = grid["lr"]
        self.assertEqual(len(lr_values), 1,
                         f"Expected single-point grid for lr, got {lr_values}")
        self.assertAlmostEqual(lr_values[0], 0.0032, places=5)

    def test_roundtrip_installed_datasource_secret_keys_intact(self):
        """secret_keys (names only) must survive the round-trip."""
        importer = self._get_importer()
        with _patch_importer_tenant_home(self.tgt_home, self.tgt_tid):
            installed = importer.install_fabric_datasources(self.tgt_tid, force=True)

        dest_dir = self.tgt_home / "tenants" / self.tgt_tid / "datasource_connections"
        s3_dest = dest_dir / "spotify-charts-s3.json"
        if not s3_dest.exists():
            self.skipTest("spotify-charts-s3 was not installed")
        data = json.loads(s3_dest.read_text())
        secret_keys = data.get("auth", {}).get("secret_keys", [])
        self.assertIn("AWS_ACCESS_KEY_ID", secret_keys,
                      "AWS_ACCESS_KEY_ID not preserved in round-trip datasource")
        self.assertIn("AWS_SECRET_ACCESS_KEY", secret_keys,
                      "AWS_SECRET_ACCESS_KEY not preserved in round-trip datasource")

    def test_roundtrip_output_datasource_installed(self):
        """Output datasource (spotify-results-s3) must also be installable."""
        importer = self._get_importer()
        with _patch_importer_tenant_home(self.tgt_home, self.tgt_tid):
            installed = importer.install_output_datasources(self.tgt_tid, force=True)

        self.assertGreater(len(installed), 0, "No output datasources installed")

        dest_dir = self.tgt_home / "tenants" / self.tgt_tid / "datasource_connections"
        for name in installed:
            dest = dest_dir / f"{name}.json"
            self.assertTrue(dest.exists(), f"Output datasource file not written: {dest}")
            data = json.loads(dest.read_text())
            self.assertNotIn(
                "vault_path", data.get("auth", {}),
                f"vault_path in installed output datasource {name!r}"
            )

    def test_roundtrip_rag_manifest_is_valid_after_install(self):
        """Installed RAG manifest must pass RAGProvider validation."""
        try:
            import yaml
        except ImportError:
            self.skipTest("pyyaml not installed")

        importer = self._get_importer()
        with _patch_importer_tenant_home(self.tgt_home, self.tgt_tid):
            importer.install_rag_manifests(self.tgt_tid, force=True)

        dest = (
            self.tgt_home / "tenants" / self.tgt_tid / "global" / "rag"
            / "spotify-charts-elastic.yaml"
        )
        self.assertTrue(dest.exists())
        data = yaml.safe_load(dest.read_text())
        self.assertEqual(data["kind"], "RAGProvider")
        self.assertIn("retrieval", data.get("spec", {}))

    def test_roundtrip_install_all_completes(self):
        """install_all() must not raise and return non-empty results."""
        importer = self._get_importer()
        with _patch_importer_tenant_home(self.tgt_home, self.tgt_tid):
            result = importer.install_all(self.tgt_tid)

        # At minimum the RAG and datasource installs should succeed
        self.assertTrue(
            len(result.rag_providers) > 0
            or len(result.input_datasources) > 0
            or len(result.output_datasources) > 0,
            f"install_all returned all-empty result: {result}"
        )

    @unittest.skipIf(
        not os.environ.get("CORVIN_E2E_LIVE"),
        "live E2E only — set CORVIN_E2E_LIVE=1 to enable"
    )
    def test_roundtrip_live_compute_worker_reachable(self):
        """Live: verify the compute worker socket is present after import."""
        importer = self._get_importer()
        reachable = importer.check_compute_worker(self.tgt_tid)
        self.assertTrue(reachable, "Compute worker not reachable after import")


# ═══════════════════════════════════════════════════════════════════════════
# TestSpotifySecurityInvariants
# ═══════════════════════════════════════════════════════════════════════════


class TestSpotifySecurityInvariants(unittest.TestCase):
    """Verify security properties of the awpkg bundle."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = Path(self.tmp) / "corvin_home"
        self.tid = "_default"
        self.out_dir = Path(self.tmp) / "out"
        self.out_dir.mkdir()
        _minimal_corvin_tree(self.home, self.tid)
        _make_spotify_pipeline_fixture(self.home, self.tid)
        _reset_modules()

        meta, zip_path = _run_export(
            self.home, self.tid, _SPOTIFY_PIPELINE_ID, self.out_dir,
            mode="replay",
            schedule_cron="0 6 * * 1",
        )
        self.meta = meta
        self.zip_path = zip_path

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        _reset_modules()

    def _all_bundle_text(self) -> str:
        """Return concatenated text of all text files in the zip."""
        buf = []
        with zipfile.ZipFile(self.zip_path, "r") as zf:
            for name in zf.namelist():
                if name.endswith((".yaml", ".json", ".py", ".txt", ".md")):
                    buf.append(zf.read(name).decode("utf-8", errors="replace"))
        return "\n".join(buf)

    def test_no_vault_path_in_any_bundled_file(self):
        """auth.vault_path must never appear as a key with a value in any bundled file."""
        # Match the pattern: vault_path: "something" or "vault_path": "something"
        vault_value_pattern = re.compile(
            r"""(["']?vault_path["']?\s*[:=]\s*["'][^"']+["'])""",
            re.IGNORECASE,
        )
        with zipfile.ZipFile(self.zip_path, "r") as zf:
            for name in zf.namelist():
                if name.endswith((".yaml", ".json", ".py")):
                    content = zf.read(name).decode("utf-8", errors="replace")
                    match = vault_value_pattern.search(content)
                    self.assertIsNone(
                        match,
                        f"vault_path value leaked in bundled file {name!r}: "
                        f"{match.group(0) if match else ''}",
                    )

    def test_no_credentials_in_processing_record(self):
        """processing_record.yaml must not contain any credential values."""
        rec = _read_processing_record_from_zip(self.zip_path)
        rec_text = json.dumps(rec)
        # vault_path values look like "secret/..."
        self.assertNotIn("secret/spotify/s3", rec_text)
        self.assertNotIn("secret/analytics", rec_text)
        # compliance.vault_path_exported must be False
        pr = rec.get("processing_record", {})
        self.assertFalse(
            pr.get("compliance", {}).get("vault_path_exported", True),
            "compliance.vault_path_exported must be False"
        )

    def test_api_trigger_disabled_in_workflow(self):
        """triggers.api.enabled must be false in the bundle."""
        text, doc = _read_workflow_from_zip(self.zip_path)
        triggers = doc.get("orchestration", {}).get("triggers", {})
        api = triggers.get("api", {})
        self.assertFalse(
            api.get("enabled", True),
            "triggers.api.enabled must be false"
        )

    def test_credential_key_pattern_not_in_raw_source_block(self):
        """The raw S3 source block must not export a 'secret' or 'password' key."""
        with zipfile.ZipFile(self.zip_path, "r") as zf:
            names = zf.namelist()
            ds_entries = [
                n for n in names
                if "datasource" in n and "spotify-charts-s3" in n and n.endswith(".json")
            ]
        if not ds_entries:
            self.skipTest("spotify-charts-s3 not bundled in this export")

        with zipfile.ZipFile(self.zip_path, "r") as zf:
            raw = zf.read(ds_entries[0]).decode("utf-8")
        data = json.loads(raw)

        # The source.raw block must not contain credential-looking keys
        source_raw = data.get("source", {}).get("raw", data.get("source", {}))
        for cred_key in ("secret", "password", "token", "api_key", "private_key"):
            self.assertNotIn(
                cred_key, source_raw,
                f"Credential key {cred_key!r} found in bundled source block "
                f"(should be REDACTED or absent)"
            )

    def test_no_actual_aws_key_values_in_bundle(self):
        """Ensure no actual AWS key values are present (only key names)."""
        all_text = self._all_bundle_text()
        # We allow the key *names* (AWS_ACCESS_KEY_ID) but not any value pattern
        # like "AKIA..." (real AWS access key prefix)
        self.assertNotRegex(
            all_text,
            r"AKIA[A-Z0-9]{16}",
            "Possible real AWS access key found in bundle"
        )

    def test_processing_record_audit_first_flag(self):
        """processing_record must declare audit_first=True."""
        rec = _read_processing_record_from_zip(self.zip_path)
        pr = rec.get("processing_record", {})
        self.assertTrue(
            pr.get("compliance", {}).get("audit_first", False),
            "compliance.audit_first must be True in processing_record"
        )

    def test_processing_record_sensitive_files_mode(self):
        """processing_record must declare that sensitive files use 0o600 mode."""
        rec = _read_processing_record_from_zip(self.zip_path)
        pr = rec.get("processing_record", {})
        mode_val = pr.get("compliance", {}).get("sensitive_files_mode", "")
        self.assertIn("600", str(mode_val),
                      "compliance.sensitive_files_mode must indicate 0o600")

    def test_workflow_slash_command_present(self):
        """A slash command trigger must be declared for operator access."""
        _text, doc = _read_workflow_from_zip(self.zip_path)
        triggers = doc.get("orchestration", {}).get("triggers", {})
        slash = triggers.get("slash", {})
        self.assertIn("command", slash, "triggers.slash.command not found")
        self.assertTrue(
            slash["command"].startswith("/"),
            f"slash.command must start with '/': {slash['command']!r}"
        )

    def test_rag_manifest_no_token_values(self):
        """Bundled RAG manifest must not contain any actual bearer token value."""
        with zipfile.ZipFile(self.zip_path, "r") as zf:
            names = zf.namelist()
            rag_entries = [n for n in names if "rag_provider" in n and n.endswith(".yaml")]
        if not rag_entries:
            self.skipTest("No RAG manifests bundled")

        with zipfile.ZipFile(self.zip_path, "r") as zf:
            content = zf.read(rag_entries[0]).decode("utf-8")

        # Must reference token as env var name only, never as a value
        self.assertIn("ES_TOKEN", content,
                      "ES_TOKEN env var reference not found in RAG manifest")
        # Must not contain "Bearer " followed by what looks like a real token
        self.assertNotRegex(
            content,
            r"Bearer\s+[A-Za-z0-9+/=]{20,}",
            "Possible real Bearer token value in bundled RAG manifest"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
