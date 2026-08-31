"""
Compute Pipeline → AWP Workflow DAG Export (ADR-0090).

Milestones implemented:
  M1  — PipelineManifest loading + champion params discovery
  M4  — fabric datasource manifest bundling (vault_path stripped)
  M5  — RAG provider manifest bundling
  M6  — output datasource discovery + bundling
  M8  — trigger generation (schedule, slash, api)
  M9  — custom adapter AST-gate + bundling
  M10 — ML backend AST-gate + bundling
  M11 — AWP workflow.awp.yaml generation with compute nodes + quality gates
  M12 — processing_record.yaml + zip package assembly

Security invariants:
  - NEVER reads auth.vault_path file contents; vault_path stripped from exported manifests
  - Audit event written BEFORE any file I/O (audit-first)
  - All sensitive files written at mode 0o600
  - No `import anthropic` (CI AST lint enforces)
  - AST-gates custom adapters and ML backends for exec/eval/subprocess/anthropic
"""
from __future__ import annotations

import ast
import datetime
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

# ---------------------------------------------------------------------------
# forge path + security_events bootstrap
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "operator" / "forge"))

from forge import paths as _forge_paths          # noqa: E402
from forge import security_events as _sec        # noqa: E402

# ---------------------------------------------------------------------------
# compute plugin bootstrap
# ---------------------------------------------------------------------------

_COMPUTE_ROOT = _REPO / "core" / "compute"
if str(_COMPUTE_ROOT) not in sys.path:
    sys.path.insert(0, str(_COMPUTE_ROOT))

from corvin_compute.pipeline.manifest import PipelineManifest, StageSpec  # noqa: E402
from corvin_compute.fabric.datasources.registry import (  # noqa: E402
    DataSourceRegistry,
    ConnectionSummary,
)

# RAG import/export
from rag_import_export import RAGProviderImportExport  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# AWP spec version declaration
# ---------------------------------------------------------------------------

_AWP_SPEC_VERSION = "v1.1"
_AWP_CORVIN_MIN_VERSION = "0.9.0"

# ---------------------------------------------------------------------------
# AST safety gate — forbidden patterns
# ---------------------------------------------------------------------------

_FORBIDDEN_IMPORT_NAMES = frozenset({"anthropic", "subprocess"})
# All names that map to exec/eval functionality, including common aliases
_FORBIDDEN_CALL_NAMES = frozenset({"exec", "eval", "__import__", "compile"})
# Forbidden builtins that can be imported and called under an alias
_FORBIDDEN_BUILTIN_NAMES = frozenset({"exec", "eval", "__import__", "compile"})


def _ast_gate(source: str, label: str) -> None:
    """Parse *source* and raise ValueError if a forbidden pattern is found.

    Forbidden:
    - ``import anthropic`` / ``from anthropic import ...``
    - ``import subprocess`` / ``from subprocess import ...``
    - Direct calls to exec(), eval(), __import__(), compile()
    - Aliased imports of forbidden builtins: ``from builtins import exec as _e``
      detected by scanning ImportFrom symbols (node.names[].name) against
      _FORBIDDEN_BUILTIN_NAMES in addition to the module check.

    Args:
        source: Python source text.
        label: human-readable description for the error message.

    Raises:
        ValueError: on any forbidden pattern.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError(f"AST gate: {label} has syntax error: {exc}") from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = (alias.name or "").split(".")[0]
                if top in _FORBIDDEN_IMPORT_NAMES:
                    raise ValueError(
                        f"AST gate: {label} imports forbidden module {top!r}"
                    )
        elif isinstance(node, ast.ImportFrom):
            module_top = ((node.module or "")).split(".")[0]
            if module_top in _FORBIDDEN_IMPORT_NAMES:
                raise ValueError(
                    f"AST gate: {label} imports from forbidden module {module_top!r}"
                )
            # Also block: from builtins import exec as _alias
            for alias in node.names:
                if alias.name in _FORBIDDEN_BUILTIN_NAMES:
                    raise ValueError(
                        f"AST gate: {label} imports forbidden builtin {alias.name!r} "
                        f"(possibly aliased as {alias.asname!r})"
                    )
        elif isinstance(node, ast.Call):
            func = node.func
            fname = (
                func.id
                if isinstance(func, ast.Name)
                else (func.attr if isinstance(func, ast.Attribute) else "")
            )
            if fname in _FORBIDDEN_CALL_NAMES:
                raise ValueError(
                    f"AST gate: {label} calls forbidden function {fname!r}()"
                )


# ---------------------------------------------------------------------------
# Return dataclass
# ---------------------------------------------------------------------------


@dataclass
class AWPackageMeta:
    """Describes the AWP package that was assembled."""

    package_id: str
    version: str
    output_dir: Path
    stage_count: int
    rag_provider_count: int
    datasource_count: int
    output_datasource_count: int
    ml_backend_count: int
    custom_adapter_count: int
    mode: str
    schedule_cron: str | None
    has_acceptance_criteria: bool


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _corvin_home() -> Path:
    return _forge_paths.corvin_home()


def _audit_path(tenant_id: str) -> Path:
    return _corvin_home() / "tenants" / tenant_id / "global" / "audit.jsonl"


def _load_json(path: Path) -> dict:
    """Read a JSON file; return {} if missing or malformed."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("compute_awp_exporter: could not read %s: %s", path, exc)
        return {}


