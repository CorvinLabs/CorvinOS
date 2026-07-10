"""Compute Worker (Layer 25, ADR-0013) — read-only viewer.

Worker socket: ``<corvin_home>/tenants/<tid>/compute/worker.sock``
Run artifacts: ``<corvin_home>/tenants/<tid>/compute/runs/<run_id>/``

Phase F ships:
  * worker socket reachability probe
  * run-list (manifest.json + summary.json projection)
  * single-run drill-down (full summary + iteration count)

Phase F deliberately does NOT submit runs from the console — the
compute worker must be opt-in-bootstrapped per tenant
(``spec.compute.enabled: true`` in tenant.corvin.yaml + worker
process running). The console surfaces what is there; the operator
decides when to engage compute.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import time
from html import escape as _html_escape
from pathlib import Path
from typing import Annotated, Any

def _read_system_resources() -> dict[str, Any]:
    """Read RAM, CPU, disk from /proc (no external deps)."""
    resources: dict[str, Any] = {}
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            mem: dict[str, int] = {}
            for line in fh:
                if ":" in line:
                    k, v = line.split(":", 1)
                    mem[k.strip()] = int(v.split()[0])
        total_kb  = mem.get("MemTotal", 0)
        avail_kb  = mem.get("MemAvailable", 0)
        used_kb   = total_kb - avail_kb
        resources["ram"] = {
            "total_gb": round(total_kb / 1_048_576, 1),
            "used_gb":  round(used_kb  / 1_048_576, 1),
            "free_gb":  round(avail_kb / 1_048_576, 1),
            "used_pct": round(used_kb / max(total_kb, 1) * 100, 1),
        }
    except OSError:
        resources["ram"] = None
    try:
        # Use /proc/loadavg (1-min average normalised by core count) —
        # sequential /proc/stat reads without sleep always produce dt=0 jiffies
        # and are therefore unreliable; loadavg is the correct no-sleep proxy.
        with open("/proc/loadavg", encoding="ascii") as fh:
            load1 = float(fh.read().split()[0])
        cores = os.cpu_count() or 1
        used_pct = round(min(100.0, (load1 / cores) * 100), 1)
        resources["cpu"] = {
            "used_pct": used_pct,
            "core_count": cores,
        }
    except OSError:
        resources["cpu"] = None
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        free  = st.f_bavail * st.f_frsize
        resources["disk"] = {
            "total_gb": round(total / 1_073_741_824, 1),
            "free_gb":  round(free  / 1_073_741_824, 1),
            "used_pct": round((1 - free / max(total, 1)) * 100, 1),
        }
    except OSError:
        resources["disk"] = None
    return resources

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import auth as session_auth
from .. import audit as console_audit
from ..deps import require_csrf, require_session, verify_reauth
from ..utils import read_json_or_none as _read_json

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_FORGE_PATH = _REPO / "operator" / "forge"
if str(_FORGE_PATH) not in sys.path:
    sys.path.insert(0, str(_FORGE_PATH))

from forge import paths as _forge_paths  # noqa: E402

_OPERATOR = _REPO / "operator"
if str(_OPERATOR) not in sys.path:
    sys.path.insert(0, str(_OPERATOR))
try:
    from license.compute_quota import increment_and_check as _cq_increment  # type: ignore[import]
    from license.compute_quota import get_today_count as _cq_today          # type: ignore[import]
    from license.validator import get_limit as _lic_get_limit               # type: ignore[import]
    from license.validator import active_tier as _lic_active_tier           # type: ignore[import]
    from license.limits import LicenseLimitError as _LicLimitError          # type: ignore[import]
    _COMPUTE_QUOTA_OK = True
except ImportError:
    _cq_increment = None   # type: ignore[assignment]
    _cq_today = None       # type: ignore[assignment]
    _lic_active_tier = None  # type: ignore[assignment]
    # ADR-0144 F-01-bis: do NOT fail-open to ``lambda f: None`` (None = unlimited).
    # Mirror the fail-closed fallback used by the five sister routes (space.py,
    # workflows.py, custom_provider.py, data_sources.py, a2a_pair.py): fall back
    # to the FREE_TIER cap (compute_units_per_day = 1) so a missing license module
    # degrades to the free quota, never to unmetered compute. If limits.py is also
    # absent, an empty dict yields None for every key — self_test flags this CRITICAL
    # at boot (license modules importable is a load-bearing check).
    try:
        from license.limits import FREE_TIER as _FREE_TIER  # type: ignore[import]
    except ImportError:
        _FREE_TIER = {}  # type: ignore[assignment]
    _lic_get_limit = _FREE_TIER.get  # type: ignore[assignment]
    _LicLimitError = Exception  # type: ignore[assignment,misc]
    _COMPUTE_QUOTA_OK = False

try:
    import yaml as _yaml
    _YAML_OK = True
except ImportError:
    _yaml = None  # type: ignore[assignment]
    _YAML_OK = False

router = APIRouter()

# ── Tenant YAML helpers ───────────────────────────────────────────────────

def _tenant_yaml_path(tid: str) -> Path:
    return _forge_paths.tenant_global_dir(tid) / "tenant.corvin.yaml"

def _read_tenant_yaml(tid: str) -> dict[str, Any]:
    p = _tenant_yaml_path(tid)
    if not p.exists() or not _YAML_OK:
        return {}
    try:
        return _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}

def _write_tenant_yaml(tid: str, data: dict[str, Any]) -> None:
    p = _tenant_yaml_path(tid)
    p.parent.mkdir(parents=True, exist_ok=True)
    import tempfile
    raw = _yaml.dump(data, default_flow_style=False, allow_unicode=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(raw)
        os.chmod(tmp, 0o600)  # mode before replace — no world-readable window
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

def _compute_dir(tid: str) -> Path:
    return _forge_paths.tenant_home(tid) / "compute"

def _socket_path(tid: str) -> Path:
    return _compute_dir(tid) / "worker.sock"

def _runs_dir(tid: str) -> Path:
    return _compute_dir(tid) / "runs"

def _runs_today_count(tid: str) -> int:
    """Count runs started since local midnight (manifest.started_at, mtime fallback)."""
    import datetime as _dt
    midnight = _dt.datetime.combine(_dt.date.today(), _dt.time()).timestamp()
    runs_root = _runs_dir(tid)
    if not runs_root.exists():
        return 0
    count = 0
    try:
        for run_dir in runs_root.iterdir():
            if not run_dir.is_dir():
                continue
            manifest = _read_json(run_dir / "manifest.json") or {}
            started = manifest.get("started_at")
            if started is not None:
                try:
                    if float(started) >= midnight:
                        count += 1
                except (TypeError, ValueError):
                    pass
            else:
                try:
                    if run_dir.stat().st_mtime >= midnight:
                        count += 1
                except OSError:
                    pass
    except OSError:
        pass
    return count

def _probe_socket(path: Path, timeout_s: float = 0.3) -> dict[str, Any]:
    """Best-effort connectivity check. Returns shape:
    ``{exists: bool, reachable: bool, error: str|None}``."""
    if not path.exists():
        return {"exists": False, "reachable": False, "error": None}
    if not hasattr(socket, "AF_UNIX"):
        # Windows has no AF_UNIX — the compute worker uses TCP-loopback there
        # (ADR-0159 M4); a unix-socket probe is simply not applicable.
        return {"exists": True, "reachable": False, "error": "AF_UNIX unsupported on this platform"}
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout_s)
    try:
        sock.connect(str(path))
        sock.close()
        return {"exists": True, "reachable": True, "error": None}
    except OSError as e:
        return {"exists": True, "reachable": False, "error": str(e)}

@router.get("/compute")
def compute_status(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Worker reachability + per-run summaries."""
    tid = rec.tenant_id
    compute_root = _compute_dir(tid)
    enabled_on_disk = compute_root.exists()
    sock_status = _probe_socket(_socket_path(tid))

    runs: list[dict[str, Any]] = []
    runs_root = _runs_dir(tid)
    if runs_root.exists():
        try:
            for run_dir in sorted(runs_root.iterdir(),
                                   key=lambda p: p.stat().st_mtime if p.exists() else 0,
                                   reverse=True):
                if not run_dir.is_dir():
                    continue
                manifest = _read_json(run_dir / "manifest.json") or {}
                summary  = _read_json(run_dir / "summary.json") or {}
                iters_dir = run_dir / "iterations"
                iter_count = 0
                if iters_dir.is_dir():
                    try:
                        iter_count = sum(1 for f in iters_dir.iterdir()
                                          if f.suffix == ".json")
                    except OSError:
                        pass
                runs.append({
                    "run_id":        run_dir.name,
                    "tool_name":     manifest.get("tool_name"),
                    "strategy":      manifest.get("strategy"),
                    "state":         summary.get("state"),
                    "best_iter":     summary.get("best_iter"),
                    "best_loss":     summary.get("best_loss"),
                    "iterations":    iter_count,
                    "started_at":    manifest.get("started_at"),
                    "convergence":   summary.get("convergence_reason"),
                    "submitted_by":  manifest.get("submitted_by"),
                    "session_id":    manifest.get("session_id"),
                    "session_label": manifest.get("session_label"),
                })
        except OSError:
            pass

    # Pipeline counts
    pipeline_count = 0
    pipelines_root = _compute_dir(tid) / "pipelines"
    if pipelines_root.exists():
        try:
            pipeline_count = sum(1 for p in pipelines_root.iterdir() if p.is_dir())
        except OSError:
            pass

    # HAC counts
    hac_count = 0
    hac_root = _compute_dir(tid) / "hac"
    if hac_root.exists():
        try:
            hac_count = sum(1 for p in hac_root.iterdir() if p.is_dir())
        except OSError:
            pass

    return {
        "tenant_id":      tid,
        "ts":             time.time(),
        "compute_dir_hash": hashlib.sha256(str(compute_root).encode()).hexdigest()[:16],
        "enabled":        enabled_on_disk,
        "worker_socket":  sock_status,
        "run_count":      len(runs),
        "runs":           runs,
        "pipeline_count": pipeline_count,
        "hac_count":      hac_count,
        "system":         _read_system_resources(),
    }

