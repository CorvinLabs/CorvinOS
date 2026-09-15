"""compute_awp_importer.py — ADR-0090 M3: AWPImporter.

Round-trip: awpkg bundle (zip or directory) → PipelineManifest.

Rules (load-bearing):
- Do NOT auto-submit or auto-install. Every install_* is an explicit call.
- Do NOT override an existing manifest without warning; log + skip unless
  force=True is passed.
- Do NOT restore vault_path from ConnectionManifest (strip auth.vault_path).
- Do NOT import anthropic (CI AST lint enforces this).
- Audit-first: emit BEFORE file writes.
- File modes: 0o600 for all sensitive output files.
"""
from __future__ import annotations

import ast
import json
import logging
import os
import secrets
import shutil
import socket
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Forge path bootstrap — lazy so tests can override sys.path first.
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[3]


def _bootstrap_forge() -> None:
    forge_pkg = _REPO / "operator" / "forge"
    if str(forge_pkg) not in sys.path:
        sys.path.insert(0, str(forge_pkg))


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ImportError(ValueError):
    """Raised on irrecoverable awpkg structural problems."""


class ResidencyViolation(ValueError):
    """Raised when a datasource manifest violates data-residency policy."""


class ClassificationError(ValueError):
    """Raised when a manifest's data classification is incompatible."""


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class InstallResult:
    rag_providers: list[str] = field(default_factory=list)
    input_datasources: list[str] = field(default_factory=list)
    output_datasources: list[str] = field(default_factory=list)
    custom_adapters: list[str] = field(default_factory=list)
    ml_backends: list[str] = field(default_factory=list)
    watermarks_restored: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_FORBIDDEN_IMPORTS = {"anthropic", "subprocess"}
_FORBIDDEN_CALL_NAMES = {"exec", "eval", "__import__"}


def _audit_path(tenant_id: str) -> Path:
    _bootstrap_forge()
    try:
        from forge import paths as _forge_paths  # type: ignore
        return _forge_paths.tenant_home(tenant_id) / "global" / "audit.jsonl"
    except Exception:
        return Path.home() / ".corvin" / "tenants" / tenant_id / "global" / "audit.jsonl"


def _emit(tenant_id: str, event_type: str, details: dict[str, Any]) -> None:
    """Emit to the L16 hash chain. Best-effort; never raises."""
    try:
        _bootstrap_forge()
        from forge import security_events as _sec  # type: ignore
        path = _audit_path(tenant_id)
        _sec.write_event(path, event_type, details=details)
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit emit failed for %s: %s", event_type, exc)


def _tenant_home(tenant_id: str) -> Path:
    _bootstrap_forge()
    try:
        from forge import paths as _forge_paths  # type: ignore
        return _forge_paths.tenant_home(tenant_id)
    except Exception:
        return Path.home() / ".corvin" / "tenants" / tenant_id


def _atomic_write(dest: Path, content: bytes, *, mode: int = 0o600) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
    tmp.write_bytes(content)
    try:
        os.chmod(tmp, mode)
    except OSError:
        pass
    os.replace(tmp, dest)


def _ast_security_check(source: str, filename: str) -> list[str]:
    """Return list of security violations found in *source*."""
    violations: list[str] = []
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        return [f"SyntaxError in {filename}: {exc}"]

    for node in ast.walk(tree):
        # Forbidden async function definitions
        if isinstance(node, ast.AsyncFunctionDef):
            violations.append(
                f"{filename}: async def {node.name!r} is not permitted"
            )
        # Forbidden imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                base = alias.name.split(".")[0]
                if base in _FORBIDDEN_IMPORTS:
                    violations.append(
                        f"{filename}: import {alias.name!r} is forbidden"
                    )
        if isinstance(node, ast.ImportFrom):
            module = (node.module or "").split(".")[0]
            if module in _FORBIDDEN_IMPORTS:
                violations.append(
                    f"{filename}: from {node.module!r} import ... is forbidden"
                )
        # Forbidden call names: exec / eval / __import__
        if isinstance(node, ast.Call):
            func = node.func
            name = ""
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name in _FORBIDDEN_CALL_NAMES:
                violations.append(
                    f"{filename}: call to {name!r} is forbidden"
                )
    return violations


