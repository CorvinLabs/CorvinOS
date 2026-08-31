"""acs_graph_builder.py — reconstruct execution graph from an ACS run directory.

Reads per-iteration data written by acs_runtime._manager_loop (M9a adds
subtask-definition persistence) and produces a WorkflowGraph that can be
serialised to:

  * AWP YAML — ``orchestration.engine: dag``           (deterministic replay)
  * AWP YAML — ``orchestration.engine: delegation_loop`` (adaptive template)
  * AWPKG ZIP — portable package containing workflow + provenance sidecar

Called by ``acs_engine_adapter.export_acs_run_as_awpkg()``.

MUST NOT import anthropic — CI AST lint enforces.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml as _yaml
    _YAML_OK = True
except ImportError:
    _yaml = None  # type: ignore[assignment]
    _YAML_OK = False


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class GraphNode:
    node_id: str
    task: str
    depends_on: list[str] = field(default_factory=list)
    iteration: int = 0
    depth: int = 0
    status: str = "unknown"
    confidence: float = 0.0
    worker_id: str = ""


@dataclass
class WorkflowGraph:
    run_id: str
    workflow_id: str
    nodes: list[GraphNode] = field(default_factory=list)
    gate_results: list[dict[str, Any]] = field(default_factory=list)
    quality_aggregate: float = 0.0
    manifest: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    total_iterations: int = 0
    workers_spawned: int = 0

    def is_empty(self) -> bool:
        return not self.nodes


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

_SAFE_ID_RE = re.compile(r"[^a-z0-9_-]")


def _safe_id(raw: str) -> str:
    return (_SAFE_ID_RE.sub("-", raw.lower())[:60].strip("-") or "node")


class ACSGraphBuilder:
    """Reconstruct a WorkflowGraph from an ACS run directory."""

    def __init__(self, run_dir: Path) -> None:
        self._d = run_dir

    # ── public API ────────────────────────────────────────────────────────

    def build(self) -> WorkflowGraph | None:
        """Return the graph or None if the run_dir does not exist."""
        if not self._d.exists():
            return None

        manifest = self._json(self._d / "manifest.json") or {}
        result = self._json(self._d / "result.json") or {}
        run_id = manifest.get("run_id") or self._d.name
        workflow_id = manifest.get("workflow_id") or "unknown"

        # Subtask definitions (M9a) — map iteration → list[subtask dict]
        subtasks_map: dict[int, list[dict[str, Any]]] = {}
        for p in sorted((self._d / "subtasks").glob("*.json") if (self._d / "subtasks").exists() else []):
            d = self._json(p) or {}
            it = d.get("iteration", 0)
            subtasks_map[it] = d.get("subtasks") or []

        # Worker metadata (confidence, status)
        worker_meta: dict[str, dict[str, Any]] = {}
        for p in sorted((self._d / "workers").glob("*.json") if (self._d / "workers").exists() else []):
            d = self._json(p) or {}
            wid = d.get("worker_id", "")
            if wid:
                worker_meta[wid] = d

        # Gate chain evaluations
        gate_results: list[dict[str, Any]] = []
        for p in sorted((self._d / "gate_results").glob("*.json") if (self._d / "gate_results").exists() else []):
            d = self._json(p) or {}
            gate_results.append(d)

        quality_aggregate = 0.0
        if gate_results:
            quality_aggregate = float(gate_results[-1].get("aggregate_score", 0.0))

        # Build nodes — one per subtask, edges from iteration order + explicit depends_on
        nodes: list[GraphNode] = []
        iter_node_ids: dict[int, list[str]] = {}

        for iteration in sorted(subtasks_map):
            prev_ids = iter_node_ids.get(iteration - 1, [])
            iter_node_ids[iteration] = []

            for i, st in enumerate(subtasks_map[iteration]):
                wid = st.get("id") or f"st_{iteration}_{i}"
                task_text = st.get("task") or st.get("description") or ""
                explicit_deps: list[str] = st.get("depends_on") or []

                # Stable node_id: prefer the worker_id (already short hex), fall back to md5
                if wid:
                    node_id = f"w-{_safe_id(wid)}"
                else:
                    h = hashlib.md5(f"{iteration}:{task_text}".encode()).hexdigest()[:8]
                    node_id = f"w-{h}"

                # Edge resolution: explicit deps win; otherwise depend on all prev-iter nodes
                if explicit_deps:
                    resolved_deps = [f"w-{_safe_id(d)}" for d in explicit_deps]
                else:
                    resolved_deps = list(prev_ids)

                meta = worker_meta.get(wid) or {}
                node = GraphNode(
                    node_id=node_id,
                    task=task_text[:500],
                    depends_on=resolved_deps,
                    iteration=iteration,
                    depth=int(meta.get("depth", st.get("depth", 0))),
                    status=meta.get("status", "unknown"),
                    confidence=float(meta.get("confidence", 0.0)),
                    worker_id=wid,
                )
                nodes.append(node)
                iter_node_ids[iteration].append(node_id)

        return WorkflowGraph(
            run_id=run_id,
            workflow_id=workflow_id,
            nodes=nodes,
            gate_results=gate_results,
            quality_aggregate=quality_aggregate,
            manifest=manifest,
            result=result,
            total_iterations=manifest.get("iterations") or len(subtasks_map),
            workers_spawned=manifest.get("workers_spawned") or len(nodes),
        )

    @staticmethod
    def _json(path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None


# ---------------------------------------------------------------------------
# AWP YAML serialiser
# ---------------------------------------------------------------------------

def _serialize(data: Any) -> str:
    if _YAML_OK:
        return _yaml.dump(data, allow_unicode=True, default_flow_style=False,
                          sort_keys=False)
    return json.dumps(data, indent=2, ensure_ascii=False)


def graph_to_awp_yaml(
    graph: WorkflowGraph,
    *,
    mode: str = "dag",
    description: str = "",
) -> str:
    """Serialize a WorkflowGraph to AWP YAML.

    Parameters
    ----------
    mode:
        ``"dag"`` — static deterministic replay (orchestration.engine: dag).
        ``"template"`` — adaptive re-exploration seed
                         (orchestration.engine: delegation_loop).
    """
    short = graph.run_id[:8] if len(graph.run_id) >= 8 else graph.run_id
    desc = description or (
        f"ACS-discovered workflow from run {short} "
        f"({graph.total_iterations} iterations, "
        f"{graph.workers_spawned} workers)"
    )

    nodes_section = []
    for node in graph.nodes:
        entry: dict[str, Any] = {
            "id": node.node_id,
            "task": node.task,
        }
        if node.depends_on:
            entry["depends_on"] = node.depends_on
        entry["meta"] = {
            "acs_confidence": round(node.confidence, 3),
            "acs_iteration": node.iteration,
            "acs_depth": node.depth,
            "acs_status": node.status,
        }
        nodes_section.append(entry)

    if mode == "dag":
        wf_id = f"discovered-{short}"
        doc: dict[str, Any] = {
            "apiVersion": "1.0.0",
            "workflow": {
                "id": wf_id,
                "description": desc,
                "tags": ["acs-discovered", "dag"],
            },
            "orchestration": {
                "engine": "dag",
            },
            "nodes": nodes_section,
            "state": {"initial": {}},
        }
    else:
        wf_id = f"template-{short}"
        doc = {
            "apiVersion": "1.0.0",
            "workflow": {
                "id": wf_id,
                "description": desc,
                "tags": ["acs-template", "delegation_loop"],
            },
            "orchestration": {
                "engine": "delegation_loop",
                "delegation_loop": {
                    "manager": {
                        "model": "claude-sonnet-5",
                        "prompt_template": "default",
                    },
                    "budget": {
                        "max_loops": max(10, graph.total_iterations + 3),
                        "max_workers_per_iteration": max(
                            4, graph.workers_spawned // max(graph.total_iterations, 1) + 1
                        ),
                        "max_depth": max(
                            2, max((n.depth for n in graph.nodes), default=0) + 1
                        ),
                        "token_limit": 100000,
                        "max_rejected_completions": 3,
                    },
                },
            },
            "nodes": nodes_section,
            "state": {"initial": {}},
        }

    return _serialize(doc)


# ---------------------------------------------------------------------------
# AWPKG packer
# ---------------------------------------------------------------------------

def build_awpkg_bytes(
    graph: WorkflowGraph,
    *,
    mode: str = "dag",
    description: str = "",
    tenant_id: str = "_default",
) -> bytes:
    """Pack the graph as a portable AWPKG ZIP archive.

    Archive layout:
        manifest.yaml
        workflows/discovered-<id>.awp.yaml   (or template-<id>...)
        provenance/acs_manifest.json
        provenance/gate_results.json
        provenance/quality.json
    """
    short = graph.run_id[:8] if len(graph.run_id) >= 8 else graph.run_id
    prefix = "discovered" if mode == "dag" else "template"
    wf_filename = f"{prefix}-{short}.awp.yaml"
    pkg_id = f"com.corvin.{prefix}-{short}"

    desc = description or (
        f"ACS-discovered workflow from run {short} "
        f"({graph.total_iterations} iterations, "
        f"{graph.workers_spawned} workers)"
    )

    awp_yaml = graph_to_awp_yaml(graph, mode=mode, description=desc)

    manifest: dict[str, Any] = {
        "awpkg": "1.0",
        "id": pkg_id,
        "name": f"ACS {'DAG' if mode == 'dag' else 'Template'}: {graph.workflow_id}",
        "version": "0.1.0",
        "description": desc,
        "author": f"acs/{tenant_id}",
        "license": "Apache-2.0",
        "workflow_description": desc,
        "components": {
            "workflows": [f"workflows/{wf_filename}"],
        },
        "permissions": {"network": False, "compute": True, "secrets": []},
        "dependencies": [],
        "acs_provenance": {
            "run_id": graph.run_id,
            "workflow_id": graph.workflow_id,
            "iterations": graph.total_iterations,
            "workers_spawned": graph.workers_spawned,
            "quality_aggregate": round(graph.quality_aggregate, 4),
            "export_mode": mode,
            "node_count": len(graph.nodes),
        },
    }

    provenance_manifest = {
        k: v for k, v in graph.manifest.items()
        if k in {
            "run_id", "workflow_id", "status", "engine",
            "started_at", "completed_at", "duration_s",
            "iterations", "workers_spawned", "budget_breach",
        }
    }
    quality = {
        "aggregate_score": graph.quality_aggregate,
        "gate_evaluations": len(graph.gate_results),
        "last_evaluation": graph.gate_results[-1] if graph.gate_results else None,
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        manifest_bytes = _serialize(manifest).encode("utf-8")
        zf.writestr("manifest.yaml", manifest_bytes)
        zf.writestr(f"workflows/{wf_filename}", awp_yaml.encode("utf-8"))
        zf.writestr(
            "provenance/acs_manifest.json",
            json.dumps(provenance_manifest, indent=2, ensure_ascii=False, default=str).encode("utf-8"),
        )
        zf.writestr(
            "provenance/gate_results.json",
            json.dumps(graph.gate_results, indent=2, ensure_ascii=False, default=str).encode("utf-8"),
        )
        zf.writestr(
            "provenance/quality.json",
            json.dumps(quality, indent=2, ensure_ascii=False, default=str).encode("utf-8"),
        )

    return buf.getvalue()