@router.get("/compute/runs/{run_id}")
def compute_run_detail(
    run_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Manifest + full summary for one run."""
    tid = rec.tenant_id
    if "/" in run_id or run_id.startswith(".."):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="invalid run_id",
        )
    run_dir = _runs_dir(tid) / run_id
    if not run_dir.exists():
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"run {run_id!r} not found",
        )
    manifest = _read_json(run_dir / "manifest.json") or {}
    summary  = _read_json(run_dir / "summary.json") or {}

    # Read iterations — sorted by iter number, return (iter, loss) pairs.
    iterations: list[dict[str, Any]] = []
    iters_dir = run_dir / "iterations"
    if iters_dir.is_dir():
        try:
            files = sorted(
                (f for f in iters_dir.iterdir() if f.suffix == ".json"),
                key=lambda f: int(f.stem.split("_")[-1]) if f.stem.split("_")[-1].isdigit() else 0,
            )
            for f in files:
                data = _read_json(f) or {}
                if isinstance(data.get("loss"), (int, float)):
                    iterations.append({
                        "iter": data.get("iter", 0),
                        "loss": float(data["loss"]),
                        "params": data.get("params", {}),
                    })
        except OSError:
            pass

    return {
        "run_id":     run_id,
        "manifest":   manifest,
        "summary":    summary,
        "iterations": iterations,
    }

# ── ADR-0099: Anthropic Batch API — open batch job list ──────────────────

@router.get("/compute/batch/open")
def list_open_batch_jobs(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return all open Anthropic Batch API jobs across all sessions for this tenant.

    Reads <corvin_home>/sessions/<session_key>/compute/open_batches.json.
    Metadata-only: batch_id_prefix (16 chars), job_id, candidate_count,
    submitted_at, session_key. Never full batch_id, never payload.
    """
    tid = rec.tenant_id
    sessions_root = _forge_paths.tenant_home(tid) / "sessions"
    open_jobs: list[dict[str, Any]] = []

    def _dir_mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    if sessions_root.exists():
        try:
            for session_dir in sorted(
                sessions_root.iterdir(),
                key=_dir_mtime,
                reverse=True,
            ):
                state_file = session_dir / "compute" / "open_batches.json"
                if not state_file.exists():
                    continue
                try:
                    entries = json.loads(state_file.read_text(encoding="utf-8"))
                    if not isinstance(entries, list):
                        continue
                    for entry in entries:
                        bid = str(entry.get("batch_id", ""))
                        if not bid:
                            continue
                        open_jobs.append({
                            "job_id":          entry.get("job_id", ""),
                            "batch_id_prefix": bid[:16],
                            "session_key":     session_dir.name,
                            "submitted_at":    entry.get("submitted_at"),
                            "candidate_count": entry.get("candidate_count"),
                            "state":           "running",
                        })
                except (OSError, json.JSONDecodeError, KeyError):
                    pass
        except OSError:
            pass

    return {
        "tenant_id":  tid,
        "open_count": len(open_jobs),
        "jobs":       open_jobs,
    }

# ── Pipeline endpoints ────────────────────────────────────────────────────

def _pipelines_dir(tid: str) -> Path:
    return _compute_dir(tid) / "pipelines"

def _hac_dir(tid: str) -> Path:
    return _compute_dir(tid) / "hac"

def _read_stage_summary(stage_dir: Path) -> dict[str, Any]:
    summary = _read_json(stage_dir / "stage_summary.json") or {}
    iters_dir = stage_dir / "iterations"
    iter_count = 0
    iterations: list[dict[str, Any]] = []
    if iters_dir.is_dir():
        try:
            files = sorted(
                (f for f in iters_dir.iterdir() if f.suffix == ".json"),
                key=lambda f: int(f.stem.split("_")[-1]) if f.stem.split("_")[-1].isdigit() else 0,
            )
            iter_count = len(files)
            for f in files:
                data = _read_json(f) or {}
                if isinstance(data.get("loss"), (int, float)):
                    iterations.append({"iter": data.get("iter", 0), "loss": float(data["loss"])})
        except OSError:
            pass
    # Derive best_loss from iterations if not in summary
    if "best_loss" not in summary and iterations:
        summary["best_loss"] = min(it["loss"] for it in iterations)
    return {**summary, "iter_count": iter_count, "iterations": iterations}

@router.get("/compute/pipelines")
def list_pipelines(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """List all pipeline runs for this tenant."""
    tid = rec.tenant_id
    pipelines_root = _pipelines_dir(tid)
    pipelines: list[dict[str, Any]] = []
    if pipelines_root.exists():
        try:
            for p in sorted(pipelines_root.iterdir(),
                            key=lambda x: x.stat().st_mtime if x.exists() else 0,
                            reverse=True):
                if not p.is_dir():
                    continue
                manifest = _read_json(p / "manifest.json") or {}
                summary = _read_json(p / "pipeline_summary.json") or {}
                pipelines.append({
                    "pipeline_id": p.name,
                    "name": manifest.get("name", p.name),
                    "stages": [s.get("stage_id") for s in manifest.get("stages", [])],
                    "stage_count": len(manifest.get("stages", [])),
                    "state": summary.get("state"),
                    "current_stage_id": summary.get("current_stage_id"),
                    "completed_stages": summary.get("completed_stages", []),
                    "best_losses": summary.get("best_losses", {}),
                    "started_at": manifest.get("started_at"),
                    "submitted_by": manifest.get("submitted_by"),
                    "steering_gate": manifest.get("steering_gate", False),
                })
        except OSError:
            pass
    return {"tenant_id": tid, "pipeline_count": len(pipelines), "pipelines": pipelines}

@router.get("/compute/pipelines/{pipeline_id}")
def pipeline_detail(
    pipeline_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Full pipeline detail including per-stage iterations."""
    tid = rec.tenant_id
    if "/" in pipeline_id or pipeline_id.startswith(".."):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid pipeline_id")
    p = _pipelines_dir(tid) / pipeline_id
    if not p.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"pipeline {pipeline_id!r} not found")
    manifest = _read_json(p / "manifest.json") or {}
    summary = _read_json(p / "pipeline_summary.json") or {}
    # PipelineCoordinator (core/compute/corvin_compute/pipeline/coordinator.py)
    # only ever writes the rolling pipeline_summary.json — per its own
    # PipelineStore docstring, a per-stage stage_summary.json was never part
    # of the write-side contract, so every stage card showed "waiting for
    # prev stage…"/"no data" even for a fully converged pipeline with real
    # results. pipeline_summary.json's completed_stages/best_losses/
    # current_stage_id already carry everything needed to derive each
    # stage's state and best_loss — use that as a fallback when a stage has
    # no (optional, richer) stage_summary.json of its own.
    completed_stages = set(summary.get("completed_stages") or [])
    best_losses = summary.get("best_losses") or {}
    current_stage_id = summary.get("current_stage_id")
    stages_detail: list[dict[str, Any]] = []
    for stage_spec in manifest.get("stages", []):
        sid = stage_spec.get("stage_id", "")
        stage_dir = p / "stages" / sid
        stage_info = _read_stage_summary(stage_dir) if stage_dir.exists() else {}
        if "state" not in stage_info:
            if sid in completed_stages:
                stage_info["state"] = "complete"
            elif sid == current_stage_id:
                stage_info["state"] = "running"
        if stage_info.get("best_loss") is None and best_losses.get(sid) is not None:
            stage_info["best_loss"] = best_losses[sid]
        stages_detail.append({**stage_spec, **stage_info, "stage_id": sid})
    return {
        "pipeline_id": pipeline_id,
        "manifest": manifest,
        "summary": summary,
        "stages": stages_detail,
    }

# ── HAC endpoints ─────────────────────────────────────────────────────────

@router.get("/compute/hac")
def list_hac_runs(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """List all HAC runs for this tenant."""
    tid = rec.tenant_id
    hac_root = _hac_dir(tid)
    runs: list[dict[str, Any]] = []
    if hac_root.exists():
        try:
            for p in sorted(hac_root.iterdir(),
                            key=lambda x: x.stat().st_mtime if x.exists() else 0,
                            reverse=True):
                if not p.is_dir():
                    continue
                manifest = _read_json(p / "manifest.json") or {}
                summary = _read_json(p / "hac_summary.json") or {}
                runs.append({
                    "hac_id": p.name,
                    "name": manifest.get("name", p.name),
                    "state": summary.get("state"),
                    "round": summary.get("round", 0),
                    "max_rounds": manifest.get("max_backprop_rounds", 5),
                    "root_loss": summary.get("root_loss"),
                    "manager_count": len(manifest.get("sub_managers", [])),
                    "aggregation_mode": manifest.get("loss_weights", {}).get("mode"),
                    "fluid_reallocation": manifest.get("fluid_reallocation", False),
                    "started_at": manifest.get("started_at"),
                    "submitted_by": manifest.get("submitted_by"),
                    "attributions": summary.get("attributions", {}),
                })
        except OSError:
            pass
    return {"tenant_id": tid, "hac_count": len(runs), "runs": runs}

@router.get("/compute/hac/{hac_id}")
def hac_detail(
    hac_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Full HAC detail with per-sub-manager stage iterations."""
    tid = rec.tenant_id
    if "/" in hac_id or hac_id.startswith(".."):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid hac_id")
    p = _hac_dir(tid) / hac_id
    if not p.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"hac {hac_id!r} not found")
    manifest = _read_json(p / "manifest.json") or {}
    summary = _read_json(p / "hac_summary.json") or {}

    managers: list[dict[str, Any]] = []
    sub_losses: dict[str, float] = summary.get("sub_manager_losses", {})
    manager_states: dict[str, str] = summary.get("manager_states", {})

    for sm in manifest.get("sub_managers", []):
        # Support both "manager_id" and "sub_manager_id" key names
        mid = sm.get("manager_id") or sm.get("sub_manager_id", "")
        label = sm.get("label") or sm.get("description", mid)
        mgr_dir = p / "sub_managers" / mid
        stages_detail: list[dict[str, Any]] = []

        if mgr_dir.is_dir():
            stages_root = mgr_dir / "stages"
            if stages_root.is_dir():
                for stage_dir in sorted(stages_root.iterdir(),
                                        key=lambda d: d.name):
                    if not stage_dir.is_dir():
                        continue
                    stage_info = _read_stage_summary(stage_dir)
                    stage_summary_raw = _read_json(stage_dir / "stage_summary.json") or {}
                    tool = stage_summary_raw.get("tool_name") or f"{mid}.{stage_dir.name}"
                    stages_detail.append({
                        "stage_id": stage_dir.name,
                        "tool_name": tool,
                        "strategy": sm.get("strategy", "bayesian"),
                        **stage_info,
                    })

        current_loss = sub_losses.get(mid)
        best_loss = sub_losses.get(mid)
        if best_loss is None and stages_detail:
            best_loss = min((s["best_loss"] for s in stages_detail
                             if s.get("best_loss") is not None), default=None)

        # Authoritative per-manager state: the coordinator persists the REAL
        # terminal state per sub-manager in summary["manager_states"]. The old
        # derivation scanned stages_detail — which is ALWAYS empty because
        # sub-manager runs are written to the global runs/ dir, not under
        # sub_managers/<mid>/stages/ — so `all(... for s in [])` evaluated to True
        # and every manager was falsely reported "complete" (even mid-run/failed).
        persisted_state = manager_states.get(mid)
        if persisted_state:
            mgr_state = persisted_state
        elif any(s.get("state") == "running" for s in stages_detail):
            mgr_state = "running"
        elif stages_detail and all(s.get("state") == "complete" for s in stages_detail):
            mgr_state = "complete"
        else:
            mgr_state = "pending"  # no stage data AND no persisted state → not "complete"

        managers.append({
            "manager_id": mid,
            "label": label,
            "budget_fraction": sm.get("budget_fraction", 1 / max(len(manifest.get("sub_managers", [])), 1)),
            "strategy": sm.get("strategy", "bayesian"),
            "stages": stages_detail,
            "summary": {
                "state": mgr_state,
                "best_loss": best_loss,
                "current_loss": current_loss,
            },
        })

    loss_history: list[float] = summary.get("root_loss_history", [])
    attributions = summary.get("attributions", {})
    if not attributions and sub_losses:
        total = sum(sub_losses.values()) or 1
        attributions = {k: round(v / total, 4) for k, v in sub_losses.items()}

    return {
        "hac_id": hac_id,
        "manifest": manifest,
        "summary": {**summary, "attributions": attributions},
        "managers": managers,
        "loss_history": loss_history,
    }

# ── License / trial quota ─────────────────────────────────────────────────

@router.get("/compute/license")
def compute_license_status(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return current compute license mode + trial iteration quota."""
    import sys as _sys
    _repo = Path(__file__).resolve().parents[4]
    _compute_pkg = _repo / "core" / "compute"
    if str(_compute_pkg) not in _sys.path:
        _sys.path.insert(0, str(_compute_pkg))

    UPGRADE_URL = "https://corvin-labs.com/pricing"
    TRIAL_CAP      = 500   # grid + random
    BAYESIAN_CAP   = 50    # bayesian separate budget
    COMPUTE_FLAG   = "compute"
    FABRIC_FLAG    = "compute_fabric"

    runs_today = _runs_today_count(rec.tenant_id)

    # Read the canonical daily counter from compute_quota.json and the limit from
    # the active licence — this is the source of truth shown to the user.
    _corvin_home = _forge_paths.corvin_home()
    _daily_limit = _lic_get_limit("compute_units_per_day")   # None = unlimited
    _used_today_quota = (
        _cq_today(_corvin_home) if _cq_today is not None else runs_today
    )

    try:
        # Import corvin_license directly — avoids the cached _LICENSE_PLUGIN_AVAILABLE
        # flag in corvin_compute.license_gate that may be False if compute was imported
        # before corvin_license was on sys.path.
        from corvin_license.verifier import (  # type: ignore[import]
            load_license_from_disk,
            LicenseFileMissing,
            LicenseExpired,
            LicenseSignatureError,
            LicenseClaimError,
            LicenseFileMalformed,
        )
        from forge import paths as _fp

        corvin_home = _fp.corvin_home()

        def _trial_counters() -> dict[str, Any]:
            """Read persisted trial iteration counters (mode 0600 enforced)."""
            p = corvin_home / "global" / "license" / "trial_compute.json"
            used_gr = 0; used_bay = 0; first_run = None
            if p.exists():
                try:
                    mode = p.stat().st_mode & 0o777
                    if not (mode & 0o077):  # not world-readable
                        raw = json.loads(p.read_text(encoding="utf-8"))
                        used_gr  = int(raw.get("iterations_used", 0))
                        used_bay = int(raw.get("bayesian_iterations_used", 0))
                        first_run = raw.get("first_run_at")
                    else:
                        used_gr = TRIAL_CAP  # tampered — show as exhausted
                except (OSError, json.JSONDecodeError, ValueError):
                    pass
            return {
                "grid_random": {
                    "cap": TRIAL_CAP, "used": used_gr,
                    "remaining": max(0, TRIAL_CAP - used_gr),
                    "pct_used": round(used_gr / TRIAL_CAP * 100, 1),
                },
                "bayesian": {
                    "cap": BAYESIAN_CAP, "used": used_bay,
                    "remaining": max(0, BAYESIAN_CAP - used_bay),
                    "pct_used": round(used_bay / BAYESIAN_CAP * 100, 1),
                },
                "first_run_at": first_run,
            }

        def _base() -> dict[str, Any]:
            """Fields common to every return path."""
            return {
                "runs_today":  _used_today_quota,
                "daily_limit": _daily_limit,   # None = unlimited
                "upgrade_url": UPGRADE_URL,
            }

        try:
            lic = load_license_from_disk()
        except LicenseFileMissing:
            # No Enterprise (on-prem) license.jwt installed — this is the
            # normal case for a Paddle/consumer subscriber, who is licensed
            # through the SEPARATE operator/license system (license.key,
            # EdDSA, corvinlabs.io) instead. Missing Enterprise license must
            # not shadow an active consumer subscription: fall back to that
            # tier before reporting "free" (previously hardcoded here, so a
            # paying Member-tier customer always saw "Trial · free" on this
            # panel even though their daily_limit above was already correctly
            # unlimited from the same operator/license system).
            _op_tier = _lic_active_tier() if _lic_active_tier is not None else "free"
            if _op_tier != "free":
                return {**_base(), "mode": "licensed", "tier": _op_tier,
                        "fabric_allowed": False, "reason": None,
                        "quota": None, "license_meta": None}
            return {**_base(), "mode": "trial", "tier": "free",
                    "fabric_allowed": False, "reason": None,
                    "quota": _trial_counters(), "license_meta": None}
        except LicenseExpired as exc:
            return {**_base(), "mode": "grace",
                    "tier": getattr(exc, "tier", "pro") or "pro",
                    "fabric_allowed": False,
                    "reason": f"License expired — renew at {UPGRADE_URL}",
                    "quota": _trial_counters(), "license_meta": None}
        except (LicenseSignatureError, LicenseClaimError, LicenseFileMalformed) as exc:
            return {**_base(), "mode": "trial", "tier": "free",
                    "fabric_allowed": False,
                    "reason": f"License invalid: {exc}",
                    "quota": _trial_counters(), "license_meta": None}

        now_ = int(time.time())
        if lic.is_expired(now=now_):
            return {**_base(), "mode": "grace", "tier": lic.tier,
                    "fabric_allowed": False,
                    "reason": f"License expired — renew at {UPGRADE_URL}",
                    "quota": _trial_counters(), "license_meta": None}

        has_compute = lic.has_flag(COMPUTE_FLAG)
        has_fabric  = lic.has_flag(FABRIC_FLAG)
        customer_id = getattr(lic, "customer_id", "unknown")
        expires_at  = getattr(lic, "valid_until", None)
        issued_at   = getattr(lic, "issued_at", None)

        if not has_compute:
            return {**_base(), "mode": "trial", "tier": lic.tier,
                    "fabric_allowed": False,
                    "reason": f"Tier '{lic.tier}' does not include compute — trial limits apply",
                    "quota": _trial_counters(), "license_meta": None}

        return {
            **_base(),
            "mode":           "licensed",
            "tier":           lic.tier,
            "fabric_allowed": has_fabric,
            "reason":         None,
            "quota":          None,
            "license_meta": {
                "customer_id_hint": customer_id[:8] + "…",
                "expires_at": expires_at,
                "issued_at":  issued_at,
                "feature_flags": sorted(lic.feature_flags),
            },
        }

    except ImportError:
        return {
            "mode": "trial", "tier": "free", "fabric_allowed": False,
            "reason": "corvin-license plugin not installed",
            "upgrade_url": UPGRADE_URL,
            "runs_today": _used_today_quota,
            "daily_limit": _daily_limit,
            "quota": None,
            "license_meta": None,
        }
    except Exception as exc:
        return {
            "mode": "unknown", "tier": "unknown", "fabric_allowed": False,
            "reason": str(exc), "upgrade_url": UPGRADE_URL,
            "runs_today": _used_today_quota, "daily_limit": _daily_limit,
            "quota": None, "license_meta": None,
        }

# ── Compute config ────────────────────────────────────────────────────────

@router.get("/compute/config")
def compute_config(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return the spec.compute section of tenant.corvin.yaml."""
    tenant = _read_tenant_yaml(rec.tenant_id)
    spec = tenant.get("spec", {})
    compute = spec.get("compute", {})
    return {
        "enabled": bool(compute.get("enabled", False)),
        "fabric_enabled": bool(compute.get("fabric_enabled", False)),
        "max_parallel_runs": int(compute.get("max_parallel_runs", 4)),
        "run_ttl_days": int(compute.get("run_ttl_days", 7)),
        "yaml_exists": _tenant_yaml_path(rec.tenant_id).exists(),
    }

class ComputeConfigUpdate(BaseModel):
    enabled: bool
    fabric_enabled: bool = False
    max_parallel_runs: int = Field(default=4, ge=1, le=32)
    run_ttl_days: int = Field(default=7, ge=1, le=90)
    re_auth_token: str | None = None
    model_config = {"extra": "forbid"}

@router.put("/compute/config")
def update_compute_config(
    body: ComputeConfigUpdate,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Enable / disable the compute worker and adjust limits."""
    if not verify_reauth(rec, body.re_auth_token):
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="compute.config_update",
            target_kind="compute",
            target_id="config",
            reason="reauth-failed",
        )
        raise HTTPException(http_status.HTTP_401_UNAUTHORIZED, "re-auth failed")

    if not _YAML_OK:
        raise HTTPException(http_status.HTTP_503_SERVICE_UNAVAILABLE, "PyYAML not available")

    tenant = _read_tenant_yaml(rec.tenant_id)
    tenant.setdefault("spec", {}).setdefault("compute", {})
    tenant["spec"]["compute"] = {
        "enabled": body.enabled,
        "fabric_enabled": body.fabric_enabled,
        "max_parallel_runs": body.max_parallel_runs,
        "run_ttl_days": body.run_ttl_days,
    }
    _write_tenant_yaml(rec.tenant_id, tenant)

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="compute.config_update",
        target_kind="compute",
        target_id="config",
    )
    return {"ok": True, "enabled": body.enabled}

# ── User-facing compute settings (defaults + preferences) ─────────────────

_SETTINGS_DEFAULTS: dict[str, Any] = {
    "default_strategy":       "bayesian",    # grid | random | bayesian
    "default_max_iterations": 30,
    "default_timeout_s":      600,
    "convergence_threshold":  None,          # null = disabled; float = auto-stop below this loss
    "auto_champion":          True,          # auto-update experiment champion on better run
    "default_group_by":       "session",     # none | session | tool | source | day | strategy
    "artifact_preview_rows":  50,
    "alert_loss_threshold":   None,          # null = disabled; float = notify when best_loss drops below
    "show_corpus_banner":     True,
}

def _settings_path(tid: str) -> Path:
    p = _forge_paths.tenant_global_dir(tid) / "compute_settings.json"
    return p

def _load_settings(tid: str) -> dict[str, Any]:
    p = _settings_path(tid)
    if not p.exists():
        return dict(_SETTINGS_DEFAULTS)
    try:
        if p.stat().st_mode & 0o077:
            return dict(_SETTINGS_DEFAULTS)  # world-readable → use defaults
        data = json.loads(p.read_text(encoding="utf-8"))
        return {**_SETTINGS_DEFAULTS, **{k: v for k, v in data.items() if k in _SETTINGS_DEFAULTS}}
    except (OSError, json.JSONDecodeError):
        return dict(_SETTINGS_DEFAULTS)

def _save_settings(tid: str, data: dict[str, Any]) -> None:
    p = _settings_path(tid)
    p.parent.mkdir(parents=True, exist_ok=True)
    import tempfile
    body = json.dumps(data, indent=2, ensure_ascii=False)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

@router.get("/compute/settings")
def get_compute_settings(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return user-facing compute defaults and preferences."""
    return {
        "settings": _load_settings(rec.tenant_id),
        "schema": {
            "default_strategy":       {"type": "enum",   "options": ["bayesian", "grid", "random"],  "label": "Default strategy"},
            "default_max_iterations": {"type": "int",    "min": 5,    "max": 1000,  "label": "Default max iterations"},
            "default_timeout_s":      {"type": "int",    "min": 60,   "max": 7200,  "label": "Run timeout (seconds)"},
            "convergence_threshold":  {"type": "float?", "min": 0.0,  "max": 1.0,   "label": "Auto-stop loss threshold"},
            "auto_champion":          {"type": "bool",                               "label": "Auto-update experiment champion"},
            "default_group_by":       {"type": "enum",   "options": ["none", "session", "tool", "source", "day", "strategy"], "label": "Default grouping"},
            "artifact_preview_rows":  {"type": "int",    "min": 10,   "max": 500,   "label": "Artifact preview rows"},
            "alert_loss_threshold":   {"type": "float?", "min": 0.0,  "max": 1.0,   "label": "Loss alert threshold"},
            "show_corpus_banner":     {"type": "bool",                               "label": "Show corpus context banner"},
        },
    }

class ComputeSettingsUpdate(BaseModel):
    default_strategy: str = Field("bayesian", pattern=r"^(bayesian|grid|random)$")
    default_max_iterations: int = Field(30, ge=5, le=1000)
    default_timeout_s: int = Field(600, ge=60, le=7200)
    convergence_threshold: float | None = Field(None, ge=0.0, le=1.0)
    auto_champion: bool = True
    default_group_by: str = Field("session", pattern=r"^(none|session|tool|source|day|strategy)$")
    artifact_preview_rows: int = Field(50, ge=10, le=500)
    alert_loss_threshold: float | None = Field(None, ge=0.0, le=1.0)
    show_corpus_banner: bool = True
    model_config = {"extra": "forbid"}

@router.put("/compute/settings")
def update_compute_settings(
    body: ComputeSettingsUpdate,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Persist user compute preferences."""
    data = {
        "default_strategy":       body.default_strategy,
        "default_max_iterations": body.default_max_iterations,
        "default_timeout_s":      body.default_timeout_s,
        "convergence_threshold":  body.convergence_threshold,
        "auto_champion":          body.auto_champion,
        "default_group_by":       body.default_group_by,
        "artifact_preview_rows":  body.artifact_preview_rows,
        "alert_loss_threshold":   body.alert_loss_threshold,
        "show_corpus_banner":     body.show_corpus_banner,
    }
    _save_settings(rec.tenant_id, data)
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="compute.settings_update",
        target_kind="compute",
        target_id="settings",
    )
    return {"ok": True, "settings": data}

# ── Submit run ────────────────────────────────────────────────────────────

class SubmitRunRequest(BaseModel):
    tool_name: str = Field(..., pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    strategy: str = Field("random", pattern=r"^(grid|random|bayesian)$")
    budget: dict[str, Any] = Field(default_factory=lambda: {"max_iterations": 20, "timeout_s": 300})
    objective: str = Field("minimize_loss", max_length=120)
    params: dict[str, Any] = Field(default_factory=dict)
    re_auth_token: str | None = None
    model_config = {"extra": "forbid"}

@router.post("/compute/runs")
def submit_run(
    body: SubmitRunRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Submit a new compute run to the worker over its Unix socket.

    WA-14: this used to just write a manifest.json and return — but no
    poller ever read this directory (recovery.scan_resumable requires a
    summary.json this endpoint never wrote), so every console-submitted run
    sat forever, unexecuted, with no error surfaced. The worker's real
    submit_run op (core/compute/corvin_compute/worker.py) expects
    ``param_grid``/``loss_metric``/``budget.max_wall_clock_s``, not this
    model's ``params``/``objective``/``budget.timeout_s`` — routed through
    WorkerClient with the field names it actually expects.
    """
    if not verify_reauth(rec, body.re_auth_token):
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="compute.run_submit",
            target_kind="compute_run",
            target_id=body.tool_name,
            reason="reauth-failed",
        )
        raise HTTPException(http_status.HTTP_401_UNAUTHORIZED, "re-auth failed")

    # ADR-0094 / ADR-0147 R3-CON-RUNS-DRIFT-01: enforce compute_units_per_day via
    # the SHARED fail-closed helper, identical to POST /compute/acs/runs and
    # /compute/jobs. The old inline guard `if _COMPUTE_QUOTA_OK and _cq_increment
    # is not None:` SKIPPED enforcement entirely when the license module was
    # absent/shadowed (_COMPUTE_QUOTA_OK=False) — unmetered compute on the PRIMARY
    # route while the other two correctly 402'd. Routing all three through one
    # helper is the only way the gates cannot drift again.
    from ._compute_license_gate import enforce_compute_quota  # noqa: PLC0415

    enforce_compute_quota(
        rec.tenant_id, rec.sid_fingerprint, audit_action="compute.run_submit",
    )

    sock_path = _socket_path(rec.tenant_id)
    if not sock_path.exists():
        raise HTTPException(
            http_status.HTTP_503_SERVICE_UNAVAILABLE,
            "compute worker is not running — start it via Settings "
            "(systemctl --user start corvin-compute@<tenant>)",
        )

    # corvin_compute lives at core/compute/corvin_compute — not on the
    # console's PYTHONPATH by default (see compute_license_status above for
    # the same pattern with corvin_license).
    import sys as _sys
    _compute_pkg = Path(__file__).resolve().parents[4] / "core" / "compute"
    if str(_compute_pkg) not in _sys.path:
        _sys.path.insert(0, str(_compute_pkg))
    from corvin_compute.client import WorkerClient, WorkerClientError  # noqa: PLC0415

    worker_budget = dict(body.budget)
    if "timeout_s" in worker_budget and "max_wall_clock_s" not in worker_budget:
        worker_budget["max_wall_clock_s"] = worker_budget.pop("timeout_s")

    client = WorkerClient(sock_path)
    try:
        result = client.submit_run(
            tool_name=body.tool_name,
            param_grid=body.params,
            loss_metric="loss",
            strategy=body.strategy,
            budget=worker_budget,
            minimise=(body.objective != "maximize_loss"),
            tenant_id=rec.tenant_id,
        )
    except (OSError, WorkerClientError) as exc:
        # The quota was charged above but the worker never accepted the run — no
        # work was done. Refund the unit so a transient worker error (worker down,
        # socket hiccup) does not burn a free-tier user's entire 1/day budget for
        # nothing. Best-effort; never masks the 502.
        from ._compute_license_gate import refund_compute_quota  # noqa: PLC0415
        refund_compute_quota()
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="compute.run_submit",
            target_kind="compute_run",
            target_id=body.tool_name,
            reason=str(exc)[:200],
        )
        raise HTTPException(
            http_status.HTTP_502_BAD_GATEWAY, f"compute worker rejected the run: {exc}",
        ) from exc

    run_id = result["compute_handle"]
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="compute.run_submit",
        target_kind="compute_run",
        target_id=run_id,
    )
    return {"ok": True, "run_id": run_id, "state": result.get("state")}

@router.delete("/compute/runs/{run_id}")
def delete_run(
    run_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Remove a run directory."""
    import shutil
    if "/" in run_id or run_id.startswith(".."):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid run_id")
    run_dir = _runs_dir(rec.tenant_id) / run_id
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="compute.run_delete",
        target_kind="compute_run",
        target_id=run_id,
    )
    if run_dir.exists():
        shutil.rmtree(run_dir)
    return {"ok": True, "run_id": run_id}

# ── Open directory in file manager ────────────────────────────────────────

def _safe_open_dir(target: Path, root: Path) -> dict[str, Any]:
    """Open *target* in the system file manager (xdg-open).

    Security: rejects paths that escape *root* (path traversal guard).
    Non-blocking — xdg-open is fire-and-forget.
    Falls back gracefully when no DISPLAY is available or xdg-open fails.
    """
    import subprocess
    try:
        resolved = target.resolve()
        root_resolved = root.resolve()
        resolved.relative_to(root_resolved)   # raises ValueError if outside root
    except ValueError:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "path escapes compute directory")

    if not resolved.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"directory not found: {resolved}")

    path_str = str(resolved)
    try:
        # Minimal env — do not inherit secrets (API keys, tokens) into subprocess.
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "DISPLAY": os.environ.get("DISPLAY", ":0"),
            "HOME": os.environ.get("HOME", ""),
            "DBUS_SESSION_BUS_ADDRESS": os.environ.get("DBUS_SESSION_BUS_ADDRESS", ""),
        }
        subprocess.Popen(
            ["xdg-open", path_str],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        launched = True
    except (FileNotFoundError, OSError):
        launched = False

    return {"ok": True, "path": path_str, "launched": launched}

@router.post("/compute/runs/{run_id}/open-dir")
def open_run_dir(
    run_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Open the run directory in the system file manager (non-blocking)."""
    if "/" in run_id or run_id.startswith(".."):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid run_id")
    run_dir  = _runs_dir(rec.tenant_id) / run_id
    root_dir = _compute_dir(rec.tenant_id)
    return _safe_open_dir(run_dir, root_dir)

@router.post("/compute/pipelines/{pipeline_id}/open-dir")
def open_pipeline_dir(
    pipeline_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Open the pipeline directory in the system file manager."""
    if "/" in pipeline_id or pipeline_id.startswith(".."):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid pipeline_id")
    p_dir    = _pipelines_dir(rec.tenant_id) / pipeline_id
    root_dir = _compute_dir(rec.tenant_id)
    return _safe_open_dir(p_dir, root_dir)

@router.post("/compute/hac/{hac_id}/open-dir")
def open_hac_dir(
    hac_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Open the HAC directory in the system file manager."""
    if "/" in hac_id or hac_id.startswith(".."):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid hac_id")
    h_dir    = _hac_dir(rec.tenant_id) / hac_id
    root_dir = _compute_dir(rec.tenant_id)
    return _safe_open_dir(h_dir, root_dir)

@router.post("/compute/acs/{run_id}/open-dir")
def open_acs_run_dir(
    run_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Open the ACS run workspace in the system file manager (non-blocking).

    Follows the run_dir pointer from the manifest so the user lands directly in
    the actual working directory (which may be session-scoped, not global).
    Uses the same 3-root containment guard as get_acs_run_graph().
    """
    import subprocess
    if "/" in run_id or run_id.startswith(".."):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid run_id")

    # Dual-path manifest lookup (same as get_acs_run_graph)
    global_manifest_path = _acs_runs_dir(rec.tenant_id) / run_id / "manifest.json"
    if not global_manifest_path.exists():
        _runtime_acs = (
            Path.home() / ".corvin" / "tenants" / rec.tenant_id
            / "global" / "acs" / "runs" / run_id / "manifest.json"
        )
        if _runtime_acs.exists():
            global_manifest_path = _runtime_acs
    if not global_manifest_path.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"ACS run {run_id!r} not found")

    manifest = _read_json(global_manifest_path) or {}
    run_dir_str = manifest.get("run_dir")
    target = (
        Path(run_dir_str).resolve()
        if run_dir_str
        else global_manifest_path.parent.resolve()
    )

    # 3-root containment guard (mirrors get_acs_run_graph)
    _project_corvin = (_forge_paths.corvin_home() / "tenants" / rec.tenant_id).resolve()
    _home_corvin = (Path.home() / ".corvin" / "tenants" / rec.tenant_id).resolve()
    _acs_index_root = _acs_runs_dir(rec.tenant_id).resolve()
    _trusted = (str(_project_corvin), str(_home_corvin), str(_acs_index_root))
    if not any(str(target).startswith(r) for r in _trusted):
        raise HTTPException(http_status.HTTP_403_FORBIDDEN,
                            "ACS run_dir points outside the allowed tenant tree")

    if not target.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND,
                            "ACS run directory not found on disk")

    path_str = str(target)
    try:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "DISPLAY": os.environ.get("DISPLAY", ":0"),
            "HOME": os.environ.get("HOME", ""),
            "DBUS_SESSION_BUS_ADDRESS": os.environ.get("DBUS_SESSION_BUS_ADDRESS", ""),
        }
        subprocess.Popen(
            ["xdg-open", path_str],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        launched = True
    except (FileNotFoundError, OSError):
        launched = False

    return {"ok": True, "path": path_str, "launched": launched}

# ══════════════════════════════════════════════════════════════════════════
# ── M1: Corpus Context Banner ─────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════

@router.get("/compute/corpus-context")
def corpus_context(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return corpus metadata from the most recent pipeline stage_1 summary."""
    tid = rec.tenant_id
    pipelines_root = _pipelines_dir(tid)
    real_stats: dict[str, Any] = {}
    pipeline_name: str | None = None

    if pipelines_root.exists():
        try:
            dirs = sorted(pipelines_root.iterdir(),
                          key=lambda p: p.stat().st_mtime if p.exists() else 0,
                          reverse=True)
            for pd in dirs:
                if not pd.is_dir():
                    continue
                s1_summary = pd / "stages" / "stage_1" / "stage_summary.json"
                if s1_summary.exists():
                    data = _read_json(s1_summary) or {}
                    real_stats = data.get("real_stats", {})
                    manifest = _read_json(pd / "manifest.json") or {}
                    pipeline_name = manifest.get("name", pd.name)
                    break
        except OSError:
            pass

    return {
        "tenant_id":     tid,
        "pipeline_name": pipeline_name,
        "real_stats":    real_stats,
        "has_corpus":    bool(real_stats),
    }

# ══════════════════════════════════════════════════════════════════════════
# ── M2: Experiment Object CRUD ────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════

def _experiments_dir(tid: str) -> Path:
    return _compute_dir(tid) / "experiments"

def _read_experiment(exp_dir: Path) -> dict[str, Any] | None:
    p = exp_dir / "experiment.json"
    if not p.exists():
        return None
    return _read_json(p) or {}

def _write_experiment(exp_dir: Path, data: dict[str, Any]) -> None:
    exp_dir.mkdir(parents=True, exist_ok=True)
    p = exp_dir / "experiment.json"
    import tempfile
    raw = json.dumps(data, indent=2, ensure_ascii=False)
    fd, tmp = tempfile.mkstemp(dir=str(exp_dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(raw)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

@router.get("/compute/experiments")
def list_experiments(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """List all experiments for this tenant."""
    tid = rec.tenant_id
    exps_root = _experiments_dir(tid)
    experiments: list[dict[str, Any]] = []
    if exps_root.exists():
        try:
            for d in sorted(exps_root.iterdir(),
                            key=lambda p: p.stat().st_mtime if p.exists() else 0,
                            reverse=True):
                if not d.is_dir():
                    continue
                exp = _read_experiment(d)
                if exp:
                    experiments.append(exp)
        except OSError:
            pass
    return {"tenant_id": tid, "count": len(experiments), "experiments": experiments}

@router.get("/compute/experiments/{experiment_id}")
def get_experiment(
    experiment_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return experiment detail including comparison table."""
    if "/" in experiment_id or experiment_id.startswith(".."):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid experiment_id")
    tid = rec.tenant_id
    exp_dir = _experiments_dir(tid) / experiment_id
    exp = _read_experiment(exp_dir)
    if not exp:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"experiment {experiment_id!r} not found")

    # Build comparison table by reading run manifests + summaries
    runs_data: list[dict[str, Any]] = []
    for run_id in exp.get("run_ids", []):
        if "/" in run_id or run_id.startswith(".."):
            continue
        run_dir = _runs_dir(tid) / run_id
        manifest = _read_json(run_dir / "manifest.json") or {}
        summary  = _read_json(run_dir / "summary.json") or {}
        iters_dir = run_dir / "iterations"
        iter_count = 0
        if iters_dir.is_dir():
            try:
                iter_count = sum(1 for f in iters_dir.iterdir() if f.suffix == ".json")
            except OSError:
                pass
        runs_data.append({
            "run_id":           run_id,
            "tool_name":        manifest.get("tool_name"),
            "strategy":         manifest.get("strategy"),
            "params":           manifest.get("params", {}),
            "best_loss":        summary.get("best_loss"),
            "best_iter":        summary.get("best_iter"),
            "convergence":      summary.get("convergence_reason"),
            "state":            summary.get("state"),
            "iterations_done":  iter_count,
            "budget_max":       (manifest.get("budget") or {}).get("max_iterations"),
            "submitted_by":     manifest.get("submitted_by"),
            "session_label":    manifest.get("session_label"),
            "started_at":       manifest.get("started_at"),
            "is_baseline":      run_id == exp.get("baseline_run_id"),
            "is_champion":      run_id == exp.get("champion_run_id"),
        })

    return {**exp, "runs_detail": runs_data}

class ExperimentCreate(BaseModel):
    name: str = Field(..., max_length=200)
    hypothesis: str = Field("", max_length=2000)
    session_id: str | None = None
    session_label: str | None = None
    run_ids: list[str] = Field(default_factory=list)
    baseline_run_id: str | None = None
    champion_run_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    model_config = {"extra": "forbid"}

@router.post("/compute/experiments")
def create_experiment(
    body: ExperimentCreate,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Create a new experiment."""
    import secrets as _sec
    exp_id = "exp_" + _sec.token_hex(4)
    exp_dir = _experiments_dir(rec.tenant_id) / exp_id
    data = {
        "experiment_id":   exp_id,
        "name":            body.name,
        "hypothesis":      body.hypothesis,
        "session_id":      body.session_id,
        "session_label":   body.session_label,
        "run_ids":         body.run_ids,
        "baseline_run_id": body.baseline_run_id,
        "champion_run_id": body.champion_run_id,
        "tags":            body.tags,
        "locked":          False,
        "created_at":      int(time.time()),
    }
    _write_experiment(exp_dir, data)
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="compute.experiment_create",
        target_kind="compute_experiment",
        target_id=exp_id,
    )
    return data

class ExperimentUpdate(BaseModel):
    name: str | None = Field(None, max_length=200)
    hypothesis: str | None = Field(None, max_length=2000)
    tags: list[str] | None = Field(None, max_length=50)
    champion_run_id: str | None = None
    run_ids: list[str] | None = Field(None, max_length=500)
    media_attachments: list[dict[str, Any]] | None = Field(None)
    model_config = {"extra": "forbid"}

@router.put("/compute/experiments/{experiment_id}")
def update_experiment(
    experiment_id: str,
    body: ExperimentUpdate,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Update experiment (hypothesis, tags, champion, add runs)."""
    if "/" in experiment_id or experiment_id.startswith(".."):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid experiment_id")
    exp_dir = _experiments_dir(rec.tenant_id) / experiment_id
    exp = _read_experiment(exp_dir)
    if not exp:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "not found")
    if exp.get("locked"):
        raise HTTPException(http_status.HTTP_409_CONFLICT, "experiment is locked")
    update_data = body.model_dump(exclude_none=True)
    exp.update(update_data)
    exp["updated_at"] = int(time.time())
    _write_experiment(exp_dir, exp)
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="compute.experiment_update",
        target_kind="compute_experiment",
        target_id=experiment_id,
    )
    return exp

# ══════════════════════════════════════════════════════════════════════════
# ── M4: Artifact Viewer endpoints ─────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════

def _stage_artifacts_dir(tid: str, pipeline_id: str, stage_id: str) -> Path:
    return _pipelines_dir(tid) / pipeline_id / "stages" / stage_id / "artifacts"

@router.get("/compute/pipelines/{pipeline_id}/stages/{stage_id}/artifact-stats")
def artifact_stats(
    pipeline_id: str,
    stage_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return pre-computed artifact statistics from stage_summary.json."""
    for v in (pipeline_id, stage_id):
        if "/" in v or v.startswith(".."):
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid id")
    tid = rec.tenant_id
    stage_dir = _pipelines_dir(tid) / pipeline_id / "stages" / stage_id
    summary = _read_json(stage_dir / "stage_summary.json") or {}
    artifacts_dir = stage_dir / "artifacts"

    # List artifact files
    artifacts: list[dict[str, Any]] = []
    if artifacts_dir.is_dir():
        for f in sorted(artifacts_dir.iterdir()):
            if f.is_file():
                stat = f.stat()
                artifacts.append({
                    "filename": f.name,
                    "size_bytes": stat.st_size,
                    "size_mb": round(stat.st_size / 1_048_576, 2),
                    "extension": f.suffix.lower(),
                })

    return {
        "stage_id":  stage_id,
        "state":     summary.get("state"),
        "real_stats": summary.get("real_stats", {}),
        "artifacts": artifacts,
        "pii_columns": summary.get("pii_tagged_columns", []),
    }

def _duckdb_table_query(
    artifact_path: "Path",
    *,
    pii_cols: list[str],
    page: int = 1,
    per_page: int = 50,
    sort_col: str | None = None,
    sort_dir: str = "asc",
    filter_text: str | None = None,
    selected_cols: list[str] | None = None,
) -> dict[str, Any]:
    """Run a fully parametrised DuckDB query supporting sort/filter/pagination.

    Returns a dict with keys: schema, rows, total_rows, page, per_page,
    pii_redacted, sort_col, sort_dir, filter_text.
    """
    try:
        import duckdb as _duckdb
    except ImportError:
        raise HTTPException(http_status.HTTP_503_SERVICE_UNAVAILABLE,
                            "duckdb not installed — run `pip install duckdb` to enable "
                            "the data viewer")

    ext = artifact_path.suffix.lower()
    path_str = str(artifact_path)
    per_page = min(max(1, per_page), 500)
    page = max(1, page)
    offset = (page - 1) * per_page
    sort_dir_safe = "ASC" if sort_dir.lower() != "desc" else "DESC"

    # Base table expression — parametrised path (no SQL injection)
    if ext == ".csv":
        tbl_expr = "read_csv_auto(?)"
    elif ext in (".parquet", ".pq"):
        tbl_expr = "read_parquet(?)"
    elif ext == ".json":
        tbl_expr = "read_json_auto(?)"
    else:
        raise HTTPException(http_status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            f"unsupported format for table preview: {ext}")

    con = _duckdb.connect()
    try:
        # Get full schema first (to validate sort_col + selected_cols)
        schema_result = con.execute(f"DESCRIBE SELECT * FROM {tbl_expr} LIMIT 0", [path_str]).fetchall()
        all_cols = [r[0] for r in schema_result]
        col_types = {r[0]: r[1] for r in schema_result}

        # Column selection — only allow known column names (prevents injection)
        if selected_cols:
            valid_cols = [c for c in selected_cols if c in all_cols]
        else:
            valid_cols = all_cols
        if not valid_cols:
            valid_cols = all_cols

        # Quoted identifiers for safe column referencing
        col_expr = ", ".join(f'"{c}"' for c in valid_cols)

        # WHERE clause — ILIKE search across string-like columns (no injection risk)
        params: list[Any] = [path_str]
        where_clause = ""
        if filter_text and filter_text.strip():
            # Only search string/varchar columns to avoid type errors
            str_cols = [c for c in valid_cols if "VARCHAR" in col_types.get(c, "").upper()
                        or "TEXT" in col_types.get(c, "").upper()
                        or "CHAR" in col_types.get(c, "").upper()]
            if str_cols:
                like_parts = " OR ".join(f'CAST("{c}" AS VARCHAR) ILIKE ?' for c in str_cols)
                where_clause = f"WHERE ({like_parts})"
                params.extend([f"%{filter_text}%"] * len(str_cols))

        base_sql = f"SELECT {col_expr} FROM {tbl_expr} {where_clause}"

        # Total row count (with filter applied)
        total_rows = con.execute(f"SELECT COUNT(*) FROM ({base_sql}) AS _t", params).fetchone()[0]

        # ORDER BY — validate column exists
        order_clause = ""
        effective_sort = None
        if sort_col and sort_col in all_cols:
            order_clause = f'ORDER BY "{sort_col}" {sort_dir_safe} NULLS LAST'
            effective_sort = sort_col

        # Final paginated query
        data_sql = f"{base_sql} {order_clause} LIMIT ? OFFSET ?"
        data_params = params + [per_page, offset]
        result = con.execute(data_sql, data_params).fetchdf()
    finally:
        con.close()

    # PII redaction (post-fetch — client never sees raw PII values)
    for col in pii_cols:
        if col in result.columns:
            result[col] = "[REDACTED]"

    schema = [{"name": c, "type": str(result[c].dtype)} for c in result.columns]
    row_dicts = result.where(result.notnull(), None).to_dict(orient="records")

    return {
        "schema":      schema,
        "rows":        row_dicts,
        "total_rows":  int(total_rows),
        "page":        page,
        "per_page":    per_page,
        "total_pages": max(1, (int(total_rows) + per_page - 1) // per_page),
        "sort_col":    effective_sort,
        "sort_dir":    sort_dir_safe.lower(),
        "filter_text": filter_text or "",
        "pii_redacted": pii_cols,
        "all_columns": all_cols,
    }

@router.get("/compute/pipelines/{pipeline_id}/stages/{stage_id}/artifact-preview")
def artifact_preview(
    pipeline_id: str,
    stage_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    filename: str = "weekly_chart_aggregates.csv",
    rows: int = 50,
    # Navigable table params
    page: int = 1,
    per_page: int = 50,
    sort_col: str | None = None,
    sort_dir: str = "asc",
    filter: str | None = None,
    cols: str | None = None,
) -> dict[str, Any]:
    """Preview a stage artifact with full sort / filter / pagination support."""
    for v in (pipeline_id, stage_id, filename):
        if "/" in v or v.startswith(".."):
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid parameter")

    tid = rec.tenant_id
    artifact_path = _stage_artifacts_dir(tid, pipeline_id, stage_id) / filename
    compute_root = _compute_dir(tid)

    try:
        artifact_path.resolve().relative_to(compute_root.resolve())
    except ValueError:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "path outside compute directory")

    if not artifact_path.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"artifact {filename!r} not found")

    stage_dir = _pipelines_dir(tid) / pipeline_id / "stages" / stage_id
    summary = _read_json(stage_dir / "stage_summary.json") or {}
    pii_cols: list[str] = summary.get("pii_tagged_columns", [])
    selected_cols = [c.strip() for c in cols.split(",")] if cols else None

    result = _duckdb_table_query(
        artifact_path,
        pii_cols=pii_cols,
        page=page,
        per_page=per_page,
        sort_col=sort_col,
        sort_dir=sort_dir,
        filter_text=filter,
        selected_cols=selected_cols,
    )
    result["filename"] = filename
    # Backwards compat: rows_returned field
    result["rows_returned"] = len(result["rows"])
    return result

@router.get("/compute/pipelines/{pipeline_id}/stages/{stage_id}/artifact-download")
def artifact_download(
    pipeline_id: str,
    stage_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    filename: str = "weekly_chart_aggregates.csv",
):
    """Stream a stage artifact file as a download."""
    from fastapi.responses import FileResponse
    for v in (pipeline_id, stage_id, filename):
        if "/" in v or v.startswith(".."):
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid parameter")

    tid = rec.tenant_id
    artifact_path = _stage_artifacts_dir(tid, pipeline_id, stage_id) / filename
    try:
        artifact_path.resolve().relative_to(_compute_dir(tid).resolve())
    except ValueError:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "path outside compute directory")

    if not artifact_path.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"artifact {filename!r} not found")

    media_types = {".csv": "text/csv", ".parquet": "application/octet-stream",
                   ".json": "application/json", ".jsonl": "application/x-ndjson"}
    mt = media_types.get(artifact_path.suffix.lower(), "application/octet-stream")
    safe_filename = filename.replace('"', "").replace("\r", "").replace("\n", "")
    return FileResponse(str(artifact_path), media_type=mt,
                        headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'})

# ══════════════════════════════════════════════════════════════════════════
# ── M6: Export Hub ────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════

@router.get("/compute/experiments/{experiment_id}/export/jupyter")
def export_jupyter(
    experiment_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
):
    """Generate a Jupyter notebook for this experiment."""
    from fastapi.responses import Response
    if "/" in experiment_id or experiment_id.startswith(".."):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid experiment_id")
    tid = rec.tenant_id
    exp_dir = _experiments_dir(tid) / experiment_id
    exp = _read_experiment(exp_dir)
    if not exp:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "experiment not found")

    try:
        import nbformat as _nbf
    except ImportError:
        raise HTTPException(http_status.HTTP_503_SERVICE_UNAVAILABLE, "nbformat not installed")

    nb = _nbf.v4.new_notebook()
    cells = []

    # Title
    cells.append(_nbf.v4.new_markdown_cell(
        f"# {exp.get('name', experiment_id)}\n\n"
        f"**Hypothesis:** {exp.get('hypothesis', '—')}\n\n"
        f"**Session:** {exp.get('session_label', '—')}  \n"
        f"**Champion:** `{exp.get('champion_run_id', '—')}`"
    ))

    # Data loading — find first stage_1 artifact across pipelines (single scan)
    first_artifact = None
    pipelines_root = _pipelines_dir(tid)
    if pipelines_root.exists():
        try:
            for pip_dir in sorted(pipelines_root.iterdir(),
                                  key=lambda p: p.stat().st_mtime if p.exists() else 0,
                                  reverse=True):
                art = pip_dir / "stages" / "stage_1" / "artifacts" / "weekly_chart_aggregates.csv"
                if art.exists():
                    first_artifact = str(art)
                    break
        except OSError:
            pass

    cells.append(_nbf.v4.new_code_cell(
        f"import pandas as pd\nimport matplotlib.pyplot as plt\n\n"
        f"# Load stage_1 artifact\ndf = pd.read_csv(r'{first_artifact or 'data.csv'}')\n"
        f"print(f'Rows: {{len(df):,}} | Columns: {{list(df.columns)}}')\ndf.head()"
    ))

    # Loss curves — use runtime-computed runs_dir, not a hardcoded developer path
    runs_base = str(_runs_dir(tid))
    run_loss_code = (
        "runs = " + json.dumps(exp.get("run_ids", [])[:4]) + "\n"
        "import json, os\n"
        f"runs_base = r'{runs_base}'\n"
        "fig, ax = plt.subplots(figsize=(10, 5))\n"
        "for run_id in runs:\n"
        "    iters_dir = os.path.join(runs_base, run_id, 'iterations')\n"
        "    if not os.path.isdir(iters_dir): continue\n"
        "    files = sorted([f for f in os.listdir(iters_dir) if f.endswith('.json')],\n"
        "                   key=lambda f: int(f.split('_')[-1].split('.')[0]) if f.split('_')[-1].split('.')[0].isdigit() else 0)\n"
        "    losses = [json.load(open(os.path.join(iters_dir, f)))['loss'] for f in files]\n"
        "    ax.plot(losses, label=run_id)\n"
        "ax.set_xlabel('Iteration'); ax.set_ylabel('Loss'); ax.legend(); ax.set_title('Loss Curves')\n"
        "plt.tight_layout(); plt.show()"
    )
    cells.append(_nbf.v4.new_code_cell(run_loss_code))

    # Top tracks
    cells.append(_nbf.v4.new_markdown_cell("## Top Tracks by Streams"))
    cells.append(_nbf.v4.new_code_cell(
        "df.groupby('track_id')['streams_p50'].max().sort_values(ascending=False).head(10)"
        if first_artifact else "print('No artifact found')"
    ))

    # Reproduction command
    cells.append(_nbf.v4.new_markdown_cell(
        f"## Reproduce Champion Run\n\n"
        f"```bash\ncorvinctl reproduce pipeline_spotify_chart_pred "
        f"--params {exp.get('champion_run_id', 'CHAMPION_RUN_ID')}\n```"
    ))

    nb.cells = cells
    nb_json = _nbf.writes(nb)
    safe_name = experiment_id.replace("/", "_").replace('"', "").replace("\r", "").replace("\n", "")
    return Response(
        content=nb_json,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.ipynb"'},
    )

@router.get("/compute/experiments/{experiment_id}/export/mlflow")
def export_mlflow(
    experiment_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
):
    """Generate MLflow-compatible mlruns directory as a ZIP archive."""
    import zipfile, io
    from fastapi.responses import StreamingResponse
    if "/" in experiment_id or experiment_id.startswith(".."):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid experiment_id")
    tid = rec.tenant_id
    exp_dir = _experiments_dir(tid) / experiment_id
    exp = _read_experiment(exp_dir)
    if not exp:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "experiment not found")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        exp_uuid = "0"
        zf.writestr(f"mlruns/{exp_uuid}/meta.yaml",
                    f"artifact_location: mlruns/{exp_uuid}\n"
                    f"experiment_id: '{exp_uuid}'\n"
                    f"lifecycle_stage: active\n"
                    f"name: {exp.get('name', experiment_id)}\n")

        for run_id in exp.get("run_ids", []):
            run_dir_path = _runs_dir(tid) / run_id
            manifest = _read_json(run_dir_path / "manifest.json") or {}
            summary  = _read_json(run_dir_path / "summary.json") or {}
            run_uuid = run_id[:8]
            base = f"mlruns/{exp_uuid}/{run_uuid}"

            # params
            for k, v in (manifest.get("params") or {}).items():
                zf.writestr(f"{base}/params/{k}", str(v))
            zf.writestr(f"{base}/params/strategy", str(manifest.get("strategy", "")))

            # metrics
            iters_dir = run_dir_path / "iterations"
            metric_lines = ""
            if iters_dir.is_dir():
                for f in sorted(iters_dir.iterdir(),
                                key=lambda x: int(x.stem.split("_")[-1]) if x.stem.split("_")[-1].isdigit() else 0):
                    d = _read_json(f) or {}
                    if isinstance(d.get("loss"), (int, float)):
                        ts = int((manifest.get("started_at") or 0) + d.get("iter", 0) * 10) * 1000
                        metric_lines += f"{ts} {d['loss']} {d.get('iter', 0)}\n"
            zf.writestr(f"{base}/metrics/loss", metric_lines)
            if summary.get("best_loss") is not None:
                zf.writestr(f"{base}/metrics/best_loss", f"0 {summary['best_loss']} 0\n")

            # tags
            zf.writestr(f"{base}/tags/mlflow.runName", run_id)
            zf.writestr(f"{base}/tags/mlflow.source.type", "LOCAL")

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{experiment_id.replace(chr(34),"").replace(chr(13),"").replace(chr(10),"")}_mlruns.zip"'},
    )

@router.get("/compute/experiments/{experiment_id}/report")
def experiment_report(
    experiment_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
):
    """Generate a self-contained HTML experiment report."""
    from fastapi.responses import HTMLResponse
    if "/" in experiment_id or experiment_id.startswith(".."):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid experiment_id")
    tid = rec.tenant_id
    exp_dir = _experiments_dir(tid) / experiment_id
    exp = _read_experiment(exp_dir)
    if not exp:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "experiment not found")

    # Gather corpus context
    corpus: dict[str, Any] = {}
    for pip_dir in (_pipelines_dir(tid)).iterdir() if _pipelines_dir(tid).exists() else []:
        s = _read_json(pip_dir / "stages" / "stage_1" / "stage_summary.json") or {}
        if s.get("real_stats"):
            corpus = s["real_stats"]
            break

    # Gather runs detail
    runs_detail: list[dict[str, Any]] = []
    for run_id in exp.get("run_ids", []):
        rd = _runs_dir(tid) / run_id
        m = _read_json(rd / "manifest.json") or {}
        s = _read_json(rd / "summary.json") or {}
        runs_detail.append({"run_id": run_id, **m, **s,
                             "is_champion": run_id == exp.get("champion_run_id"),
                             "is_baseline": run_id == exp.get("baseline_run_id")})

    champ = next((r for r in runs_detail if r.get("is_champion")), runs_detail[0] if runs_detail else {})
    base  = next((r for r in runs_detail if r.get("is_baseline")), {})

    # Loss improvement — champ_loss was tautological copy-paste; use best_loss directly.
    champ_loss = champ.get("best_loss")
    base_loss  = base.get("best_loss")
    improvement = round((1 - champ_loss / base_loss) * 100, 1) if champ_loss is not None and base_loss and base_loss > 0 else 0

    # All user-controlled values HTML-escaped to prevent stored XSS.
    def _h(v: Any) -> str:
        return _html_escape(str(v)) if v is not None else "—"

    top_tracks = corpus.get("top_tracks", [])
    top_tracks_html = "".join(
        f"<tr><td>{i+1}</td><td>{_h(t.get('track_name'))}</td>"
        f"<td>{_h(t.get('artist'))}</td>"
        f"<td>{t.get('total_streams',0):,}</td>"
        f"<td>#{_h(t.get('peak_rank'))}</td>"
        f"<td>{_h(t.get('days_on_chart'))}</td></tr>"
        for i, t in enumerate(top_tracks[:10])
    )

    run_rows = "".join(
        f"<tr class='{'champion' if r.get('is_champion') else 'baseline' if r.get('is_baseline') else ''}'>"
        f"<td>{'🏆 ' if r.get('is_champion') else '📌 ' if r.get('is_baseline') else ''}{_h(r['run_id'])}</td>"
        f"<td>{_h(r.get('strategy'))}</td>"
        f"<td style='color:{'#16a34a' if r.get('is_champion') else 'inherit'};font-weight:{'700' if r.get('is_champion') else '400'}'>"
        f"{_h(r.get('best_loss'))}</td>"
        f"<td>{_h(r.get('convergence_reason'))}</td>"
        f"<td>{_h(r.get('submitted_by'))}</td></tr>"
        for r in runs_detail
    )

    exp_name = _h(exp.get("name", "Experiment Report"))
    exp_hypothesis = _h(exp.get("hypothesis"))
    exp_session = _h(exp.get("session_label"))
    champ_best_loss = _h(champ.get("best_loss"))
    champ_strategy = _h(champ.get("strategy"))
    champ_run_id = _h(exp.get("champion_run_id", "CHAMPION"))
    watermark = _h(corpus.get("watermark_date", "2026-05-08"))

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<title>{exp_name}</title>
<style>
body{{font-family:-apple-system,sans-serif;max-width:900px;margin:0 auto;padding:32px;color:#1a1a18;background:#f7f5f2}}
.header{{background:#1a1a18;color:#f7f5f2;padding:24px;border-radius:10px;margin-bottom:20px}}
.header h1{{margin:0;font-size:22px;font-weight:300}}
.header .sub{{color:#a8a29e;font-size:12px;margin-top:6px}}
.hyp{{background:#fff;border-left:4px solid #b8945f;padding:12px 16px;border-radius:0 6px 6px 0;font-style:italic;margin-bottom:16px;font-size:13px}}
.kpi-row{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:16px}}
.kpi{{background:#fff;border-radius:8px;padding:12px;text-align:center;border:1px solid #e5e0d8}}
.kpi .v{{font-size:20px;font-weight:700;font-family:monospace}}
.kpi .l{{font-size:10px;color:#8a7f72;text-transform:uppercase;margin-top:2px}}
.section{{background:#fff;border:1px solid #e5e0d8;border-radius:8px;padding:16px;margin-bottom:16px}}
.section h2{{font-size:14px;font-weight:600;margin:0 0 12px}}
table{{width:100%;border-collapse:collapse;font-size:11px}}
th{{text-align:left;padding:6px 8px;background:#f7f5f2;font-size:10px;text-transform:uppercase;color:#8a7f72;border-bottom:1px solid #e5e0d8}}
td{{padding:6px 8px;border-bottom:1px solid #f0ece6}}
tr.champion td{{background:#f0fdf4;font-weight:600}}
tr.baseline td{{background:#faf5eb}}
.repro{{font-family:monospace;font-size:11px;background:#f7f5f2;padding:10px 14px;border-radius:6px;border:1px solid #e5e0d8}}
</style></head>
<body>
<div class="header">
  <h1>{exp_name}</h1>
  <div class="sub">Generated {time.strftime('%Y-%m-%d %H:%M UTC')} · Session: {exp_session} · {len(runs_detail)} runs</div>
</div>
<div class="hyp"><strong>Hypothesis:</strong> {exp_hypothesis}</div>
<div class="kpi-row">
  <div class="kpi"><div class="v" style="color:#16a34a">▼{improvement}%</div><div class="l">Loss reduction</div></div>
  <div class="kpi"><div class="v" style="color:#2563eb">{champ_best_loss}</div><div class="l">Champion loss</div></div>
  <div class="kpi"><div class="v">{corpus.get('total_rows',0):,}</div><div class="l">Rows processed</div></div>
  <div class="kpi"><div class="v">{corpus.get('unique_countries',0)}</div><div class="l">Markets</div></div>
  <div class="kpi"><div class="v" style="color:#7c3aed">{champ_strategy}</div><div class="l">Winning strategy</div></div>
</div>
<div class="section">
  <h2>Run Comparison</h2>
  <table><thead><tr><th>Run</th><th>Strategy</th><th>Best Loss</th><th>Convergence</th><th>Source</th></tr></thead>
  <tbody>{run_rows}</tbody></table>
</div>
{"<div class='section'><h2>Top Tracks (Source Data)</h2><table><thead><tr><th>#</th><th>Track</th><th>Artist</th><th>Total Streams</th><th>Peak Rank</th><th>Days on Chart</th></tr></thead><tbody>" + top_tracks_html + "</tbody></table></div>" if top_tracks_html else ""}
<div class="section">
  <h2>Reproduction Command</h2>
  <div class="repro">corvinctl reproduce pipeline_spotify_chart_pred --params {champ_run_id} --watermark {watermark}</div>
</div>
</body></html>"""
    return HTMLResponse(content=html)

# ══════════════════════════════════════════════════════════════════════════
# ── ADR-0090: Pipeline → awpkg Export (M2) ────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════

def _load_awp_exporter() -> type:
    """Lazy-import PipelineAWPExporter from the shared bridge module."""
    import sys as _sys
    _shared = Path(__file__).resolve().parents[4] / "operator" / "bridges" / "shared"
    if str(_shared) not in _sys.path:
        _sys.path.insert(0, str(_shared))
    from compute_awp_exporter import PipelineAWPExporter  # type: ignore[import]
    return PipelineAWPExporter

class AwpkgExportRequest(BaseModel):
    package_id: str = Field(..., pattern=r"^[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)+$",
                            max_length=128)
    version: str = Field("1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    mode: str = Field("replay", pattern=r"^(replay|reoptimize)$")
    include_sample_data: bool = True
    sample_rows: int = Field(100, ge=10, le=500)
    include_rag_manifests: bool = True
    include_fabric_datasources: bool = True
    include_output_datasources: bool = True
    include_watermarks: bool = False
    include_custom_adapters: bool = True
    include_ml_backends: bool = True
    schedule_cron: str | None = Field(None, max_length=100)
    schedule_timezone: str = Field("UTC", max_length=64)
    acceptance_criteria: dict[str, Any] | None = None
    model_config = {"extra": "forbid"}

@router.get("/compute/pipelines/{pipeline_id}/export/awpkg/preview")
def awpkg_export_preview(
    pipeline_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return what an awpkg export would contain without generating it (M2 preview)."""
    if "/" in pipeline_id or pipeline_id.startswith(".."):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid pipeline_id")
    tid = rec.tenant_id

    p_dir = _pipelines_dir(tid) / pipeline_id
    if not p_dir.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"pipeline {pipeline_id!r} not found")

    manifest = _read_json(p_dir / "manifest.json") or {}
    summary  = _read_json(p_dir / "pipeline_summary.json") or {}

    # Collect stage-level metadata for the preview
    # Guard: stages may be dicts or (malformed) strings
    raw_stages = manifest.get("stages", [])
    stages = [s for s in raw_stages if isinstance(s, dict)]
    tool_names = list({s.get("tool_name") or s.get("stage_id", "") for s in stages
                       if s.get("tool_name")})

    # Count datasources referenced across stages
    rag_providers: list[dict[str, Any]] = []
    fabric_ds: list[dict[str, Any]] = []
    output_ds: list[dict[str, Any]] = []
    secrets_required: list[str] = []

    # RAG: scan stage_summary.json for rag_providers_queried
    rag_dir = _forge_paths.tenant_home(tid) / "global" / "rag"
    seen_rag: set[str] = set()
    for s in stages:
        sid = s.get("stage_id", "")
        stage_dir = p_dir / "stages" / sid
        ss = _read_json(stage_dir / "stage_summary.json") or {}
        for pid in ss.get("rag_providers_queried", []):
            if pid not in seen_rag:
                seen_rag.add(pid)
                mf = rag_dir / f"{pid}.yaml"
                if mf.exists():
                    try:
                        import yaml as _y
                        data = _y.safe_load(mf.read_text(encoding="utf-8")) or {}
                        spec = data.get("spec", {})
                        env_var = spec.get("retrieval", {}).get("auth", {}).get("token_env_var")
                        rag_providers.append({
                            "provider_id": pid,
                            "classification": spec.get("dataClassification", "UNKNOWN"),
                            "zone": spec.get("complianceZone", "UNKNOWN"),
                        })
                        if env_var:
                            secrets_required.append(env_var)
                    except Exception:
                        rag_providers.append({"provider_id": pid, "classification": "UNKNOWN", "zone": "UNKNOWN"})

    # Fabric datasources: scan stage inputs
    ds_dir = _forge_paths.tenant_home(tid) / "datasource_connections"
    known_ds: set[str] = set()
    if ds_dir.exists():
        known_ds = {f.stem for f in ds_dir.iterdir() if f.suffix == ".json"}
    for s in stages:
        for v in (s.get("inputs") or {}).values():
            if isinstance(v, str) and v in known_ds:
                ds_json = _read_json(ds_dir / f"{v}.json") or {}
                auth = ds_json.get("auth", {})
                for k in auth.get("secret_keys", []):
                    if k not in secrets_required:
                        secrets_required.append(k)
                tags = ds_json.get("tags", [])
                role = "output" if "output" in tags else "input"
                entry = {
                    "name": v,
                    "adapter": ds_json.get("adapter", ""),
                    "region": (ds_json.get("source") or {}).get("region", ""),
                    "classification_inferred": "INTERNAL",
                    "has_watermark": bool(ds_json.get("incremental")),
                    "secret_key_count": len(auth.get("secret_keys", [])),
                }
                if role == "output":
                    output_ds.append(entry)
                else:
                    fabric_ds.append(entry)

    # Estimate size
    estimated_kb = max(5, len(stages) * 8 + len(tool_names) * 4 + len(rag_providers) * 3
                       + len(fabric_ds) * 3 + 10)

    return {
        "pipeline_id": pipeline_id,
        "stage_count": len(stages),
        "tool_names": tool_names,
        "dag_nodes": len(stages),
        "rag_providers": rag_providers,
        "fabric_datasources": fabric_ds,
        "output_datasources": output_ds,
        "ml_backend_count": 0,
        "custom_adapter_count": 0,
        "acceptance_criteria_stages": [],
        "schedule_detected": None,
        "secrets_required": secrets_required,
        "estimated_size_kb": estimated_kb,
        "mode_options": ["replay", "reoptimize"],
    }

@router.post("/compute/pipelines/{pipeline_id}/export/awpkg")
def awpkg_export_download(
    pipeline_id: str,
    body: AwpkgExportRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
):
    """Generate and stream the awpkg bundle as a zip download (M2 export, ADR-0090)."""
    import tempfile as _tmp
    from fastapi.responses import FileResponse
    if "/" in pipeline_id or pipeline_id.startswith(".."):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid pipeline_id")
    p_dir = _pipelines_dir(rec.tenant_id) / pipeline_id
    if not p_dir.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"pipeline {pipeline_id!r} not found")

    try:
        PipelineAWPExporter = _load_awp_exporter()
    except ImportError as exc:
        raise HTTPException(http_status.HTTP_503_SERVICE_UNAVAILABLE,
                            f"compute_awp_exporter not available: {exc}")

    out_dir = Path(_tmp.mkdtemp(prefix="awpkg_export_"))
    try:
        exporter = PipelineAWPExporter(
            tenant_id=rec.tenant_id,
            pipeline_id=pipeline_id,
        )
        meta = exporter.export(
            package_id=body.package_id,
            version=body.version,
            mode=body.mode,
            include_sample_data=body.include_sample_data,
            sample_rows=body.sample_rows,
            include_rag_manifests=body.include_rag_manifests,
            include_fabric_datasources=body.include_fabric_datasources,
            include_output_datasources=body.include_output_datasources,
            include_watermarks=body.include_watermarks,
            include_custom_adapters=body.include_custom_adapters,
            include_ml_backends=body.include_ml_backends,
            schedule_cron=body.schedule_cron,
            schedule_timezone=body.schedule_timezone,
            acceptance_criteria=body.acceptance_criteria,
            output_dir=out_dir,
        )
        # Find the generated zip (search recursively — exporter may nest under a subdir)
        zip_files = list(out_dir.rglob("*.awpkg"))
        if not zip_files:
            zip_files = list(out_dir.rglob("*.zip"))
        if not zip_files:
            raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                                "export produced no zip file")
        zip_path = zip_files[0]
        safe_pkg = body.package_id.replace("/", "_").replace('"', "").replace("\r", "").replace("\n", "")
        download_name = f"{safe_pkg}-{body.version}.awpkg"

        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="compute.pipeline_export_download",
            target_kind="compute_pipeline",
            target_id=pipeline_id,
        )

        # StreamingResponse to allow cleanup after send
        from fastapi.responses import StreamingResponse
        import io as _io

        zip_bytes = zip_path.read_bytes()
        return StreamingResponse(
            _io.BytesIO(zip_bytes),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                            f"export failed: {exc}") from exc
    finally:
        import shutil as _sh
        _sh.rmtree(out_dir, ignore_errors=True)

# ── M9: Champion Promotion endpoint ──────────────────────────────────────

class PromoteChampionRequest(BaseModel):
    run_id: str = Field(..., pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    package_id: str = Field(..., pattern=r"^[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)+$",
                            max_length=128)
    current_version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$")
    improvement_threshold_pct: float = Field(2.0, ge=0.0, le=100.0)
    model_config = {"extra": "forbid"}

@router.post("/compute/pipelines/{pipeline_id}/promote-champion")
def promote_champion(
    pipeline_id: str,
    body: PromoteChampionRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Promote the champion run's parameters to a new awpkg version (M9, ADR-0090)."""
    if "/" in pipeline_id or pipeline_id.startswith(".."):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid pipeline_id")
    if "/" in body.run_id or body.run_id.startswith(".."):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid run_id")

    tid = rec.tenant_id
    p_dir = _pipelines_dir(tid) / pipeline_id
    if not p_dir.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"pipeline {pipeline_id!r} not found")

    run_dir = _runs_dir(tid) / body.run_id
    if not run_dir.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"run {body.run_id!r} not found")

    run_summary = _read_json(run_dir / "summary.json") or {}
    new_best_loss = run_summary.get("best_loss")
    if new_best_loss is None:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST,
                            "run has no best_loss — cannot compute improvement")

    # Read current champion from pipeline summary
    pipe_summary = _read_json(p_dir / "pipeline_summary.json") or {}
    best_losses = pipe_summary.get("best_losses", {})
    if best_losses:
        current_best: float | None = min(best_losses.values())
    else:
        current_best = None  # no prior champion

    if current_best is not None and current_best <= 0:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "current champion loss is 0 or negative")

    if current_best is None:
        # No prior champion — first promotion always succeeds
        improvement_pct = None
    else:
        improvement_pct = round((1 - new_best_loss / current_best) * 100, 1)
        if improvement_pct < body.improvement_threshold_pct:
            return {
                "promoted": False,
                "reason": f"improvement {improvement_pct:.1f}% < threshold {body.improvement_threshold_pct:.1f}%",
                "new_best_loss": new_best_loss,
                "current_best_loss": current_best,
                "improvement_pct": improvement_pct,
            }

    # Bump patch version
    parts = body.current_version.split(".")
    new_version = f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"

    console_audit.action_performed(
        tenant_id=tid,
        sid_fingerprint=rec.sid_fingerprint,
        action="compute.champion_promoted",
        target_kind="compute_pipeline",
        target_id=pipeline_id,
    )

    return {
        "promoted": True,
        "run_id": body.run_id,
        "new_version": new_version,
        "new_best_loss": new_best_loss,
        "current_best_loss": current_best,  # None if no prior champion
        "improvement_pct": improvement_pct,  # None if first promotion
        "next_step": f"Re-export with version={new_version} to update the awpkg bundle",
    }

# ── M2: Pipeline → awpkg → Workflow import ────────────────────────────────

@router.post("/compute/pipelines/{pipeline_id}/export/awpkg/to-workflow")
def awpkg_export_to_workflow(
    pipeline_id: str,
    body: AwpkgExportRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Export pipeline as awpkg and import it directly into the Workflows system (ADR-0090)."""
    import io as _io
    import re as _re
    import shutil as _sh
    import tempfile as _tmp
    import time as _time
    import zipfile as _zf

    if "/" in pipeline_id or pipeline_id.startswith(".."):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid pipeline_id")
    p_dir = _pipelines_dir(rec.tenant_id) / pipeline_id
    if not p_dir.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"pipeline {pipeline_id!r} not found")

    try:
        PipelineAWPExporter = _load_awp_exporter()
    except ImportError as exc:
        raise HTTPException(http_status.HTTP_503_SERVICE_UNAVAILABLE,
                            f"compute_awp_exporter not available: {exc}")

    # ── Step 1: generate awpkg zip ─────────────────────────────────────────
    out_dir = Path(_tmp.mkdtemp(prefix="awpkg_to_wf_"))
    try:
        exporter = PipelineAWPExporter(
            tenant_id=rec.tenant_id,
            pipeline_id=pipeline_id,
        )
        exporter.export(
            package_id=body.package_id,
            version=body.version,
            mode=body.mode,
            include_sample_data=body.include_sample_data,
            sample_rows=body.sample_rows,
            include_rag_manifests=body.include_rag_manifests,
            include_fabric_datasources=body.include_fabric_datasources,
            include_output_datasources=body.include_output_datasources,
            include_watermarks=body.include_watermarks,
            include_custom_adapters=body.include_custom_adapters,
            include_ml_backends=body.include_ml_backends,
            schedule_cron=body.schedule_cron,
            schedule_timezone=body.schedule_timezone,
            acceptance_criteria=body.acceptance_criteria,
            output_dir=out_dir,
        )
        zip_files = list(out_dir.rglob("*.awpkg"))
        if not zip_files:
            zip_files = list(out_dir.rglob("*.zip"))
        if not zip_files:
            raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                                "export produced no zip file")
        zip_bytes = zip_files[0].read_bytes()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                            f"awpkg export failed: {exc}") from exc
    finally:
        _sh.rmtree(out_dir, ignore_errors=True)

    # ── Step 2: extract workflow YAML from zip ─────────────────────────────
    try:
        with _zf.ZipFile(_io.BytesIO(zip_bytes)) as zf:
            yaml_names = [n for n in zf.namelist() if n.endswith(".awp.yaml")]
            if not yaml_names:
                raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                                    "awpkg bundle contains no .awp.yaml")
            yaml_content = zf.read(yaml_names[0]).decode("utf-8")
    except _zf.BadZipFile as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                            "generated awpkg is not a valid zip") from exc

    # ── Step 3: derive workflow id from YAML name field ────────────────────
    _WID_RE = _re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
    wid: str | None = None
    workflow_name: str | None = None
    if _YAML_OK:
        try:
            parsed = _yaml.safe_load(yaml_content) or {}
            wid = parsed.get("workflow", {}).get("name") or None
            workflow_name = wid
        except Exception:
            pass
    if not wid:
        wid = _re.sub(r"[^a-z0-9_-]", "_", pipeline_id.lower())[:63] or "pipeline"
    if not _WID_RE.match(wid):
        wid = "workflow_" + _re.sub(r"[^a-z0-9]", "_", wid)[:55]
    if not workflow_name:
        workflow_name = wid

    # ── Step 4: avoid collision ────────────────────────────────────────────
    _wf_base = _forge_paths.tenant_home(rec.tenant_id) / "workflows"
    _wf_base.mkdir(parents=True, exist_ok=True)

    def _meta_p(w: str) -> Path:
        return _wf_base / f"{w}.meta.json"

    def _yaml_p(w: str) -> Path:
        return _wf_base / f"{w}.awp.yaml"

    base_wid = wid
    counter = 0
    while _meta_p(wid).exists() and counter < 100:
        counter += 1
        wid = f"{base_wid}_{counter}"
    if counter >= 100:
        raise HTTPException(409, "too many workflows with this name")

    # ADR-0094: enforce workflows_max before creating a new workflow via
    # compute→awpkg→workflow promotion (this path bypassed the regular create
    # route's licence check).
    _wf_max = _lic_get_limit("workflows_max")
    if _wf_max is not None:
        _existing_wf = sum(1 for _ in _wf_base.glob("*.meta.json")) if _wf_base.exists() else 0
        if _existing_wf >= _wf_max:
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "license_limit",
                    "feature": "workflows_max",
                    "existing": _existing_wf,
                    "msg": f"Free tier: maximum {_wf_max} workflow(s). Upgrade to add more.",
                    "upgrade_url": "https://corvin-labs.com/pricing",
                },
            )

    # ── Step 5: write workflow files atomically ────────────────────────────
    import tempfile as _tmp2

    def _write_atomic_local(path: Path, data: "dict[str, Any] | str") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = _tmp2.mkstemp(dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                if isinstance(data, str):
                    fh.write(data)
                else:
                    import json as _json
                    _json.dump(data, fh, ensure_ascii=False)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    now = _time.time()
    meta: dict[str, Any] = {
        "id": wid,
        "title": workflow_name.replace("_", " ").title(),
        "description": "",
        "phase": "ready",
        "created_at": now,
        "updated_at": now,
        "has_schedule": False,
        "imported_from": f"{body.package_id}-{body.version}.awpkg",
        "source": "compute_pipeline",
        "pipeline_id": pipeline_id,
    }
    _write_atomic_local(_yaml_p(wid), yaml_content)
    _write_atomic_local(_meta_p(wid), meta)

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="compute.pipeline_exported_to_workflow",
        target_kind="compute_pipeline",
        target_id=pipeline_id,
    )

    return {
        "ok": True,
        "workflow_id": wid,
        "workflow_name": workflow_name,
        "redirect_url": f"/app/workflows/{wid}",
    }

# ══════════════════════════════════════════════════════════════════════════
# ── M7: Stage Chart Viewer (ADR-0088 M7) ──────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════

_IMAGE_MIME: dict[str, str] = {
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif":  "image/gif",
    ".webp": "image/webp",
    ".svg":  "image/svg+xml",
    ".pdf":  "application/pdf",
}

_IMAGE_EXTENSIONS = frozenset(_IMAGE_MIME)

@router.get("/compute/pipelines/{pipeline_id}/stages/{stage_id}/artifact-image/{filename}")
def stage_artifact_image(
    pipeline_id: str,
    stage_id: str,
    filename: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
):
    """Serve a stage image artifact inline (ADR-0088 M7)."""
    from fastapi.responses import FileResponse

    for v in (pipeline_id, stage_id):
        if "/" in v or v.startswith(".."):
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid id")

    # Validate filename: alphanumeric, dots, underscores, hyphens only
    import re as _re
    if not _re.fullmatch(r"[A-Za-z0-9_.\-]{1,128}", filename):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid filename")

    ext = Path(filename).suffix.lower()
    if ext not in _IMAGE_EXTENSIONS:
        raise HTTPException(http_status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            f"not an image or PDF: {ext}")

    tid = rec.tenant_id
    artifact_path = (
        _pipelines_dir(tid) / pipeline_id / "stages" / stage_id / "artifacts" / filename
    )
    compute_root = _compute_dir(tid)

    # Path traversal guard
    try:
        artifact_path.resolve().relative_to(compute_root.resolve())
    except ValueError:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "path outside compute directory")

    if not artifact_path.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"artifact {filename!r} not found")

    mime = _IMAGE_MIME.get(ext, "application/octet-stream")
    safe_fn = filename.replace('"', "").replace("\r", "").replace("\n", "")

    console_audit.action_performed(
        tenant_id=tid,
        sid_fingerprint=rec.sid_fingerprint,
        action="compute.stage_image_served",
        target_kind="compute_stage",
        target_id=f"{pipeline_id}/{stage_id}",
    )

    return FileResponse(
        str(artifact_path),
        media_type=mime,
        headers={
            "Content-Disposition": f'inline; filename="{safe_fn}"',
            "Cache-Control": "private, max-age=3600",
        },
    )

@router.get("/compute/pipelines/{pipeline_id}/stages/{stage_id}/artifact-images")
def stage_artifact_images_list(
    pipeline_id: str,
    stage_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """List image/PDF artifacts for a stage (ADR-0088 M7)."""
    for v in (pipeline_id, stage_id):
        if "/" in v or v.startswith(".."):
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid id")

    tid = rec.tenant_id
    artifacts_dir = _pipelines_dir(tid) / pipeline_id / "stages" / stage_id / "artifacts"

    images: list[dict[str, Any]] = []
    if artifacts_dir.is_dir():
        try:
            for f in sorted(artifacts_dir.iterdir()):
                ext = f.suffix.lower()
                if f.is_file() and ext in _IMAGE_EXTENSIONS:
                    stat = f.stat()
                    # Look for pre-generated thumbnail
                    thumb_name = f"{f.stem}_thumb{f.suffix}"
                    thumb_path = artifacts_dir / thumb_name
                    images.append({
                        "filename": f.name,
                        "mime_type": _IMAGE_MIME.get(ext, "application/octet-stream"),
                        "size_bytes": stat.st_size,
                        "thumbnail_filename": thumb_name if thumb_path.exists() else None,
                        "stage_id": stage_id,
                        "pipeline_id": pipeline_id,
                    })
        except OSError:
            pass

    return {
        "pipeline_id": pipeline_id,
        "stage_id": stage_id,
        "images": images,
        "count": len(images),
    }

# ══════════════════════════════════════════════════════════════════════════
# ── Experiment Narrative + Voice (L25 extension) ──────────────────────────
# ══════════════════════════════════════════════════════════════════════════

@router.get("/compute/runs/{run_id}/narrative")
def compute_run_narrative(
    run_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    force: bool = False,
    locale: str = "de",
) -> dict[str, Any]:
    """Generate (or return cached) spoken narrative for a compute run.

    Narrative is generated lazily by Haiku-4.5 via the ``claude`` CLI.
    Result cached in ``<run_dir>/narrative.json`` (mode 0600).
    Audit: ``compute.narrative_generated`` — run_id + model only, no text.
    """
    if not run_id or "/" in run_id or run_id.startswith("..") or run_id == ".":
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid run_id")
    run_dir = _runs_dir(rec.tenant_id) / run_id
    if not run_dir.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"run {run_id!r} not found")

    _shared = _REPO / "operator" / "bridges" / "shared"
    if str(_shared) not in sys.path:
        sys.path.insert(0, str(_shared))
    try:
        from compute_narrator import narrate_run  # type: ignore[import]
    except ImportError as exc:
        raise HTTPException(http_status.HTTP_503_SERVICE_UNAVAILABLE,
                            f"compute_narrator not available: {exc}")

    # ADR-0150 LIC-NARRATE-SPAWN-01: a cache MISS or client force=true spawns a
    # paid Haiku `claude -p`. Charge chat_turns_per_day ONLY when a spawn will
    # actually occur (a true cache hit stays free), so the client-bustable force
    # flag cannot loop unbounded paid spawns.
    if force or not (run_dir / "narrative.json").exists():
        from ._compute_license_gate import enforce_chat_turns  # noqa: PLC0415
        enforce_chat_turns(rec.tenant_id, rec.sid_fingerprint,
                           audit_action="compute.narrative", channel="narrative")

    # When force-regenerating narrative text, delete the stale audio file so the
    # next /voice request re-synthesises from the fresh narrative.
    if force:
        _stale_audio = run_dir / "narrative.ogg"
        try:
            _stale_audio.unlink(missing_ok=True)
        except OSError:
            pass

    result = narrate_run(run_dir, locale=locale, force=force)
    if result is None:
        raise HTTPException(http_status.HTTP_503_SERVICE_UNAVAILABLE,
                            "narrative generation unavailable — is the claude CLI installed?")

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="compute.narrative_generated",
        target_kind="compute_run",
        target_id=run_id,
    )
    return result

def _get_narrate_run():
    """Return the narrate_run callable, adding shared dir to sys.path if needed."""
    _shared = _REPO / "operator" / "bridges" / "shared"
    if str(_shared) not in sys.path:
        sys.path.insert(0, str(_shared))
    try:
        from compute_narrator import narrate_run  # type: ignore[import]
        return narrate_run
    except ImportError as exc:
        raise HTTPException(http_status.HTTP_503_SERVICE_UNAVAILABLE,
                            f"compute_narrator not available: {exc}")

@router.get("/compute/runs/{run_id}/voice")
def compute_run_voice(
    run_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    force: bool = False,
):
    """Return TTS audio for a run's narrative.

    Calls ``say.py`` (OpenAI → edge-tts → Piper chain).  Audio is cached
    as ``<run_dir>/narrative.ogg`` so repeat calls are instant.
    Returns ``audio/ogg``, ``audio/mpeg``, or ``audio/wav`` depending on
    the provider that succeeded.
    """
    import subprocess as _sp
    from fastapi.responses import FileResponse

    if not run_id or "/" in run_id or run_id.startswith("..") or run_id == ".":
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid run_id")
    run_dir = _runs_dir(rec.tenant_id) / run_id
    if not run_dir.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"run {run_id!r} not found")

    audio_path = run_dir / "narrative.ogg"

    if not audio_path.exists() or force:
        # ADR-0150 LIC-NARRATE-SPAWN-01: narrate_run spawns paid Haiku `claude -p`
        # only on a narrative-cache miss or force — charge chat_turns_per_day then
        # (not for pure TTS regeneration of an already-narrated run).
        if force or not (run_dir / "narrative.json").exists():
            from ._compute_license_gate import enforce_chat_turns  # noqa: PLC0415
            enforce_chat_turns(rec.tenant_id, rec.sid_fingerprint,
                               audit_action="compute.voice", channel="narrative")
        narrate_run = _get_narrate_run()
        narrative = narrate_run(run_dir, force=force)
        if narrative is None:
            raise HTTPException(http_status.HTTP_503_SERVICE_UNAVAILABLE,
                                "narrative not available")

        text = narrative.get("text", "")
        lang = narrative.get("lang", "de")

        say_script = _REPO / "operator" / "voice" / "scripts" / "say.py"
        if not say_script.exists():
            # Wheel install: operator/ is vendored under _vendor/operator.
            try:
                from .._operator_bootstrap import vendor_operator_root  # noqa: PLC0415
                _vr = vendor_operator_root()
            except Exception:  # noqa: BLE001
                _vr = None
            if _vr is not None and (_vr / "voice" / "scripts" / "say.py").exists():
                say_script = _vr / "voice" / "scripts" / "say.py"
        if not say_script.exists():
            raise HTTPException(http_status.HTTP_503_SERVICE_UNAVAILABLE,
                                "say.py not found — TTS unavailable")
        try:
            result = _sp.run(
                [sys.executable, str(say_script), str(audio_path), text, lang],
                capture_output=True, text=True, timeout=60,
            )
        except _sp.TimeoutExpired:
            raise HTTPException(http_status.HTTP_503_SERVICE_UNAVAILABLE, "TTS timed out")

        if not audio_path.exists():
            raise HTTPException(http_status.HTTP_503_SERVICE_UNAVAILABLE,
                                "TTS synthesis failed — check TTS provider config")

    # Detect MIME type from magic bytes so the browser picks the right codec.
    media_type = "audio/ogg"
    try:
        header = audio_path.read_bytes()[:4]
        if header[:3] == b"ID3" or (header[0] == 0xFF and header[1] & 0xE0 == 0xE0):
            media_type = "audio/mpeg"
        elif header[:4] == b"RIFF":
            media_type = "audio/wav"
    except OSError:
        pass

    return FileResponse(str(audio_path), media_type=media_type)

# ══════════════════════════════════════════════════════════════════════════
# ── ACS Engine — Autonomous Compute Shell (ADR-0104, second engine)  ──────
# ══════════════════════════════════════════════════════════════════════════
#
# Runs alongside the existing L25 Compute Worker (parameter sweeps).
# ACS handles agentic decision loops (delegation_loop engine type).
# Engine selection is spec-driven: a workflow with
# ``orchestration.engine: delegation_loop`` dispatches here.

_ACS_SHARED = _REPO / "operator" / "bridges" / "shared"
if str(_ACS_SHARED) not in sys.path:
    sys.path.insert(0, str(_ACS_SHARED))

try:
    from acs_engine_adapter import (  # type: ignore
        run_acs_workflow as _run_acs_workflow,
        list_acs_runs as _list_acs_runs,
        get_acs_run as _get_acs_run,
        export_acs_run_as_awpkg as _export_acs_run_as_awpkg,
    )
    _ACS_ENGINE_OK = True
except ImportError:
    _ACS_ENGINE_OK = False

def _acs_runs_dir(tid: str) -> Path:
    return _forge_paths.tenant_home(tid) / "global" / "acs" / "runs"

@router.get("/compute/acs")
def list_acs_workflow_runs(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    limit: int = 50,
) -> dict[str, Any]:
    """List recent ACS workflow runs for this tenant."""
    tid = rec.tenant_id
    if not _ACS_ENGINE_OK:
        return {"engine": "acs", "available": False, "runs": []}

    runs = _list_acs_runs(tenant_id=tid)
    return {
        "engine": "acs",
        "available": True,
        "run_count": len(runs),
        "runs": runs[:max(1, min(limit, 200))],
    }

@router.get("/compute/acs/{run_id}")
def get_acs_workflow_run(
    run_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Get status and result for a single ACS run."""
    if not run_id or run_id in (".", "..") or "/" in run_id or run_id.startswith(".."):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid run_id")
    if not _ACS_ENGINE_OK:
        raise HTTPException(http_status.HTTP_503_SERVICE_UNAVAILABLE,
                            "ACS engine not available on this installation")
    data = _get_acs_run(run_id, tenant_id=rec.tenant_id)
    if data is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"ACS run {run_id!r} not found")
    return data

class ACSRunRequest(BaseModel):
    workflow_path: str = Field(..., description="Path to .awp.yaml workflow file")
    inputs: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = Field(False, description="Validate only — no workers spawned")
    budget_override: dict[str, Any] | None = Field(
        None,
        description="Override delegation_loop.budget fields (e.g. max_loops, max_depth)",
    )

@router.post("/compute/acs/runs")
def submit_acs_workflow_run(
    body: ACSRunRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Submit a delegation_loop workflow to the ACS engine.

    The workflow is dispatched synchronously (returns when the run finishes).
    For long-running workflows use ``dry_run=true`` to validate first, then
    decide whether to trigger the full run via the CLI or a background job.
    """
    if not _ACS_ENGINE_OK:
        raise HTTPException(http_status.HTTP_503_SERVICE_UNAVAILABLE,
                            "ACS engine not installed on this installation")

    # Path-traversal guard: only absolute paths or paths under the tenant home.
    wf_path = body.workflow_path.strip()
    if not wf_path:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "workflow_path is required")
    if ".." in wf_path:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "path traversal rejected")

    _wf_audit_id = hashlib.sha256(wf_path.encode()).hexdigest()[:16]

    # ADR-0146 CON-ACS-01 / WF-CONC-01: ACS runs spawn real LLM compute
    # (claude -p managers + Haiku workers) and were a SECOND, ungated compute
    # execution path that bypassed the compute_units_per_day cap enforced on
    # POST /compute/runs. Enforce the shared fail-closed quota gate here too, so
    # a free-tier user cannot loop this route for unbounded paid compute. The
    # FREE_TIER cap (1/day) also bounds concurrent ACS runs. dry_run spawns no
    # workers, so it is exempt.
    if not body.dry_run:
        from ._compute_license_gate import enforce_compute_quota  # noqa: PLC0415

        enforce_compute_quota(
            rec.tenant_id, rec.sid_fingerprint, audit_action="acs.run_submit",
        )

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="acs.run_submit",
        target_kind="acs_workflow",
        target_id=_wf_audit_id,
    )

    try:
        result = _run_acs_workflow(
            wf_path,
            inputs=body.inputs or None,
            tenant_id=rec.tenant_id,
            dry_run=body.dry_run,
            budget_override=body.budget_override,
            # This route already charged the daily quota via enforce_compute_quota
            # above (and returns a 402); the chokepoint must NOT double-count.
            charge_quota=False,
        )
    except Exception as e:  # noqa: BLE001
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="acs.run_submit",
            target_kind="acs_workflow",
            target_id=_wf_audit_id,
            reason="internal_error",
        )
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                            f"ACS run failed: {type(e).__name__}") from e

    # L44 acceptable-use refusal (ADR-0143): the run was blocked by the
    # house-rules gate at the ACSRuntime.run chokepoint (run_acs_workflow →
    # ACSRuntime.run), which is fail-closed + audit-first — it already emitted
    # the house_rules.{denied,escalated} L16 event before returning the refusal.
    # Surface it to the caller as a 403 with the user-facing refusal string
    # (metadata-only console audit: reason code, never the workflow goal text).
    _err = result.get("error") or ""
    if (
        not body.dry_run
        and result.get("status") == "failed"
        and isinstance(_err, str)
        and _err.startswith("[house-rules]")
    ):
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="acs.run_submit",
            target_kind="acs_workflow",
            target_id=_wf_audit_id,
            reason="house_rules_blocked",
        )
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, _err)

    if result.get("status") == "failed" and not body.dry_run:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="acs.run_submit",
            target_kind="acs_workflow",
            target_id=_wf_audit_id,
            reason="workflow_failed",
        )

    return result

class ACSExportRequest(BaseModel):
    mode: str = Field("dag", description="Export mode: 'dag' for deterministic replay, 'template' for adaptive re-exploration")
    description: str = Field("", description="Optional description override for the generated workflow")

@router.post("/compute/acs/{run_id}/export")
def export_acs_workflow_run(
    run_id: str,
    body: ACSExportRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> Response:
    """Export an ACS run's discovered execution graph as a portable AWPKG archive.

    The AWPKG contains:
    * ``workflows/discovered-<id>.awp.yaml`` — reconstructed AWP workflow
    * ``provenance/acs_manifest.json`` — original run metadata
    * ``provenance/gate_results.json`` — gate chain evaluations
    * ``provenance/quality.json`` — quality aggregate scores

    Mode ``"dag"`` produces a static DAG (deterministic replay via corvin-workflow run).
    Mode ``"template"`` produces a delegation_loop seed for adaptive re-exploration.
    """
    if not run_id or run_id in (".", "..") or "/" in run_id or run_id.startswith(".."):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid run_id")
    if body.mode not in ("dag", "template"):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "mode must be 'dag' or 'template'")
    if not _ACS_ENGINE_OK:
        raise HTTPException(http_status.HTTP_503_SERVICE_UNAVAILABLE,
                            "ACS engine not available on this installation")

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="acs.export",
        target_kind="acs_run",
        target_id=run_id[:64],
    )

    result = _export_acs_run_as_awpkg(
        run_id,
        tenant_id=rec.tenant_id,
        mode=body.mode,
        description=body.description or "",
    )

    if not result.get("ok"):
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="acs.export",
            target_kind="acs_run",
            target_id=run_id[:64],
            reason="export_failed",
        )
        raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                            result.get("error", "export failed"))

    pkg_bytes: bytes = result["bytes"]
    filename: str = result["filename"]
    # Strip path separators, quotes, and CRLF to prevent Content-Disposition header injection.
    safe_filename = (
        filename.replace('"', "").replace("\\", "").replace("/", "_")
        .replace("\r", "").replace("\n", "")
    )
    return Response(
        content=pkg_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_filename}"',
            "X-ACS-Node-Count": str(result.get("node_count", 0)),
            "X-ACS-Quality-Score": str(round(result.get("quality_aggregate", 0.0), 3)),
        },
    )

# ── Compute Graph (ADR-0107 M1a) ─────────────────────────────────────────────

def _l25_loss_hue(loss: float, loss_min: float, loss_max: float) -> int:
    """Best (lowest) loss → 220° (AWP blue), worst (highest) → 0° (AWP red)."""
    if loss_max <= loss_min:
        return 110
    normalized = (loss - loss_min) / (loss_max - loss_min)
    return max(0, min(220, int(220 * (1.0 - normalized))))

def _build_l25_graph(run_dir: "Path") -> "dict[str, Any]":
    """Build vis.js-compatible graph payload for an L25 hyperparameter run."""
    manifest = _read_json(run_dir / "manifest.json") or {}
    summary = _read_json(run_dir / "summary.json") or {}

    run_id = manifest.get("run_id", run_dir.name)
    strategy = str(manifest.get("strategy", "unknown"))
    tool_name = str(manifest.get("tool_name", ""))
    state = str(summary.get("state", "unknown"))
    best_iter = summary.get("best_iter")
    best_loss = summary.get("best_loss")
    convergence = str(summary.get("convergence_reason", "") or "")
    best_params = summary.get("best_params") or {}

    # Load iteration files
    iters_dir = run_dir / "iterations"
    iter_records: list[dict[str, Any]] = []
    if iters_dir.is_dir():
        try:
            files = sorted(
                (f for f in iters_dir.iterdir() if f.suffix == ".json"),
                key=lambda f: int("".join(c for c in f.stem if c.isdigit()) or "0"),
            )
            for f in files:
                d = _read_json(f)
                if d:
                    iter_records.append(d)
        except OSError:
            pass

    losses = [float(d["loss"]) for d in iter_records if isinstance(d.get("loss"), (int, float))]
    loss_min = min(losses) if losses else 0.0
    loss_max = max(losses) if losses else 1.0

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    _font = {"color": "#c9d1d9", "size": 11, "face": "monospace"}

    # Level 0 — Run root
    nodes.append({
        "id": "run_root",
        "label": f"Run\n{run_id[-14:]}",
        "shape": "diamond",
        "color": "#40C4FF",
        "size": 40,
        "level": 0,
        "group": "run",
        "font": _font,
        "title": (
            f"<b>Compute Run</b><br>ID: {run_id}<br>"
            f"Tool: {tool_name or '—'}<br>State: {state}<br>"
            f"Iterations: {len(iter_records)}"
        ),
    })

    # Level 1 — Strategy
    best_loss_str = f"{best_loss:.4g}" if isinstance(best_loss, (int, float)) else "—"
    nodes.append({
        "id": "strategy_node",
        "label": f"Strategy\n{strategy}",
        "shape": "star",
        "color": "#E040FB",
        "size": 30,
        "level": 1,
        "group": "strategy",
        "font": _font,
        "title": (
            f"<b>Search Strategy: {strategy}</b><br>"
            f"Iterations: {len(iter_records)}<br>"
            f"Best loss: {best_loss_str}<br>"
            f"Stop reason: {convergence or '—'}"
        ),
    })
    edges.append({"from": "run_root", "to": "strategy_node",
                  "color": "#E040FB", "width": 2, "dashes": False})

    # Level 2 — Iteration boxes
    best_iter_id: str | None = None
    for d in iter_records:
        iter_num = int(d.get("iter") or d.get("iteration") or 0)
        loss = d.get("loss")
        params = d.get("params") or {}
        error = d.get("error")
        is_best = best_iter is not None and iter_num == int(best_iter)
        iter_id = f"iter_{iter_num:03d}"
        if is_best:
            best_iter_id = iter_id

        if error:
            bg, border = "#FF1744", "#FF1744"
            label = f"#{iter_num}\nFAILED"
        elif loss is None:
            bg, border = "#40C4FF", "#888"
            label = f"#{iter_num}\n..."
        else:
            hue = _l25_loss_hue(float(loss), loss_min, loss_max)
            bg = f"hsl({hue},70%,45%)"
            border = "#FFD700" if is_best else "#666"
            loss_s = f"{float(loss):.4g}"
            label = f"#{iter_num}\n{loss_s}" + (" ★" if is_best else "")

        params_html = "<br>".join(f"&nbsp;{k}: {v}" for k, v in list(params.items())[:8])
        nodes.append({
            "id": iter_id,
            "label": label,
            "shape": "box",
            "color": {"background": bg, "border": border,
                      "highlight": {"background": bg, "border": "#fff"}},
            "size": 22,
            "level": 2,
            "group": "best_iter" if is_best else "iteration",
            "borderWidth": 4 if is_best else 1,
            "font": _font,
            "title": (
                f"<b>Iteration {iter_num}</b><br>"
                f"Loss: {loss if loss is not None else '—'}<br>"
                + (f"Error: {error}<br>" if error else "")
                + f"Params:<br>{params_html or '—'}"
            ),
        })
        edges.append({
            "from": "strategy_node", "to": iter_id,
            "color": "#FF1744" if error else "#00E676",
            "width": 1.5, "dashes": True,
            "label": str(iter_num),
            "font": {"color": "#8b949e", "size": 9, "face": "monospace"},
        })

    # Level 3 — Best params node (best iteration only)
    if best_iter_id and best_params:
        params_label = "\n".join(f"{k}: {v}" for k, v in list(best_params.items())[:5])
        params_html = "<br>".join(f"&nbsp;{k}: {v}" for k, v in best_params.items())
        nodes.append({
            "id": "best_params_node",
            "label": f"Best Params\n{params_label}",
            "shape": "dot",
            "color": "#FFD700",
            "size": 22,
            "level": 3,
            "group": "best_params",
            "borderWidth": 3,
            "font": _font,
            "title": f"<b>Best Parameters</b><br>{params_html}",
        })
        edges.append({"from": best_iter_id, "to": "best_params_node",
                      "color": "#FFD700", "width": 2, "dashes": False})

    # Completion node — a flat ComputeRun NEVER reaches a "complete" state; its
    # successful terminal states are converged / stalled / budget_exhausted (only
    # failed / aborted are real failures). The old `state == "complete"` check was
    # never true, so every successful run's Result node rendered red with a
    # "failed"-styled edge. Mirror the frontend isRunDone success set instead.
    _SUCCESS_STATES = {"converged", "stalled", "budget_exhausted", "complete"}
    comp_color = "#00E676" if state in _SUCCESS_STATES else "#FF1744"
    comp_level = 4 if (best_iter_id and best_params) else 3
    nodes.append({
        "id": "completion",
        "label": f"Result\n{state}",
        "shape": "box",
        "color": comp_color,
        "size": 25,
        "level": comp_level,
        "group": "completion",
        "font": _font,
        "title": (
            f"<b>Completion</b><br>Status: {state}<br>"
            f"Best iter: {best_iter or '—'}<br>"
            f"Best loss: {best_loss_str}<br>"
            f"Stop: {convergence or '—'}"
        ),
    })
    edges.append({"from": "run_root", "to": "completion",
                  "color": comp_color, "width": 2,
                  "dashes": state not in _SUCCESS_STATES})

    return {
        "mode": "l25",
        "strategy": strategy,
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "loss_min": loss_min,
            "loss_max": loss_max,
            "best_iter": best_iter,
            "n_iters": len(iter_records),
            "state": state,
        },
    }

@router.get("/compute/runs/{run_id}/graph")
def compute_run_graph(
    run_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return vis.js-compatible graph data for an L25 compute run (ADR-0107 M1a)."""
    if not run_id or "/" in run_id or run_id.startswith("..") or run_id == ".":
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid run_id")
    run_dir = _runs_dir(rec.tenant_id) / run_id
    if not run_dir.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"run {run_id!r} not found")

    try:
        payload = _build_l25_graph(run_dir)
    except Exception as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                            f"graph build failed: {exc}")

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="compute.graph_viewed",
        target_kind="compute_run",
        target_id=run_id,
    )
    return payload

# ── ACS Graph (ADR-0107 M1b) ──────────────────────────────────────────────────

def _acs_confidence_color(confidence: float) -> str:
    """AWP confidence colour: green >=0.8, yellow 0.5-0.8, orange 0.3-0.5, red <0.3."""
    if confidence >= 0.8:
        return "#00E676"
    if confidence >= 0.5:
        return "#FFC107"
    if confidence >= 0.3:
        return "#FF9100"
    return "#FF1744"

def _build_acs_graph(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Build vis.js-compatible graph payload for an ACS workflow run."""
    result = _read_json(run_dir / "result.json") or {}

    run_id = str(manifest.get("run_id", ""))
    workflow_id = str(manifest.get("workflow_id", ""))
    status = str(manifest.get("status", result.get("status", "unknown")))
    total_iters = int(manifest.get("iterations", 0))
    workers_spawned = int(manifest.get("workers_spawned", 0))
    duration_s = float(manifest.get("duration_s", result.get("elapsed_s", 0)))
    _final_output = result.get("final_output") or {}
    quality_score: float | None = (
        float(_final_output["quality_score"]) if "quality_score" in _final_output else None
    )

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    _font = {"color": "#c9d1d9", "size": 11, "face": "monospace"}

    # Level 0 — ACS Task Root
    nodes.append({
        "id": "task_root",
        "label": f"Task\n{workflow_id[:20]}",
        "shape": "diamond",
        "color": "#40C4FF",
        "size": 45,
        "level": 0,
        "group": "task",
        "font": _font,
        # Structured fields for frontend tooltip
        "run_id": run_id,
        "workflow_id": workflow_id,
        "run_status": status,
        "total_iters": total_iters,
        "workers_spawned": workers_spawned,
        "duration_s": round(duration_s, 1),
    })

    # Level 1 — Manager Agent
    manager_model = str(manifest.get("engine", "acs"))
    nodes.append({
        "id": "mgr_1",
        "label": f"Manager\n{manager_model}",
        "shape": "star",
        "color": "#E040FB",
        "size": 35,
        "level": 1,
        "group": "manager",
        "font": _font,
        # Structured fields for frontend tooltip
        "engine": manager_model,
        "max_loops": manifest.get("max_loops"),
    })
    edges.append({"from": "task_root", "to": "mgr_1", "color": "#E040FB", "width": 2})

    # Load iteration files
    iters_dir = run_dir / "iterations"
    iter_records: list[dict[str, Any]] = []
    if iters_dir.is_dir():
        try:
            files = sorted(
                (f for f in iters_dir.iterdir() if f.suffix == ".json"),
                key=lambda f: int("".join(c for c in f.stem if c.isdigit()) or "0"),
            )
            for f in files:
                d = _read_json(f)
                if d:
                    iter_records.append(d)
        except OSError:
            pass

    # Load worker files — map iteration -> list of workers.
    # ADR-0108: sub-manager directories (workers/<wid>_iter{N}/) are detected by the
    # presence of manifest.json inside; their sub-workers are loaded from sibling files.
    workers_dir = run_dir / "workers"
    iter_workers: dict[int, list[dict[str, Any]]] = {}
    if workers_dir.is_dir():
        try:
            for wf in workers_dir.iterdir():
                if wf.is_dir() and (wf / "manifest.json").exists():
                    # Sub-manager directory — load manifest + all sub-worker files.
                    sm = _read_json(wf / "manifest.json")
                    if sm:
                        sub_workers: list[dict[str, Any]] = []
                        for sw_file in sorted(wf.iterdir()):
                            if sw_file.is_file() and sw_file.suffix == ".json" and sw_file.name != "manifest.json":
                                sw = _read_json(sw_file)
                                if sw:
                                    sub_workers.append(sw)
                        sm["_sub_workers"] = sub_workers
                        it = int(sm.get("iteration", 0))
                        iter_workers.setdefault(it, []).append(sm)
                elif wf.suffix == ".json":
                    wd = _read_json(wf)
                    if wd:
                        it = int(wd.get("iteration", 0))
                        iter_workers.setdefault(it, []).append(wd)
        except OSError:
            pass

    # Level 2 — Iteration boxes + Level 3 Worker dots
    # Iterations are chained sequentially (mgr → iter_0 → iter_1 → … → completion)
    # so that edges never cross through intermediate iteration nodes.
    last_iter_id: str | None = None
    for d in iter_records:
        it_num = int(d.get("iteration", 0))
        decision = str(d.get("decision") or "UNKNOWN")
        conf = d.get("confidence")
        budget_pct = d.get("budget_pct")
        workers_count = d.get("workers_count", len(iter_workers.get(it_num, [])))
        iter_id = f"iter_{it_num:03d}"

        iter_label = f"Iter {it_num}\n{decision}"
        if conf is not None:
            iter_label += f"\nconf:{float(conf):.2f}"

        conf_str = f"{float(conf):.2f}" if conf is not None else "?"
        budget_str = f"{float(budget_pct):.1f}%" if budget_pct is not None else "?"

        nodes.append({
            "id": iter_id,
            "label": iter_label,
            "shape": "box",
            "color": "#FFD600",
            "size": 24,
            "level": 2,
            "group": "decision",
            "font": _font,
            # Structured fields for frontend tooltip
            "iter_num": it_num,
            "decision": decision,
            "confidence": round(float(conf), 3) if conf is not None else None,
            "budget_pct": round(float(budget_pct), 1) if budget_pct is not None else None,
            "workers_count": workers_count,
        })
        # Sequential chain: manager → first iteration, then each iteration → next.
        # This avoids edges passing through intermediate iteration nodes.
        chain_src = last_iter_id if last_iter_id else "mgr_1"
        edges.append({
            "from": chain_src, "to": iter_id,
            "color": "#555555", "width": 1.5,
        })
        last_iter_id = iter_id

        # Level 3 — Worker dots for this iteration (chained left→right).
        # Workers are chained sequentially (iter → w0 → w1 → … → wN) so that
        # edges connect only adjacent nodes and never pass through intermediate
        # worker nodes.
        workers_for_iter = sorted(
            iter_workers.get(it_num, []),
            key=lambda w: str(w.get("worker_id", "")),
        )
        prev_worker_id: str | None = None
        for wd in workers_for_iter:
            w_id = str(wd.get("worker_id", "worker"))
            w_status = str(wd.get("status", "?"))
            w_conf = float(wd.get("confidence", 0.0))
            w_depth = int(wd.get("depth", 0))
            w_type = str(wd.get("type", "worker"))

            # Colour: success/partial+conf>0 → heatmap, failed → red, else neutral.
            if w_status in ("success", "ok"):
                color = _acs_confidence_color(w_conf)
            elif w_status == "partial" and w_conf > 0.0:
                color = _acs_confidence_color(w_conf)
            elif w_status in ("failed", "error"):
                color = "#FF1744"
            else:
                color = "#8b949e"

            if w_type == "sub_manager":
                # ADR-0108: sub-manager node in the worker chain row.
                node_id = f"w_{w_id}_iter{it_num}"
                sub_workers_count = len(wd.get("_sub_workers") or [])
                nodes.append({
                    "id": node_id,
                    "label": f"{w_id}\n[Sub-Mgr]\nconf:{w_conf:.2f}",
                    "shape": "dot",
                    "color": color,
                    "size": 24,
                    "level": 3,
                    "group": "sub_manager",
                    "font": _font,
                    "worker_name": w_id,
                    "iteration": it_num,
                    "status": w_status,
                    "confidence": round(w_conf, 3),
                    "depth": w_depth,
                    "sub_workers_spawned": sub_workers_count,
                })
                edge_src = prev_worker_id if prev_worker_id else iter_id
                edges.append({"from": edge_src, "to": node_id, "color": "#E040FB", "width": 1.5})
                prev_worker_id = node_id

                # Sub-workers are chained horizontally from the sub-manager.
                # They use "sw_" prefix to allow the frontend to distinguish them.
                prev_sw_id: str | None = None
                for sw in sorted(wd.get("_sub_workers") or [], key=lambda x: str(x.get("worker_id", ""))):
                    sw_id = str(sw.get("worker_id", "sw"))
                    sw_status = str(sw.get("status", "?"))
                    sw_conf = float(sw.get("confidence", 0.0))
                    sw_node_id = f"sw_{sw_id}_iter{it_num}"
                    if sw_status in ("success", "ok"):
                        sw_color = _acs_confidence_color(sw_conf)
                    elif sw_status == "partial" and sw_conf > 0.0:
                        sw_color = _acs_confidence_color(sw_conf)
                    elif sw_status in ("failed", "error"):
                        sw_color = "#FF1744"
                    else:
                        sw_color = "#8b949e"
                    nodes.append({
                        "id": sw_node_id,
                        "label": f"{sw_id}\nconf:{sw_conf:.2f}",
                        "shape": "dot",
                        "color": sw_color,
                        "size": 16,
                        "level": 3,
                        "group": "sub_worker",
                        "font": _font,
                        "worker_name": sw_id,
                        "iteration": it_num,
                        "status": sw_status,
                        "confidence": round(sw_conf, 3),
                        "depth": int(sw.get("depth", 1)),
                        "parent_worker_id": w_id,
                        "parent_node_id": node_id,
                    })
                    sw_edge_src = prev_sw_id if prev_sw_id else node_id
                    sw_edge_color = "#E040FB" if prev_sw_id is None else "#9C27B0"
                    edges.append({"from": sw_edge_src, "to": sw_node_id, "color": sw_edge_color, "width": 1.2})
                    prev_sw_id = sw_node_id
            else:
                node_id = f"w_{w_id}_iter{it_num}"
                nodes.append({
                    "id": node_id,
                    "label": f"{w_id}\nconf:{w_conf:.2f}",
                    "shape": "dot",
                    "color": color,
                    "size": 20,
                    "level": 3,
                    "group": "worker",
                    "font": _font,
                    # Structured fields for frontend tooltip
                    "worker_name": w_id,
                    "iteration": it_num,
                    "status": w_status,
                    "confidence": round(w_conf, 3),
                    "depth": w_depth,
                })
                # Chain: iteration → first worker, then each worker → next worker.
                edge_src = prev_worker_id if prev_worker_id else iter_id
                edges.append({
                    "from": edge_src, "to": node_id,
                    "color": "#00E676",
                    "width": 1.5,
                })
                prev_worker_id = node_id

    # Completion node
    is_success = status == "success"
    comp_color = "#00E676" if is_success else "#FF1744"
    comp_label = f"Result\n{status}\niters:{total_iters}"
    nodes.append({
        "id": "completion",
        "label": comp_label,
        "shape": "box",
        "color": comp_color,
        "size": 30,
        "level": 4,
        "group": "completion",
        "font": _font,
        # Structured fields for frontend tooltip
        "run_status": status,
        "total_iters": total_iters,
        "workers_spawned": workers_spawned,
        "quality_score": round(quality_score, 3) if quality_score is not None else None,
        "duration_s": round(duration_s, 1),
    })
    # Chain from last iteration → completion (stays in left column, no crossing).
    # Fall back to task_root only when no iterations were recorded.
    edges.append({
        "from": last_iter_id if last_iter_id else "task_root",
        "to": "completion",
        "color": comp_color, "width": 2,
        "dashes": not is_success,
    })
    # Add loss = 1 - confidence to every node that carries a confidence value.
    # Accumulate worker losses (for min/max range) and decision losses (for the curve)
    # in a single pass to avoid scanning the node list twice.
    _worker_losses: list[float] = []
    _loss_curve_unsorted: list[dict] = []
    for _n in nodes:
        _conf = _n.get("confidence")
        if _conf is not None:
            _n["loss"] = round(1.0 - float(_conf), 4)
            _grp = _n.get("group")
            if _grp in ("worker", "sub_worker", "sub_manager"):
                _worker_losses.append(_n["loss"])
            elif _grp == "decision":
                _loss_curve_unsorted.append({
                    "iter": _n["iter_num"], "confidence": _conf, "loss": _n["loss"],
                })
    _loss_curve = sorted(_loss_curve_unsorted, key=lambda x: x["iter"])

    return {
        "mode": "acs",
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "n_iters": total_iters,
            "n_workers": workers_spawned,
            "state": status,
            "wall_time_s": duration_s,
            "quality_score": quality_score,
            "loss_min": round(min(_worker_losses), 4) if _worker_losses else None,
            "loss_max": round(max(_worker_losses), 4) if _worker_losses else None,
            "loss_curve": _loss_curve,
        },
    }

@router.get("/compute/acs/{run_id}/graph")
def compute_acs_run_graph(
    run_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return vis.js-compatible graph data for an ACS workflow run (ADR-0107 M1b)."""
    if not run_id or "/" in run_id or run_id.startswith("..") or run_id == ".":
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid run_id")

    # Global index stores only manifest.json; run_dir field points to actual data.
    # Try the project-scoped tenant home first, then fall back to the runtime ~/.corvin
    # home used by the ACS adapter — both are valid storage locations.
    global_manifest_path = _acs_runs_dir(rec.tenant_id) / run_id / "manifest.json"
    if not global_manifest_path.exists():
        _runtime_acs = (
            Path.home() / ".corvin" / "tenants" / rec.tenant_id
            / "global" / "acs" / "runs" / run_id / "manifest.json"
        )
        if _runtime_acs.exists():
            global_manifest_path = _runtime_acs
    if not global_manifest_path.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"ACS run {run_id!r} not found")

    manifest = _read_json(global_manifest_path) or {}
    run_dir_str = manifest.get("run_dir")
    if not run_dir_str:
        raise HTTPException(http_status.HTTP_503_SERVICE_UNAVAILABLE,
                            "ACS run_dir not recorded in manifest")

    run_dir = Path(run_dir_str).resolve()
    # Path-containment guard: run_dir must stay within a trusted root.
    # Three valid roots (all are canonicalized via .resolve()):
    #   1. The tenant's project-scoped corvin_home tree (covers session-scoped ACS runs
    #      stored alongside the project, e.g. <project>/.corvin/tenants/<tid>/sessions/…)
    #   2. The tenant's user-home runtime tree (~/.corvin/tenants/<tid>/…)
    #   3. Narrow fallback: just the global ACS runs dir (subset of root 1 or 2)
    # This prevents a tampered manifest.json from pointing outside operator storage.
    _project_corvin = (_forge_paths.corvin_home() / "tenants" / rec.tenant_id).resolve()
    _home_corvin = (Path.home() / ".corvin" / "tenants" / rec.tenant_id).resolve()
    _acs_index_root = _acs_runs_dir(rec.tenant_id).resolve()
    _trusted = (str(_project_corvin), str(_home_corvin), str(_acs_index_root))
    if not any(str(run_dir).startswith(root) for root in _trusted):
        raise HTTPException(http_status.HTTP_403_FORBIDDEN,
                            "ACS run_dir points outside the allowed tenant tree")
    if not run_dir.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND,
                            f"ACS run_dir not found on disk")

    try:
        payload = _build_acs_graph(run_dir, manifest)
    except Exception as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                            f"ACS graph build failed: {exc}")

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="compute.acs_graph_viewed",
        target_kind="acs_run",
        target_id=run_id,
    )
    return payload