def _topo_sort_compute_nodes(nodes: list[dict]) -> list[dict]:
    """Kahn topological sort of compute nodes by depends_on."""
    by_id = {n["id"]: n for n in nodes}
    incoming: dict[str, set[str]] = {n["id"]: set(n.get("depends_on") or []) for n in nodes}
    remaining = list(nodes)
    ordered: list[dict] = []
    while remaining:
        roots = [n for n in remaining if not incoming[n["id"]]]
        if not roots:
            cycle_ids = [n["id"] for n in remaining]
            raise ImportError(f"Cycle detected in compute nodes: {cycle_ids}")
        for r in roots:
            ordered.append(r)
            remaining.remove(r)
            for n in remaining:
                incoming[n["id"]].discard(r["id"])
    return ordered


# ---------------------------------------------------------------------------
# Node type helpers — support both type:compute (legacy) and
# type:agent + x_compute (AWP-compatible, ADR-0091 + ADR-0088 M7)
# ---------------------------------------------------------------------------


def _is_compute_node(n: dict) -> bool:
    """True if *n* represents a Compute Worker stage.

    Accepts:
    - Legacy: type == "compute"
    - AWP-compatible: type == "agent" with x_compute extension field
    """
    return n.get("type") == "compute" or (
        n.get("type") == "agent" and bool(n.get("x_compute"))
    )


def _compute_node_tool_name(n: dict) -> str | None:
    """Extract tool_name from either node format."""
    if n.get("x_compute"):
        return n["x_compute"].get("tool_name")
    return n.get("tool_name")


def _compute_node_params(n: dict) -> dict:
    """Extract params/param_grid from either node format."""
    if n.get("x_compute"):
        xc = n["x_compute"]
        if xc.get("param_grid"):
            return {"param_grid": xc["param_grid"]}
        return {"params": xc.get("params", {})}
    return {"params": n.get("params", {}), "param_grid": n.get("param_grid", {})}


def _compute_node_budget(n: dict) -> dict:
    """Extract budget from either node format."""
    if n.get("x_compute"):
        return n["x_compute"].get("budget", {})
    return n.get("budget", {})


# ---------------------------------------------------------------------------
# AWPImporter
# ---------------------------------------------------------------------------