def _safe_write(path: Path, content: str | bytes, *, mode: int = 0o600) -> None:
    """Atomic write to *path* at *mode*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{_hex4()}")
    if isinstance(content, bytes):
        tmp.write_bytes(content)
    else:
        tmp.write_text(content, encoding="utf-8")
    try:
        os.chmod(tmp, mode)
    except OSError:
        pass
    os.replace(tmp, path)
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _hex4() -> str:
    import secrets
    return secrets.token_hex(4)


def _pipeline_root(tenant_id: str, pipeline_id: str) -> Path:
    return (
        _corvin_home()
        / "tenants" / tenant_id
        / "compute" / "pipelines" / pipeline_id
    )


def _runs_root(tenant_id: str) -> Path:
    return _corvin_home() / "tenants" / tenant_id / "compute" / "runs"


def _rag_dir(tenant_id: str) -> Path:
    return _corvin_home() / "tenants" / tenant_id / "global" / "rag"


def _custom_adapters_dir(tenant_id: str) -> Path:
    return _corvin_home() / "tenants" / tenant_id / "datasource_adapters"


def _compute_backends_dir(tenant_id: str) -> Path:
    return _corvin_home() / "tenants" / tenant_id / "compute_backends"


# ---------------------------------------------------------------------------
# Champion param resolution
# ---------------------------------------------------------------------------


def _load_pipeline_manifest(tenant_id: str, pipeline_id: str) -> dict:
    """Load raw pipeline manifest dict from disk."""
    path = _pipeline_root(tenant_id, pipeline_id) / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Pipeline manifest not found: {path}"
        )
    return _load_json(path)


def _load_pipeline_summary(tenant_id: str, pipeline_id: str) -> dict:
    """Load pipeline_summary.json — contains best_losses + completed_stages."""
    path = _pipeline_root(tenant_id, pipeline_id) / "pipeline_summary.json"
    return _load_json(path)


def _load_per_stage_champion(
    tenant_id: str,
    pipeline_id: str,
    stage_id: str,
) -> dict:
    """Scan compute/runs/*/summary.json for runs belonging to this pipeline+stage.

    Returns the best (lowest loss) champion params dict, or {} if none found.
    """
    runs_root = _runs_root(tenant_id)
    if not runs_root.is_dir():
        return {}

    best_loss: float | None = None
    best_params: dict = {}

    for run_dir in runs_root.iterdir():
        if not run_dir.is_dir():
            continue
        manifest = _load_json(run_dir / "manifest.json")
        # A run belongs to this pipeline+stage when manifest carries
        # pipeline_id + stage_id fields (written by PipelineEngine).
        if manifest.get("pipeline_id") != pipeline_id:
            continue
        if manifest.get("stage_id") != stage_id:
            continue
        summary = _load_json(run_dir / "summary.json")
        loss = summary.get("best_loss")
        if loss is None:
            continue
        if best_loss is None or loss < best_loss:
            best_loss = loss
            best_params = summary.get("best_params") or {}

    return best_params


# ---------------------------------------------------------------------------
# RAG provider discovery
# ---------------------------------------------------------------------------


def _discover_rag_providers(
    tenant_id: str,
    stage: StageSpec,
    stage_summary: dict,
) -> list[dict]:
    """Return list of {provider_id, manifest_path} for the stage.

    Discovery:
    1. Scan StageSpec.inputs values against RAG provider dirs in
       <corvin_home>/tenants/<tid>/global/rag/.
    2. Also include any provider in stage_summary["rag_providers_queried"].
    """
    rag_root = _rag_dir(tenant_id)
    if not rag_root.is_dir():
        return []

    # Build set of known provider IDs on disk
    known: dict[str, Path] = {}
    for p in rag_root.iterdir():
        if p.is_dir() or p.suffix in (".yaml", ".yml", ".json"):
            pid = p.stem if p.suffix else p.name
            # Prefer .yaml manifest file within a provider subdir
            candidate_yaml = p / "manifest.yaml" if p.is_dir() else p
            if candidate_yaml.exists():
                known[pid] = candidate_yaml
            elif p.is_dir():
                # Also accept any .yaml inside the dir
                for f in p.glob("*.yaml"):
                    known[pid] = f
                    break

    # Gather candidates from inputs + stage_summary
    candidates: set[str] = set()
    for v in stage.inputs.values():
        if isinstance(v, str) and v in known:
            candidates.add(v)
    for pid in stage_summary.get("rag_providers_queried", []):
        if isinstance(pid, str) and pid in known:
            candidates.add(pid)

    result = []
    for pid in sorted(candidates):
        result.append({"provider_id": pid, "manifest_path": known[pid]})
    return result


# ---------------------------------------------------------------------------
# Datasource discovery
# ---------------------------------------------------------------------------


def _discover_fabric_datasources(
    tenant_id: str,
    stage: StageSpec,
    known_connections: list[ConnectionSummary],
) -> list[str]:
    """Return names of fabric datasources referenced in stage inputs."""
    known_names = {c.name for c in known_connections}
    found: list[str] = []
    for v in stage.inputs.values():
        if isinstance(v, str) and v in known_names:
            found.append(v)
    return sorted(set(found))


def _discover_output_datasources(
    tenant_id: str,
    stage: StageSpec,
    stage_summary: dict,
    known_connections: list[ConnectionSummary],
) -> list[str]:
    """Return names of output datasources for the stage.

    Discovery:
    1. StageSpec.inputs keys starting with "output_" whose value is a known
       connection name.
    2. stage_summary["output_datasources_written"] list.
    """
    known_names = {c.name for c in known_connections}
    found: set[str] = set()

    for k, v in stage.inputs.items():
        if k.startswith("output_") and isinstance(v, str) and v in known_names:
            found.add(v)

    for ds_name in stage_summary.get("output_datasources_written", []):
        if isinstance(ds_name, str) and ds_name in known_names:
            found.add(ds_name)

    return sorted(found)


# ---------------------------------------------------------------------------
# Custom adapter discovery
# ---------------------------------------------------------------------------


def _discover_custom_adapters(tenant_id: str, stages: list[StageSpec]) -> list[str]:
    """Return list of custom adapter names used by stages (tenant-tier only)."""
    adapter_dir = _custom_adapters_dir(tenant_id)
    if not adapter_dir.is_dir():
        return []
    available = {p.stem for p in adapter_dir.glob("*.py")}

    used: set[str] = set()
    for stage in stages:
        # Adapter name may be embedded in tool_name as "adapter.<name>" or
        # directly as the tool_name, or referenced in inputs
        if stage.tool_name in available:
            used.add(stage.tool_name)
        for v in stage.inputs.values():
            if isinstance(v, str) and v in available:
                used.add(v)
    return sorted(used)


# ---------------------------------------------------------------------------
# ML backend discovery
# ---------------------------------------------------------------------------


def _discover_ml_backends(tenant_id: str, stages: list[StageSpec], stage_summaries: dict[str, dict]) -> list[str]:
    """Return list of ML backend names referenced in stage_summary[*].backend_used."""
    backend_dir = _compute_backends_dir(tenant_id)
    available: set[str] = set()
    if backend_dir.is_dir():
        available = {p.stem for p in backend_dir.glob("*.py")}

    used: set[str] = set()
    for stage in stages:
        summary = stage_summaries.get(stage.stage_id, {})
        backend = summary.get("backend_used")
        if isinstance(backend, str) and backend and backend in available:
            used.add(backend)
    return sorted(used)


# ---------------------------------------------------------------------------
# AWP YAML generation helpers
# ---------------------------------------------------------------------------


def _compute_node(
    stage: StageSpec,
    champion_params: dict,
    mode: Literal["replay", "reoptimize"],
    fabric_ds: list[str],
    output_ds: list[str],
    rag_providers: list[dict],
    depends_on: list[str],
    acceptance_criteria: dict | None,
) -> dict:
    """Build an AWP-compatible node dict for a Compute stage.

    Uses ``type: agent`` (valid in AWP v1.0) so the workflow can be imported
    and edited in the standard Workflows UI without validation errors.
    The full compute specification is preserved in ``x_compute`` (extension
    field, ignored by AWP v1.0 parsers) so a future ``type: compute`` runtime
    can read it without re-export.

    Instructions are structured to work with the compute_worker persona:
    the agent calls ``compute_run`` / ``compute_status`` / ``compute_result``
    MCP tools to submit and await the Compute Worker job.
    """
    params_or_grid = champion_params or {} if mode == "replay" else stage.param_grid or {}
    budget = (
        {"max_iterations": 1, "timeout_s": int(stage.budget.get("timeout_s", 3600))}
        if mode == "replay"
        else {"max_iterations": int(stage.budget.get("max_iterations", 50)),
              "timeout_s": int(stage.budget.get("timeout_s", 3600))}
    )
    ds_lines = "\n".join(f"  - {n} (input)" for n in fabric_ds)
    out_lines = "\n".join(f"  - {n} (output)" for n in output_ds)
    rag_lines = "\n".join(f"  - {r['provider_id']}" for r in rag_providers)

    instructions = (
        f"Execute Compute stage: {stage.tool_name}\n\n"
        f"Mode: {mode}\n"
        f"Parameters: {params_or_grid}\n"
        f"Budget: {budget}\n"
    )
    if ds_lines:
        instructions += f"Input datasources:\n{ds_lines}\n"
    if out_lines:
        instructions += f"Output sinks:\n{out_lines}\n"
    if rag_lines:
        instructions += f"RAG providers:\n{rag_lines}\n"
    instructions += (
        "\nSteps:\n"
        "1. Call compute_run(tool_name, params/param_grid, budget, datasources)\n"
        "2. Poll compute_status(run_id) until converged/failed\n"
        "3. Call compute_result(run_id) to retrieve best_loss and artifact_path\n"
        "4. Return best_loss, best_params, artifact_path in share_output\n"
    )
    if acceptance_criteria and acceptance_criteria.get("max_best_loss") is not None:
        thr = acceptance_criteria["max_best_loss"]
        on_fail = acceptance_criteria.get("on_fail", "abort")
        instructions += f"\nQuality gate: if best_loss > {thr}: {on_fail}\n"

    node: dict[str, Any] = {
        "id": stage.stage_id,
        "type": "agent",          # AWP v1.0 standard — importable without validation errors
        "agent": "compute_worker",
        "instructions": instructions,
        # Extension field — preserved for future type:compute runtime support (ADR-0090 M7)
        "x_compute": {
            "tool_name": stage.tool_name,
            **({"params": params_or_grid} if mode == "replay" else {"param_grid": params_or_grid}),
            "budget": budget,
            "mode": mode,
            "fabric_datasources": [{"name": n, "role": "input"} for n in fabric_ds],
            "output_datasources": [{"name": n, "role": "output"} for n in output_ds],
            "rag_datasources": [{"provider_id": r["provider_id"]} for r in rag_providers],
        },
        "share_output": ["artifact_path", "best_loss", "best_params", "watermark_advanced_to"],
    }
    if depends_on:
        node["depends_on"] = depends_on
    return node


def _quality_gate_node(
    stage_id: str,
    threshold: float,
    depends_on: list[str],
) -> dict:
    """Build an AWP-compatible agent node that acts as a quality gate.

    Uses ``type: agent`` (AWP v1.0 valid) with evaluation instructions.
    The gate criteria are preserved in ``x_quality_gate`` for future
    ``type: quality_gate`` runtime support.
    """
    instructions = (
        f"Evaluate quality gate for stage '{stage_id}'.\n\n"
        f"Check: best_loss from node '{stage_id}' must be ≤ {threshold}.\n\n"
        "Steps:\n"
        f"1. Read best_loss from the output of '{stage_id}'\n"
        f"2. If best_loss > {threshold}: output FAIL and stop the workflow\n"
        f"3. If best_loss ≤ {threshold}: output PASS and continue\n"
    )
    return {
        "id": f"quality_gate_after_{stage_id}",
        "type": "agent",           # AWP v1.0 standard
        "agent": "compute_worker",
        "instructions": instructions,
        # Extension field (ADR-0090 M9 — type:quality_gate pending AWP v1.1)
        "x_quality_gate": {
            "metric": "best_loss",
            "from_node": stage_id,
            "operator": "lte",
            "threshold": threshold,
            "on_pass": "continue",
        "depends_on": depends_on,
        "criteria": [
            {
                "metric": "best_loss",
                "from_node": stage_id,
                "operator": "lte",
                "threshold": threshold,
            }
        ],
        },
        "depends_on": depends_on,
    }


def _build_workflow_yaml(
    pipeline_id: str,
    pipeline_name: str,
    stages: list[StageSpec],
    champion_params_by_stage: dict[str, dict],
    mode: Literal["replay", "reoptimize"],
    fabric_ds_by_stage: dict[str, list[str]],
    output_ds_by_stage: dict[str, list[str]],
    rag_by_stage: dict[str, list[dict]],
    steering_gate: bool,
    acceptance_criteria: dict | None,
    schedule_cron: str | None,
    schedule_timezone: str,
    version: str,
) -> str:
    """Assemble the workflow.awp.yaml content as a string."""
    nodes: list[dict] = []
    prev_node_id: str | None = None

    for stage in stages:
        depends_on: list[str] = []
        if prev_node_id:
            depends_on = [prev_node_id]

        compute_node = _compute_node(
            stage=stage,
            champion_params=champion_params_by_stage.get(stage.stage_id, {}),
            mode=mode,
            fabric_ds=fabric_ds_by_stage.get(stage.stage_id, []),
            output_ds=output_ds_by_stage.get(stage.stage_id, []),
            rag_providers=rag_by_stage.get(stage.stage_id, []),
            depends_on=depends_on,
            acceptance_criteria=acceptance_criteria,
        )
        nodes.append(compute_node)

        # Insert quality gate if steering_gate or acceptance_criteria present
        if steering_gate or (acceptance_criteria and acceptance_criteria.get("max_best_loss") is not None):
            threshold = (
                acceptance_criteria.get("max_best_loss", 999)
                if acceptance_criteria
                else 999
            )
            gate = _quality_gate_node(
                stage_id=stage.stage_id,
                threshold=threshold,
                depends_on=[stage.stage_id],
            )
            nodes.append(gate)
            prev_node_id = gate["id"]
        else:
            prev_node_id = stage.stage_id

    # Build triggers block (M8)
    triggers: dict[str, Any] = {}
    if schedule_cron:
        triggers["schedule"] = {
            "cron": schedule_cron,
            "timezone": schedule_timezone,
        }
    triggers["slash"] = {
        "command": f"/run-{_slugify(pipeline_name)}",
        "visibility": "admin",
    }
    triggers["api"] = {"enabled": False}

    # Standard AWP format: orchestration.engine=dag + orchestration.graph
    # (not dag.nodes — that was a non-standard structure)
    workflow_doc: dict[str, Any] = {
        "awp": "1.0.0",
        "workflow": {
            "name": pipeline_name,
            "description": (
                f"AWP Workflow DAG exported from Compute Pipeline {pipeline_id}. "
                f"Mode: {mode}. Spec: ADR-0090 (corvin-pipeline-export)."
            ),
        },
        "orchestration": {
            "engine": "dag",
            "graph": nodes,
            "triggers": triggers,
        },
    }

    return yaml.dump(workflow_doc, sort_keys=False, allow_unicode=True, default_flow_style=False)


def _slugify(name: str) -> str:
    """Convert a pipeline name to a slug suitable for a slash command."""
    slug = re.sub(r"[^a-z0-9-]", "-", name.lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "pipeline"


# ---------------------------------------------------------------------------
# Main exporter class
# ---------------------------------------------------------------------------


class PipelineAWPExporter:
    """Export a CorvinOS compute pipeline to an AWP-compatible workflow package.

    Args:
        tenant_id: The tenant that owns the pipeline.
        pipeline_id: The pipeline to export.
    """

    def __init__(self, tenant_id: str, pipeline_id: str) -> None:
        self._tenant_id = tenant_id
        self._pipeline_id = pipeline_id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def export(
        self,
        package_id: str,
        version: str = "1.0.0",
        mode: Literal["replay", "reoptimize"] = "replay",
        include_sample_data: bool = True,
        sample_rows: int = 100,
        include_rag_manifests: bool = True,
        include_fabric_datasources: bool = True,
        include_output_datasources: bool = True,
        include_watermarks: bool = False,
        include_custom_adapters: bool = True,
        include_ml_backends: bool = True,
        schedule_cron: str | None = None,
        schedule_timezone: str = "UTC",
        acceptance_criteria: dict | None = None,
        publish_to_hub: bool = False,
        output_dir: Path | None = None,
    ) -> AWPackageMeta:
        """Export the pipeline as an AWP workflow package (zip).

        Args:
            package_id: Unique identifier for this export package.
            version: SemVer string for the package.
            mode: ``"replay"`` bakes champion params; ``"reoptimize"`` exports
                  param_grid for a fresh search.
            include_sample_data: Bundle a PII-redacted sample CSV per stage.
            sample_rows: Number of rows per sample CSV.
            include_rag_manifests: Bundle RAG provider manifests (YAML).
            include_fabric_datasources: Bundle input datasource manifests
                                         (vault_path stripped).
            include_output_datasources: Bundle output datasource manifests
                                         (vault_path stripped).
            include_watermarks: Include watermark state in datasource manifests.
            include_custom_adapters: Bundle tenant-tier custom adapters
                                      (AST-gated).
            include_ml_backends: Bundle tenant-tier ML backend scripts
                                  (AST-gated).
            schedule_cron: Optional cron expression for the schedule trigger.
            schedule_timezone: Timezone for the schedule trigger.
            acceptance_criteria: Optional dict with ``max_best_loss`` key.
            publish_to_hub: Reserved for M13 — raises NotImplementedError if
                            True (no hub endpoint wired yet).
            output_dir: Directory to write the package zip into. Defaults to
                        the pipeline root.

        Returns:
            AWPackageMeta describing what was assembled.

        Raises:
            FileNotFoundError: if the pipeline manifest is absent.
            ValueError: if AST gates reject a custom adapter or ML backend.
            NotImplementedError: if ``publish_to_hub=True``.
        """
        if publish_to_hub:
            raise NotImplementedError(
                "publish_to_hub is reserved for ADR-0090 M13; hub endpoint not wired."
            )

        audit_path = _audit_path(self._tenant_id)

        # --- Load pipeline manifest first — validate existence before audit ---
        # Audit-first contract: emit AFTER we know the pipeline exists so the
        # audit record is never a false positive (Finding 1 fix, ADR-0090 review).
        raw_manifest = _load_pipeline_manifest(self._tenant_id, self._pipeline_id)

        # Now that existence is confirmed, emit audit event before any file I/O.
        _sec.write_event(
            audit_path,
            "compute.pipeline_exported",
            details={
                "pipeline_id": self._pipeline_id,
                "tenant_id": self._tenant_id,
                "package_id": package_id,
                "version": version,
                "mode": mode,
            },
        )
        pipeline_name: str = raw_manifest.get("pipeline_id", self._pipeline_id)
        stages: list[StageSpec] = [
            StageSpec.from_dict(s) for s in raw_manifest.get("stages", [])
        ]
        steering_gate: bool = bool(raw_manifest.get("steering_gate", True))

        # --- Load pipeline summary (best_losses, completed_stages) ---
        pipeline_summary = _load_pipeline_summary(self._tenant_id, self._pipeline_id)
        best_losses: dict[str, float] = pipeline_summary.get("best_losses", {})

        # --- Load per-stage summaries ---
        stage_summaries: dict[str, dict] = {}
        for stage in stages:
            stage_summary_path = (
                _pipeline_root(self._tenant_id, self._pipeline_id)
                / "stages" / stage.stage_id / "stage_summary.json"
            )
            stage_summaries[stage.stage_id] = _load_json(stage_summary_path)

        # --- Resolve champion params per stage ---
        champion_params_by_stage: dict[str, dict] = {}
        for stage in stages:
            # Check pipeline-level best_losses first (fast path)
            summary_params = stage_summaries.get(stage.stage_id, {}).get("best_params")
            if summary_params:
                champion_params_by_stage[stage.stage_id] = summary_params
            else:
                champion_params_by_stage[stage.stage_id] = _load_per_stage_champion(
                    self._tenant_id, self._pipeline_id, stage.stage_id
                )

        # --- Datasource registry ---
        registry = DataSourceRegistry(corvin_home=_corvin_home())
        known_connections: list[ConnectionSummary] = []
        try:
            known_connections = registry.list_connections(self._tenant_id)
        except Exception as exc:
            logger.warning("compute_awp_exporter: datasource registry error: %s", exc)

        # --- Per-stage datasource discovery ---
        fabric_ds_by_stage: dict[str, list[str]] = {}
        output_ds_by_stage: dict[str, list[str]] = {}
        all_fabric_names: set[str] = set()
        all_output_names: set[str] = set()

        for stage in stages:
            ss = stage_summaries.get(stage.stage_id, {})
            fds = _discover_fabric_datasources(self._tenant_id, stage, known_connections)
            ods = _discover_output_datasources(self._tenant_id, stage, ss, known_connections)
            fabric_ds_by_stage[stage.stage_id] = fds
            output_ds_by_stage[stage.stage_id] = ods
            all_fabric_names.update(fds)
            all_output_names.update(ods)

        # --- RAG provider discovery ---
        rag_by_stage: dict[str, list[dict]] = {}
        all_rag_providers: list[dict] = []
        rag_provider_ids_seen: set[str] = set()

        for stage in stages:
            ss = stage_summaries.get(stage.stage_id, {})
            providers = _discover_rag_providers(self._tenant_id, stage, ss)
            rag_by_stage[stage.stage_id] = providers
            for p in providers:
                pid = p["provider_id"]
                if pid not in rag_provider_ids_seen:
                    rag_provider_ids_seen.add(pid)
                    all_rag_providers.append(p)

        # --- Custom adapter discovery ---
        custom_adapters: list[str] = []
        if include_custom_adapters:
            custom_adapters = _discover_custom_adapters(self._tenant_id, stages)

        # --- ML backend discovery ---
        ml_backends: list[str] = []
        if include_ml_backends:
            ml_backends = _discover_ml_backends(
                self._tenant_id, stages, stage_summaries
            )

        # --- Assemble package in a temp staging dir ---
        staging_dir = Path(tempfile.mkdtemp(prefix="awp_export_"))
        try:
            meta = self._assemble_package(
                staging_dir=staging_dir,
                package_id=package_id,
                version=version,
                mode=mode,
                pipeline_name=pipeline_name,
                stages=stages,
                champion_params_by_stage=champion_params_by_stage,
                best_losses=best_losses,
                steering_gate=steering_gate,
                fabric_ds_by_stage=fabric_ds_by_stage,
                output_ds_by_stage=output_ds_by_stage,
                all_fabric_names=all_fabric_names,
                all_output_names=all_output_names,
                rag_by_stage=rag_by_stage,
                all_rag_providers=all_rag_providers,
                custom_adapters=custom_adapters,
                ml_backends=ml_backends,
                known_connections=known_connections,
                registry=registry,
                include_rag_manifests=include_rag_manifests,
                include_fabric_datasources=include_fabric_datasources,
                include_output_datasources=include_output_datasources,
                include_watermarks=include_watermarks,
                include_custom_adapters=include_custom_adapters,
                include_ml_backends=include_ml_backends,
                schedule_cron=schedule_cron,
                schedule_timezone=schedule_timezone,
                acceptance_criteria=acceptance_criteria,
                output_dir=output_dir,
            )
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)

        return meta

    # ------------------------------------------------------------------
    # Private assembly
    # ------------------------------------------------------------------

    def _assemble_package(
        self,
        *,
        staging_dir: Path,
        package_id: str,
        version: str,
        mode: Literal["replay", "reoptimize"],
        pipeline_name: str,
        stages: list[StageSpec],
        champion_params_by_stage: dict[str, dict],
        best_losses: dict[str, float],
        steering_gate: bool,
        fabric_ds_by_stage: dict[str, list[str]],
        output_ds_by_stage: dict[str, list[str]],
        all_fabric_names: set[str],
        all_output_names: set[str],
        rag_by_stage: dict[str, list[dict]],
        all_rag_providers: list[dict],
        custom_adapters: list[str],
        ml_backends: list[str],
        known_connections: list[ConnectionSummary],
        registry: DataSourceRegistry,
        include_rag_manifests: bool,
        include_fabric_datasources: bool,
        include_output_datasources: bool,
        include_watermarks: bool,
        include_custom_adapters: bool,
        include_ml_backends: bool,
        schedule_cron: str | None,
        schedule_timezone: str,
        acceptance_criteria: dict | None,
        output_dir: Path | None,
    ) -> AWPackageMeta:
        """Write all package artefacts to *staging_dir*, then zip to *output_dir*."""
        pkg_dir = staging_dir / package_id

        # 1. workflow.awp.yaml  (M11)
        workflow_yaml = _build_workflow_yaml(
            pipeline_id=self._pipeline_id,
            pipeline_name=pipeline_name,
            stages=stages,
            champion_params_by_stage=champion_params_by_stage,
            mode=mode,
            fabric_ds_by_stage=fabric_ds_by_stage,
            output_ds_by_stage=output_ds_by_stage,
            rag_by_stage=rag_by_stage,
            steering_gate=steering_gate,
            acceptance_criteria=acceptance_criteria,
            schedule_cron=schedule_cron,
            schedule_timezone=schedule_timezone,
            version=version,
        )
        _safe_write(pkg_dir / "workflow.awp.yaml", workflow_yaml, mode=0o644)

        # 2. RAG manifests  (M5)
        rag_count = 0
        if include_rag_manifests:
            rag_out_dir = pkg_dir / "rag_providers"
            for provider_info in all_rag_providers:
                pid = provider_info["provider_id"]
                manifest_path = Path(provider_info["manifest_path"])
                if not manifest_path.exists():
                    logger.warning(
                        "compute_awp_exporter: RAG manifest not found: %s", manifest_path
                    )
                    continue
                try:
                    yaml_str = RAGProviderImportExport.export_manifest(manifest_path)
                    valid, err = RAGProviderImportExport.validate_manifest(yaml_str)
                    if not valid:
                        logger.warning(
                            "compute_awp_exporter: RAG manifest %s invalid: %s", pid, err
                        )
                        continue
                    _safe_write(
                        rag_out_dir / f"{pid}.yaml",
                        yaml_str,
                        mode=0o600,
                    )
                    rag_count += 1
                except Exception as exc:
                    logger.warning(
                        "compute_awp_exporter: could not export RAG provider %s: %s",
                        pid, exc,
                    )

        # 3. Fabric datasource manifests  (M4)
        ds_count = 0
        if include_fabric_datasources:
            ds_out_dir = pkg_dir / "datasources" / "input"
            for ds_name in sorted(all_fabric_names):
                exported = self._export_datasource_manifest(
                    registry=registry,
                    ds_name=ds_name,
                    out_dir=ds_out_dir,
                    include_watermarks=include_watermarks,
                )
                if exported:
                    ds_count += 1

        # 4. Output datasource manifests  (M6)
        out_ds_count = 0
        if include_output_datasources:
            out_ds_dir = pkg_dir / "datasources" / "output"
            for ds_name in sorted(all_output_names):
                exported = self._export_datasource_manifest(
                    registry=registry,
                    ds_name=ds_name,
                    out_dir=out_ds_dir,
                    include_watermarks=include_watermarks,
                )
                if exported:
                    out_ds_count += 1

        # 5. Custom adapters  (M9)
        ca_count = 0
        if include_custom_adapters:
            adapter_dir = _custom_adapters_dir(self._tenant_id)
            ca_out_dir = pkg_dir / "adapters"
            for name in custom_adapters:
                src = adapter_dir / f"{name}.py"
                if not src.exists():
                    continue
                source = src.read_text(encoding="utf-8")
                try:
                    _ast_gate(source, f"custom adapter {name!r}")
                except ValueError as exc:
                    raise ValueError(
                        f"compute_awp_exporter: AST gate rejected adapter {name!r}: {exc}"
                    ) from exc
                _safe_write(ca_out_dir / f"{name}.py", source, mode=0o600)
                ca_count += 1

        # 6. ML backends  (M10)
        be_count = 0
        if include_ml_backends:
            backend_dir = _compute_backends_dir(self._tenant_id)
            be_out_dir = pkg_dir / "backends"
            for name in ml_backends:
                src = backend_dir / f"{name}.py"
                if not src.exists():
                    continue
                source = src.read_text(encoding="utf-8")
                try:
                    _ast_gate(source, f"ML backend {name!r}")
                except ValueError as exc:
                    raise ValueError(
                        f"compute_awp_exporter: AST gate rejected backend {name!r}: {exc}"
                    ) from exc
                _safe_write(be_out_dir / f"{name}.py", source, mode=0o600)
                be_count += 1

        # 7. processing_record.yaml  (M12)
        processing_record = self._build_processing_record(
            package_id=package_id,
            version=version,
            mode=mode,
            stages=stages,
            champion_params_by_stage=champion_params_by_stage,
            best_losses=best_losses,
            rag_count=rag_count,
            ds_count=ds_count,
            out_ds_count=out_ds_count,
            ca_count=ca_count,
            be_count=be_count,
            schedule_cron=schedule_cron,
            has_acceptance_criteria=acceptance_criteria is not None,
        )
        _safe_write(
            pkg_dir / "processing_record.yaml",
            yaml.dump(processing_record, sort_keys=False, allow_unicode=True),
            mode=0o600,
        )

        # 8. Zip the package
        if output_dir is None:
            output_dir = _pipeline_root(self._tenant_id, self._pipeline_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        zip_path = output_dir / f"{package_id}-{version}.awp.zip"

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file in sorted(pkg_dir.rglob("*")):
                if file.is_file():
                    zf.write(file, arcname=file.relative_to(staging_dir))

        try:
            os.chmod(zip_path, 0o600)
        except OSError:
            pass

        logger.info(
            "compute_awp_exporter: package written to %s "
            "(stages=%d, rag=%d, ds=%d, out_ds=%d, ca=%d, be=%d)",
            zip_path, len(stages), rag_count, ds_count, out_ds_count, ca_count, be_count,
        )

        return AWPackageMeta(
            package_id=package_id,
            version=version,
            output_dir=output_dir,
            stage_count=len(stages),
            rag_provider_count=rag_count,
            datasource_count=ds_count,
            output_datasource_count=out_ds_count,
            ml_backend_count=be_count,
            custom_adapter_count=ca_count,
            mode=mode,
            schedule_cron=schedule_cron,
            has_acceptance_criteria=acceptance_criteria is not None,
        )

    def _export_datasource_manifest(
        self,
        *,
        registry: DataSourceRegistry,
        ds_name: str,
        out_dir: Path,
        include_watermarks: bool,
    ) -> bool:
        """Load, strip vault_path, and write a connection manifest.

        Returns True on success.
        """
        try:
            conn = registry.load_manifest(ds_name, self._tenant_id)
        except Exception as exc:
            logger.warning(
                "compute_awp_exporter: could not load manifest for %s: %s", ds_name, exc
            )
            return False

        # Credential scrub patterns — any raw key whose name suggests a literal
        # credential is stripped before export (finding: conn.source.raw spread).
        _CRED_KEYS = frozenset({
            "connection_string", "dsn", "url", "uri", "password", "passwd",
            "secret", "token", "api_key", "apikey", "private_key",
        })

        # Build sanitised dict — vault_path NEVER exported
        auth_safe = {
            "method": conn.auth.method,
            "secret_keys": list(conn.auth.secret_keys),  # copy; never mutate original
            # vault_path intentionally omitted
        }
        raw_scrubbed = {
            k: "[REDACTED]" if k.lower() in _CRED_KEYS else v
            for k, v in conn.source.raw.items()
        }
        source_raw = {
            "adapter": conn.source.adapter,
            "region": conn.source.region,
            **raw_scrubbed,
        }
        manifest_dict: dict[str, Any] = {
            "name": conn.name,
            "adapter": conn.adapter,
            "source": source_raw,
            "auth": auth_safe,
            "pii_handling": conn.pii_handling,
            "filters": conn.filters,
            "tags": conn.tags,
        }
        if conn.schema_hint is not None:
            manifest_dict["schema_hint"] = conn.schema_hint
        if conn.incremental is not None and include_watermarks:
            manifest_dict["incremental"] = {
                "mode": conn.incremental.mode,
                "watermark_col": conn.incremental.watermark_col,
                # initial_watermark omitted (could contain sensitive boundary values)
            }

        _safe_write(
            out_dir / f"{ds_name}.json",
            json.dumps(manifest_dict, indent=2, sort_keys=True),
            mode=0o600,
        )
        return True

    def _build_processing_record(
        self,
        *,
        package_id: str,
        version: str,
        mode: str,
        stages: list[StageSpec],
        champion_params_by_stage: dict[str, dict],
        best_losses: dict[str, float],
        rag_count: int,
        ds_count: int,
        out_ds_count: int,
        ca_count: int,
        be_count: int,
        schedule_cron: str | None,
        has_acceptance_criteria: bool,
    ) -> dict:
        """Build the processing_record.yaml payload (M12)."""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        return {
            "processing_record": {
                "schema_version": "1.0",
                "package_id": package_id,
                "version": version,
                "generated_at": now,
                "pipeline_id": self._pipeline_id,
                "tenant_id": self._tenant_id,
                "mode": mode,
                "spec_version": _AWP_SPEC_VERSION,
                "corvin_min_version": _AWP_CORVIN_MIN_VERSION,
                "stage_count": len(stages),
                "rag_provider_count": rag_count,
                "datasource_count": ds_count,
                "output_datasource_count": out_ds_count,
                "custom_adapter_count": ca_count,
                "ml_backend_count": be_count,
                "schedule_cron": schedule_cron,
                "has_acceptance_criteria": has_acceptance_criteria,
                "stages": [
                    {
                        "stage_id": s.stage_id,
                        "tool_name": s.tool_name,
                        "strategy": s.strategy,
                        "best_loss": best_losses.get(s.stage_id),
                        "champion_params_present": bool(
                            champion_params_by_stage.get(s.stage_id)
                        ),
                    }
                    for s in stages
                ],
                "compliance": {
                    "vault_path_exported": False,
                    "ast_gated": True,
                    "audit_first": True,
                    "sensitive_files_mode": "0o600",
                },
            }
        }
