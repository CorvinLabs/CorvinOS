"""acs_engine_adapter.py — ACS engine bridge for corvin-workflow CLI (ADR-0104 M7).

Makes ACS a selectable second compute engine alongside L25 Compute Worker.
Engine selection is spec-driven:

    orchestration.engine: delegation_loop  →  dispatched here (ACS)
    orchestration.engine: dag              →  existing DAGRunner (L26)

Storage layout (mirrors L25 compute runs directory):
    <corvin_home>/tenants/<tid>/global/acs/runs/<run_id>/
        manifest.json   — run_id, workflow_id, status, started_at, duration_s
        result.json     — full ACSResult (status, summary, artifacts, error)

MUST NOT import anthropic — CI AST lint enforces.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_SHARED = Path(__file__).resolve().parent
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

# Pre-compute the forge path once so both _corvin_home() and _enforce_acs_compute_quota
# can import forge.paths without per-call sys.path manipulation.
_FORGE_P = str(Path(__file__).resolve().parents[2] / "forge")
if _FORGE_P not in sys.path:
    sys.path.insert(0, _FORGE_P)


def _corvin_home() -> Path:
    """Resolve corvin_home via forge.paths (repo-root aware) with env-var fallback.

    Uses forge.paths.corvin_home() so that repo-root .corvin detection fires when
    CORVIN_HOME is unset — matching the same resolution used for the quota counter
    so run manifests and quota files always land in the same directory tree.
    """
    try:
        from forge import paths as _fp  # type: ignore
        return _fp.corvin_home()
    except ImportError:
        env = os.environ.get("CORVIN_HOME")
        return Path(env) if env else Path.home() / ".corvin"


def _acs_runs_dir(tenant_id: str) -> Path:
    return _corvin_home() / "tenants" / tenant_id / "global" / "acs" / "runs"


def _write_json_atomic(path: Path, data: dict) -> None:
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(raw)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _enforce_acs_compute_quota(tenant_id: str, run_id: "str | None") -> "dict[str, Any] | None":
    """Charge one compute unit against the persistent per-UTC-day counter.

    ADR-0149 WF-CLI-ACS-01: run_acs_workflow is the single chokepoint every ACS
    caller (console route, /workflow run CLI, scheduler) funnels through. Charging
    the daily compute_units_per_day counter here (not only at the console HTTP
    route) closes the CLI/scheduler bypass. Returns a failed status dict on
    over-quota OR when the license module cannot be imported (fail-CLOSED, ADR-0150
    LIC-ACS-CQ-IMPORT — matches the sibling gates and the metering-map invariant
    that a missing/shadowed license module must DENY, not run unmetered); None to
    proceed. Transient I/O is swallowed by increment_and_check (operational fail-open).
    """
    try:
        _lic_root = str(Path(__file__).resolve().parents[2])  # operator/
        if _lic_root not in sys.path:
            sys.path.insert(0, _lic_root)
        from license.compute_quota import increment_and_check as _cq_inc  # type: ignore
        from license.limits import LicenseLimitError as _CQErr  # type: ignore
        from license.validator import load_license_from_env as _acs_load_lic  # type: ignore
        # Load license so quota limits reflect the actual tier, not FREE_TIER defaults.
        # The CLI/scheduler process never calls load_license_from_env() at startup;
        # without this call _ACTIVE_LICENSE is None and get_limit() falls back to
        # FREE_TIER (1/day). Idempotent via _LICENSE_INITIALIZED guard.
        _acs_load_lic()
    except ImportError:
        # Fail-CLOSED: the license package is part of the repo (Apache core); a
        # failed import means a removed/shadowed module, not a legitimate state.
        return {
            "run_id": run_id or "unknown",
            "status": "failed",
            "error": "compute quota enforcement unavailable (fail-closed)",
            "engine": "acs",
            "duration_s": 0.0,
        }
    # _corvin_home() now uses forge.paths.corvin_home() (module-level _FORGE_P set at
    # import time), so repo-root .corvin detection is consistent with run manifest storage.
    _ch = _corvin_home()
    try:
        _cq_inc(_ch, channel="acs", chat_key=f"acs:{tenant_id}:{run_id or 'run'}")
    except _CQErr as exc:  # type: ignore[misc]
        return {
            "run_id": run_id or "unknown",
            "status": "failed",
            "error": f"compute_units_per_day exceeded: {exc}",
            "engine": "acs",
            "duration_s": 0.0,
        }
    except Exception:  # noqa: BLE001 — operational error already swallowed (fail-open)
        pass
    return None


def run_acs_workflow(
    spec: "dict | str | Path",
    inputs: "dict | None" = None,
    *,
    tenant_id: str = "_default",
    dry_run: bool = False,
    run_id: "str | None" = None,
    budget_override: "dict | None" = None,
    charge_quota: bool = True,
) -> dict[str, Any]:
    """Run an ACS workflow synchronously and return a status dict.

    Parameters
    ----------
    spec:
        Workflow spec as a dict, or a path (str/Path) to an .awp.yaml file.
    inputs:
        Key-value inputs merged into ``state.initial``.
    tenant_id:
        Tenant scope for audit events and run storage.
    dry_run:
        Validate only; do not spawn workers or manager.
    run_id:
        Optional fixed run_id (generated if omitted).
    budget_override:
        Optional budget dict merged over the spec's delegation_loop.budget.

    Returns
    -------
    dict with keys: run_id, status, summary, artifacts, error, engine, duration_s.
    """
    try:
        from acs_runtime import ACSRuntime, BudgetEnvelope  # type: ignore
    except ImportError as e:
        return {
            "run_id": run_id or "unknown",
            "status": "failed",
            "error": f"acs_runtime not importable: {e}",
            "engine": "acs",
            "duration_s": 0.0,
        }

    # L44 acceptable-use (ADR-0143 / ADR-0158): the house-rules gate is enforced
    # ONCE downstream inside ACSRuntime.run (the single universal chokepoint every
    # ACS caller funnels through — including the corvin-workflow CLI __main__ that
    # bypasses this wrapper). It is fail-closed + audit-first there. Do NOT add a
    # second check_l44 call here — that would double-classify and emit a duplicate
    # house_rules.* L16 event.

    # ADR-0149 WF-CLI-ACS-01: charge the daily compute quota at this chokepoint so
    # the CLI (/workflow run) and scheduler paths cannot bypass it. dry_run spawns
    # no workers → exempt. The console route already charges (and returns 402), so
    # it passes charge_quota=False to avoid double-counting.
    if charge_quota and not dry_run:
        _cq_block = _enforce_acs_compute_quota(tenant_id, run_id)
        if _cq_block is not None:
            return _cq_block

    rt = ACSRuntime(tenant_id=tenant_id)
    t0 = time.time()
    try:
        result = asyncio.run(
            rt.run(
                spec,
                inputs=inputs,
                dry_run=dry_run,
                run_id=run_id,
                budget_override=budget_override,
            )
        )
    except Exception as e:  # noqa: BLE001
        return {
            "run_id": run_id or "unknown",
            "status": "failed",
            "error": f"{type(e).__name__}: {e}",
            "engine": "acs",
            "duration_s": round(time.time() - t0, 3),
        }

    completed_at = time.time()
    duration_s = round(completed_at - t0, 3)
    out: dict[str, Any] = {
        "run_id": result.run_id,
        "status": result.status,
        "summary": result.summary,
        "final_output": result.final_output,
        "error": result.error,
        "engine": "acs",
        "duration_s": duration_s,
        "workflow_id": result.workflow_id,
        "iterations": result.iterations,
        "workers_spawned": result.workers_spawned,
        "budget_breach": result.budget_breach,
    }

    if not dry_run:
        runs_dir = _acs_runs_dir(tenant_id)
        # The ACS runtime stores run data (subtasks, workers, iterations,
        # gate_results) in a session-scoped directory.  The console's list/get
        # endpoints scan the tenant-global index at global/acs/runs/<run_id>/.
        # We write a thin manifest to the global index and embed a "run_dir"
        # pointer so get_acs_run() and export_acs_run_as_awpkg() can follow it
        # to the actual data.
        actual_run_dir = result.run_dir if result.run_dir is not None else runs_dir / result.run_id
        global_run_dir = runs_dir / result.run_id

        _be = BudgetEnvelope()
        if budget_override:
            _int_fields = {
                "max_loops", "max_workers_per_iteration", "max_wall_time",
                "max_total_workers", "max_rejected_completions", "max_depth",
            }
            for k, v in budget_override.items():
                if not hasattr(_be, k):
                    continue
                try:
                    setattr(_be, k, int(v) if k in _int_fields else v)
                except (TypeError, ValueError):
                    # This is a SECOND, display-only re-parse of
                    # budget_override — the real workflow run above (line
                    # ~187) already applied the properly-validated merge
                    # inside ACSRuntime.run() and has ALREADY EXECUTED
                    # (workers spawned, quota charged) by the time this
                    # code runs. A non-numeric value here previously raised
                    # uncaught, propagating out of this function entirely —
                    # so the run's manifest/result.json (written just below)
                    # never got persisted, even though the run genuinely
                    # happened and its audit events exist: a dangling audit
                    # trail with no corresponding run-list entry
                    # (adversarial review finding). Skip the malformed
                    # field for display purposes rather than crash after
                    # the real work is already done.
                    log.warning(
                        "acs_engine_adapter: ignoring non-numeric budget_override "
                        "display field %r=%r for manifest", k, v,
                    )

        manifest = {
            "run_id": result.run_id,
            "workflow_id": result.workflow_id,
            "status": result.status,
            "engine": "acs",
            "started_at": t0,
            "completed_at": completed_at,
            "duration_s": duration_s,
            "iterations": result.iterations,
            "workers_spawned": result.workers_spawned,
            "budget_breach": result.budget_breach,
            "run_dir": str(actual_run_dir),
            "max_loops": _be.max_loops,
            "max_workers_per_iteration": _be.max_workers_per_iteration,
            "max_wall_time": _be.max_wall_time,
        }
        # Write global index entry (always global path — this is what list/get scan).
        _write_json_atomic(global_run_dir / "manifest.json", manifest)
        # Write data files to actual run dir (may be same as global_run_dir).
        _write_json_atomic(actual_run_dir / "manifest.json", manifest)
        _write_json_atomic(actual_run_dir / "result.json", {
            "run_id": result.run_id,
            "workflow_id": result.workflow_id,
            "status": result.status,
            "summary": result.summary,
            "final_output": result.final_output,
            "error": result.error,
            "iterations": result.iterations,
            "workers_spawned": result.workers_spawned,
            "budget_breach": result.budget_breach,
            "elapsed_s": result.elapsed_s,
        })

    return out


def list_acs_runs(tenant_id: str = "_default") -> list[dict[str, Any]]:
    """List ACS runs for a tenant, newest first."""
    runs_dir = _acs_runs_dir(tenant_id)
    if not runs_dir.exists():
        return []
    runs: list[dict[str, Any]] = []
    try:
        for run_dir in runs_dir.iterdir():
            if not run_dir.is_dir():
                continue
            manifest_path = run_dir / "manifest.json"
            if not manifest_path.exists():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                runs.append(manifest)
            except Exception:  # noqa: BLE001
                continue
    except OSError:
        pass
    return sorted(runs, key=lambda r: r.get("started_at", 0), reverse=True)


def _read_dir_jsons(directory: Path) -> list[dict[str, Any]]:
    """Read all *.json files from a directory, sorted by name, ignore failures."""
    items: list[dict[str, Any]] = []
    if not directory.exists():
        return items
    for p in sorted(directory.iterdir()):
        if p.suffix != ".json":
            continue
        try:
            items.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            continue
    return items


def get_acs_run(run_id: str, tenant_id: str = "_default") -> dict[str, Any] | None:
    """Get manifest + result + per-iteration detail for a single ACS run."""
    runs_dir = _acs_runs_dir(tenant_id)
    index_dir = runs_dir / run_id
    if not index_dir.exists():
        return None
    manifest_path = index_dir / "manifest.json"
    try:
        manifest = (json.loads(manifest_path.read_text(encoding="utf-8"))
                    if manifest_path.exists() else {})
        # Follow run_dir pointer to the actual data directory (may differ from
        # the global index entry when the runtime used a session-scoped path).
        data_dir = Path(manifest["run_dir"]) if manifest.get("run_dir") else index_dir
        result_path = data_dir / "result.json"
        result = (json.loads(result_path.read_text(encoding="utf-8"))
                  if result_path.exists() else {})
        iterations = _read_dir_jsons(data_dir / "iterations")
        gate_results = _read_dir_jsons(data_dir / "gate_results")
        workers = _read_dir_jsons(data_dir / "workers")
        has_subtasks = (data_dir / "subtasks").exists()
        return {
            "manifest": manifest,
            "result": result,
            "iterations": iterations,
            "gate_results": gate_results,
            "workers": workers,
            "graph_exportable": has_subtasks,
        }
    except Exception:  # noqa: BLE001
        return None


def export_acs_run_as_awpkg(
    run_id: str,
    tenant_id: str = "_default",
    *,
    mode: str = "dag",
    description: str = "",
) -> dict[str, Any]:
    """Build and return an AWPKG archive for the given ACS run.

    Parameters
    ----------
    run_id:
        The ACS run identifier.
    tenant_id:
        Tenant scope; used to locate the run directory.
    mode:
        ``"dag"`` — deterministic DAG replay.
        ``"template"`` — adaptive delegation_loop template.
    description:
        Human-readable description override for the generated workflow.

    Returns
    -------
    dict with keys:

        * ``ok`` — bool; False on any error.
        * ``bytes`` — raw AWPKG ZIP bytes (only when ok=True).
        * ``filename`` — suggested download filename.
        * ``node_count`` — number of graph nodes.
        * ``error`` — error message (only when ok=False).
    """
    try:
        from acs_graph_builder import ACSGraphBuilder, build_awpkg_bytes  # type: ignore
    except ImportError as exc:
        return {"ok": False, "error": f"acs_graph_builder not importable: {exc}",
                "bytes": b"", "filename": "", "node_count": 0}

    runs_dir = _acs_runs_dir(tenant_id)
    index_dir = runs_dir / run_id
    if not index_dir.exists():
        return {"ok": False, "error": f"run not found: {run_id}",
                "bytes": b"", "filename": "", "node_count": 0}

    manifest_path = index_dir / "manifest.json"
    manifest = (json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest_path.exists() else {})
    run_dir = Path(manifest["run_dir"]) if manifest.get("run_dir") else index_dir

    builder = ACSGraphBuilder(run_dir)
    graph = builder.build()
    if graph is None:
        return {"ok": False, "error": "graph builder returned None",
                "bytes": b"", "filename": "", "node_count": 0}

    short = run_id[:8] if len(run_id) >= 8 else run_id
    prefix = "discovered" if mode == "dag" else "template"
    filename = f"acs-{prefix}-{short}.awpkg"

    pkg_bytes = build_awpkg_bytes(
        graph,
        mode=mode,
        description=description,
        tenant_id=tenant_id,
    )
    return {
        "ok": True,
        "bytes": pkg_bytes,
        "filename": filename,
        "node_count": len(graph.nodes),
        "graph_exportable": not graph.is_empty(),
        "quality_aggregate": graph.quality_aggregate,
    }