class AWPImporter:
    """Import an awpkg bundle and convert it to a PipelineManifest.

    The bundle is either a zip archive or an unpacked directory following
    the standard awpkg layout.  All install_* methods are explicit — none
    are called automatically.
    """

    def __init__(self, package_path: Path) -> None:
        self._src = Path(package_path)
        self._tmpdir: Path | None = None
        self._root: Path = self._unpack()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _unpack(self) -> Path:
        """Return the root directory of the unpacked bundle."""
        if self._src.is_dir():
            return self._src
        if zipfile.is_zipfile(self._src):
            self._tmpdir = Path(tempfile.mkdtemp(prefix="awpkg_"))
            with zipfile.ZipFile(self._src, "r") as zf:
                zf.extractall(self._tmpdir)
            # If the zip contains a single top-level directory, use it.
            entries = list(self._tmpdir.iterdir())
            if len(entries) == 1 and entries[0].is_dir():
                return entries[0]
            return self._tmpdir
        raise ImportError(f"package_path {self._src} is neither a directory nor a zip")

    def __del__(self) -> None:
        if self._tmpdir and self._tmpdir.exists():
            try:
                shutil.rmtree(self._tmpdir)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Internal readers
    # ------------------------------------------------------------------

    def _read_yaml(self, rel: str) -> dict:
        path = self._root / rel
        if not path.exists():
            raise ImportError(f"Missing required file: {rel}")
        return yaml.safe_load(path.read_text("utf-8")) or {}

    def _workflow_raw(self) -> dict:
        return self._read_yaml("src/workflow.awp.yaml")

    def _all_nodes(self) -> list[dict]:
        raw = self._workflow_raw()
        # Support both: orchestration.graph (standard AWP) and dag.nodes (legacy)
        graph = (raw.get("orchestration") or {}).get("graph")
        if graph is not None:
            return list(graph)
        return list((raw.get("dag") or {}).get("nodes") or [])

    def _compute_nodes(self) -> list[dict]:
        return [n for n in self._all_nodes() if (_is_compute_node(n))]

    # ------------------------------------------------------------------
    # validate
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Return a list of validation errors; empty list means valid."""
        errors: list[str] = []

        # Required top-level files
        for req in ("awpkg.yaml", "src/workflow.awp.yaml"):
            if not (self._root / req).exists():
                errors.append(f"Missing required file: {req}")

        # awpkg.yaml parseable
        try:
            meta = self._read_yaml("awpkg.yaml")
        except Exception as exc:
            errors.append(f"awpkg.yaml unreadable: {exc}")
            meta = {}

        # workflow parseable
        try:
            raw = self._workflow_raw()
        except Exception as exc:
            errors.append(f"workflow.awp.yaml unreadable: {exc}")
            return errors  # nothing more to check

        nodes = self._all_nodes()
        node_ids = {n.get("id") for n in nodes if isinstance(n.get("id"), str)}

        # Duplicate IDs
        seen: set[str] = set()
        for n in nodes:
            nid = n.get("id")
            if nid in seen:
                errors.append(f"Duplicate node id: {nid!r}")
            if nid:
                seen.add(nid)

        # compute nodes must have tool_name (supports both type:compute and type:agent+x_compute)
        for n in nodes:
            if (_is_compute_node(n)):
                if not _compute_node_tool_name(n):
                    errors.append(
                        f"compute node {n.get('id')!r} missing tool_name"
                    )

        # All depends_on resolve
        for n in nodes:
            for dep in (n.get("depends_on") or []):
                if dep not in node_ids:
                    errors.append(
                        f"Node {n.get('id')!r} depends_on unknown id {dep!r}"
                    )

        # Must have at least one type:compute node (to_pipeline_manifest would fail otherwise)
        compute = [n for n in nodes if (_is_compute_node(n))]
        if not compute:
            errors.append("workflow.awp.yaml contains no type:compute nodes")

        # Cycle detection — full-graph Kahn over ALL node types (fixes cross-type cycles)
        full_graph_ids = {n.get("id") for n in nodes if n.get("id")}
        incoming: dict[str, set[str]] = {n.get("id"): set() for n in nodes if n.get("id")}
        for n in nodes:
            nid = n.get("id")
            if not nid:
                continue
            for dep in (n.get("depends_on") or []):
                if dep in full_graph_ids:
                    incoming[nid].add(dep)

        remaining = list(nodes)
        while remaining:
            roots = [n for n in remaining if not incoming.get(n.get("id", ""), set())]
            if not roots:
                cycle_ids = [n.get("id") for n in remaining]
                errors.append(f"Cycle detected in DAG involving nodes: {cycle_ids}")
                break
            for r in roots:
                remaining.remove(r)
                rid = r.get("id", "")
                for n in remaining:
                    incoming.get(n.get("id", ""), set()).discard(rid)

        return errors

    # ------------------------------------------------------------------
    # check_compute_worker
    # ------------------------------------------------------------------

    def check_compute_worker(self, tenant_id: str) -> bool:
        """Probe the compute worker socket; warn if unreachable.

        Returns True when the worker appears reachable.
        Uses bridge_transport (ADR-0159 M4) for cross-platform probe:
        AF_UNIX on Linux/macOS, TCP loopback on Windows.
        """
        th = _tenant_home(tenant_id)
        sock_path = th / "compute" / "worker.sock"
        try:
            from bridge_transport import probe_socket  # ADR-0159 M4
            reachable = probe_socket(sock_path, timeout=2.0)
        except ImportError:
            # Fallback: pre-M4 Unix-only probe
            if not sock_path.exists():
                logger.warning(
                    "compute worker socket not found at %s — "
                    "worker may be offline (check spec.compute.enabled)",
                    sock_path,
                )
                return False
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:  # type: ignore[attr-defined]
                    s.settimeout(2.0)
                    s.connect(str(sock_path))
                return True
            except OSError as exc:
                logger.warning("compute worker socket not reachable: %s", exc)
                return False
        if not reachable:
            logger.warning(
                "compute worker not reachable at %s — "
                "worker may be offline (check spec.compute.enabled)",
                sock_path,
            )
        return reachable

    # ------------------------------------------------------------------
    # install_rag_manifests
    # ------------------------------------------------------------------

    def install_rag_manifests(
        self, tenant_id: str, *, force: bool = False
    ) -> list[str]:
        """Install RAG provider manifests from src/rag/*.yaml.

        Returns list of installed provider names.
        """
        rag_src = self._root / "src" / "rag"
        if not rag_src.is_dir():
            return []

        th = _tenant_home(tenant_id)
        dest_dir = th / "global" / "rag"

        installed: list[str] = []
        for src_file in sorted(rag_src.glob("*.yaml")):
            try:
                content = src_file.read_text("utf-8")
                valid, err = self._validate_rag_manifest(content)
                if not valid:
                    logger.warning("Skipping RAG manifest %s: %s", src_file.name, err)
                    continue

                dest = dest_dir / src_file.name
                if dest.exists() and not force:
                    logger.warning(
                        "RAG manifest %s already exists; skipping (pass force=True to override)",
                        dest,
                    )
                    continue

                provider_id = src_file.stem

                # Audit-first
                _emit(tenant_id, "package.installed", {
                    "kind": "rag_manifest",
                    "provider_id": provider_id,
                })
                _atomic_write(dest, content.encode("utf-8"))
                installed.append(provider_id)
                logger.info("Installed RAG manifest: %s → %s", provider_id, dest)

            except Exception as exc:
                logger.error("Failed to install RAG manifest %s: %s", src_file.name, exc)

        return installed

    @staticmethod
    def _validate_rag_manifest(content: str) -> tuple[bool, str | None]:
        try:
            data = yaml.safe_load(content)
            if not isinstance(data, dict):
                return False, "not a dict"
            for req in ("apiVersion", "kind", "metadata", "spec"):
                if req not in data:
                    return False, f"missing field: {req}"
            if not (data.get("metadata") or {}).get("name"):
                return False, "missing metadata.name"
            if not (data.get("spec") or {}).get("retrieval"):
                return False, "missing spec.retrieval"
            return True, None
        except yaml.YAMLError as exc:
            return False, f"YAML error: {exc}"

    # ------------------------------------------------------------------
    # install_fabric_datasources
    # ------------------------------------------------------------------

    def install_fabric_datasources(
        self, tenant_id: str, *, force: bool = False
    ) -> list[str]:
        """Install input datasource manifests from src/datasources/input/*.json."""
        return self._install_datasources(
            tenant_id,
            src_subdir="src/datasources/input",
            role_tag=None,
            force=force,
        )

    def install_output_datasources(
        self, tenant_id: str, *, force: bool = False
    ) -> list[str]:
        """Install output datasource manifests from src/datasources/output/*.json."""
        return self._install_datasources(
            tenant_id,
            src_subdir="src/datasources/output",
            role_tag="role:output",
            force=force,
        )

    def _install_datasources(
        self,
        tenant_id: str,
        src_subdir: str,
        role_tag: str | None,
        *,
        force: bool = False,
    ) -> list[str]:
        src_dir = self._root / src_subdir
        if not src_dir.is_dir():
            return []

        th = _tenant_home(tenant_id)
        dest_dir = th / "datasource_connections"
        installed: list[str] = []

        for src_file in sorted(src_dir.glob("*.json")):
            try:
                raw = json.loads(src_file.read_text("utf-8"))
            except Exception as exc:
                logger.error("Cannot parse datasource manifest %s: %s", src_file.name, exc)
                continue

            # Strip vault_path — never restore it from a bundle
            auth = raw.get("auth") or {}
            if "vault_path" in auth:
                del auth["vault_path"]
                raw["auth"] = auth

            # Residency check: source.region must be present
            region = (raw.get("source") or {}).get("region")
            if not region:
                raise ResidencyViolation(
                    f"datasource {src_file.name}: source.region is required "
                    "for data-residency compliance"
                )

            # Inject role tag if required
            if role_tag:
                tags: list[str] = raw.get("tags") or []
                if role_tag not in tags:
                    tags.append(role_tag)
                raw["tags"] = tags

            name = raw.get("name") or src_file.stem
            dest = dest_dir / f"{name}.json"

            if dest.exists() and not force:
                logger.warning(
                    "Datasource manifest %s already exists; skipping (pass force=True)",
                    dest,
                )
                continue

            content = (json.dumps(raw, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")

            # Audit-first
            _emit(tenant_id, "datasource.registered", {
                "name": name,
                "region": region,
                "role": role_tag or "input",
            })
            _atomic_write(dest, content)
            installed.append(name)
            logger.info("Installed datasource: %s → %s", name, dest)

        return installed

    # ------------------------------------------------------------------
    # install_custom_adapters
    # ------------------------------------------------------------------

    def install_custom_adapters(
        self, tenant_id: str, *, force: bool = False
    ) -> list[str]:
        """AST-check then install custom datasource adapters."""
        return self._install_python_files(
            tenant_id,
            src_subdir="src/datasource_adapters",
            dest_subdir="datasource_adapters",
            event_kind="datasource_adapter",
            force=force,
        )

    # ------------------------------------------------------------------
    # install_ml_backends
    # ------------------------------------------------------------------

    def install_ml_backends(
        self, tenant_id: str, *, force: bool = False
    ) -> list[str]:
        """AST-check then install ML backend plugins."""
        return self._install_python_files(
            tenant_id,
            src_subdir="src/ml_backends",
            dest_subdir="compute_backends",
            event_kind="ml_backend",
            force=force,
        )

    def _install_python_files(
        self,
        tenant_id: str,
        src_subdir: str,
        dest_subdir: str,
        event_kind: str,
        *,
        force: bool = False,
    ) -> list[str]:
        src_dir = self._root / src_subdir
        if not src_dir.is_dir():
            return []

        th = _tenant_home(tenant_id)
        dest_dir = th / dest_subdir
        installed: list[str] = []

        for src_file in sorted(src_dir.glob("*.py")):
            source = src_file.read_text("utf-8")
            violations = _ast_security_check(source, src_file.name)
            if violations:
                for v in violations:
                    logger.error("Security violation in %s: %s", src_file.name, v)
                _emit(tenant_id, "package.install_denied", {
                    "kind": event_kind,
                    "filename": src_file.name,
                    "violation_count": len(violations),
                })
                continue

            dest = dest_dir / src_file.name
            if dest.exists() and not force:
                logger.warning(
                    "%s/%s already exists; skipping (pass force=True)",
                    dest_subdir,
                    src_file.name,
                )
                continue

            # Audit-first
            _emit(tenant_id, "package.installed", {
                "kind": event_kind,
                "filename": src_file.name,
            })
            _atomic_write(dest, source.encode("utf-8"))
            installed.append(src_file.stem)
            logger.info("Installed %s: %s → %s", event_kind, src_file.name, dest)

        return installed

    # ------------------------------------------------------------------
    # restore_watermarks
    # ------------------------------------------------------------------

    def restore_watermarks(
        self, tenant_id: str, *, force: bool = False
    ) -> list[str]:
        """Restore datasource checkpoints from src/datasource_checkpoints/*.json.

        Only restores a checkpoint when the bundled timestamp is newer than
        the on-disk version (or when force=True).
        """
        ckpt_src = self._root / "src" / "datasource_checkpoints"
        if not ckpt_src.is_dir():
            return []

        th = _tenant_home(tenant_id)
        dest_dir = th / "datasource_checkpoints"
        restored: list[str] = []

        for src_file in sorted(ckpt_src.glob("*.json")):
            try:
                bundled = json.loads(src_file.read_text("utf-8"))
            except Exception as exc:
                logger.error("Cannot parse checkpoint %s: %s", src_file.name, exc)
                continue

            name = src_file.stem
            dest = dest_dir / src_file.name

            if dest.exists() and not force:
                try:
                    existing = json.loads(dest.read_text("utf-8"))
                    bundled_ts_raw = bundled.get("ts") or bundled.get("timestamp") or 0
                    existing_ts_raw = existing.get("ts") or existing.get("timestamp") or 0
                    # Timestamps may be numeric or ISO-8601 strings; parse defensively.
                    def _to_float(v: object) -> float:
                        try:
                            return float(v)  # type: ignore[arg-type]
                        except (TypeError, ValueError):
                            pass
                        # ISO-8601 fallback: parse via datetime
                        try:
                            import datetime as _dt
                            s = str(v).rstrip("Z")
                            return _dt.datetime.fromisoformat(s).timestamp()
                        except Exception:
                            return 0.0

                    if _to_float(bundled_ts_raw) <= _to_float(existing_ts_raw):
                        logger.info(
                            "Checkpoint %s not newer than on-disk version; skipping",
                            name,
                        )
                        continue
                except Exception as exc:
                    logger.warning("Could not compare timestamps for %s: %s — skipping", name, exc)
                    continue  # safe default: skip if comparison fails

            content = (json.dumps(bundled, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")

            # Audit-first
            _emit(tenant_id, "compute.checkpoint_written", {
                "checkpoint_name": name,
                "source": "awpkg_restore",
            })
            _atomic_write(dest, content)
            restored.append(name)
            logger.info("Restored watermark: %s → %s", name, dest)

        return restored

    # ------------------------------------------------------------------
    # install_all
    # ------------------------------------------------------------------

    def install_all(
        self, tenant_id: str, *, restore_watermarks: bool = False
    ) -> InstallResult:
        """Convenience wrapper: calls all install_* methods."""
        result = InstallResult()
        result.rag_providers = self.install_rag_manifests(tenant_id)
        result.input_datasources = self.install_fabric_datasources(tenant_id)
        result.output_datasources = self.install_output_datasources(tenant_id)
        result.custom_adapters = self.install_custom_adapters(tenant_id)
        result.ml_backends = self.install_ml_backends(tenant_id)
        if restore_watermarks:
            result.watermarks_restored = self.restore_watermarks(tenant_id)
        return result

    # ------------------------------------------------------------------
    # to_pipeline_manifest
    # ------------------------------------------------------------------

    def to_pipeline_manifest(self, tenant_id: str) -> Any:
        """Reconstruct a PipelineManifest from workflow.awp.yaml.

        Returns a PipelineManifest (from
        core/compute/corvin_compute/pipeline/manifest.py).

        Each type:compute node in topological order maps to one StageSpec:
          - id          → stage_id
          - tool_name   → tool_name
          - params      → param_grid (single-point: {k: [v]})
          - param_grid  → param_grid (as-is, takes precedence over params)
          - budget.max_iterations → budget.max_iterations
          - fabric_datasources[0].name → inputs["data_source"]
          - additional fabric_datasources → inputs["data_source_N"]
        steering_gate=True when any type:quality_gate node exists.
        """
        # Lazy import so the module can be used without the full compute stack
        # installed (e.g. in tests that stub this path).
        try:
            repo_core = _REPO / "core" / "compute"
            if str(repo_core) not in sys.path:
                sys.path.insert(0, str(repo_core))
            from corvin_compute.pipeline.manifest import (  # type: ignore
                PipelineManifest,
                StageSpec,
                new_pipeline_id,
            )
        except ImportError as exc:
            raise ImportError(
                "corvin_compute.pipeline.manifest not importable — "
                "is the compute plugin installed?"
            ) from exc

        raw = self._workflow_raw()
        all_nodes = self._all_nodes()

        has_quality_gate = any(n.get("type") == "quality_gate" for n in all_nodes)
        compute_nodes_unordered = [n for n in all_nodes if (_is_compute_node(n))]

        if not compute_nodes_unordered:
            raise ImportError("workflow.awp.yaml contains no type:compute nodes")

        ordered = _topo_sort_compute_nodes(compute_nodes_unordered)

        stages: list[StageSpec] = []
        for node in ordered:
            stage_id = str(node["id"])
            # Support both type:compute (legacy) and type:agent + x_compute
            tool_name = str(_compute_node_tool_name(node) or "")
            if not tool_name:
                raise ImportError(
                    f"type:compute node {stage_id!r} has no tool_name"
                )

            # param_grid / params — use helper for both node formats
            extracted = _compute_node_params(node)
            explicit_grid = extracted.get("param_grid") or {}
            params = extracted.get("params") or {}
            if explicit_grid:
                param_grid = {str(k): v for k, v in explicit_grid.items()}
            else:
                # Single-point grid: {k: [v]}
                param_grid = {str(k): [v] for k, v in params.items()}

            # Budget — use helper for both node formats
            node_budget_raw = _compute_node_budget(node)
            budget: dict[str, Any] = {}
            if "max_iterations" in node_budget_raw:
                budget["max_iterations"] = int(node_budget_raw["max_iterations"])

            # Inputs from fabric_datasources (top-level or inside x_compute)
            xc = node.get("x_compute") or {}
            fabric_ds = xc.get("fabric_datasources") or node.get("fabric_datasources") or []
            inputs: dict[str, str] = {}
            for idx, ds in enumerate(fabric_ds):
                ds_name = ds.get("name") or ""
                if not ds_name:
                    continue
                if idx == 0:
                    inputs["data_source"] = ds_name
                else:
                    inputs[f"data_source_{idx}"] = ds_name

            stages.append(
                StageSpec(
                    stage_id=stage_id,
                    tool_name=tool_name,
                    strategy=str(node.get("strategy") or "grid"),
                    param_grid=param_grid,
                    budget=budget,
                    inputs=inputs,
                    outputs=list(node.get("outputs") or []),
                    sensitive_fields=list(node.get("sensitive_fields") or []),
                )
            )

        pipeline_id = new_pipeline_id()
        return PipelineManifest(
            pipeline_id=pipeline_id,
            tenant_id=tenant_id,
            stages=stages,
            steering_gate=has_quality_gate,
        )
