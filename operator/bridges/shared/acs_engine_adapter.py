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
import threading
import time
import uuid
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
            # NOT quota_exhausted: a removed/shadowed license module must stay a
            # hard fail-closed deny — the quota fallback below must NOT fire, or
            # deleting the license package would buy unmetered fallback compute.
            "reason": "enforcement_unavailable",
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
            "reason": "quota_exhausted",
            "error": f"compute_units_per_day exceeded: {exc}",
            "engine": "acs",
            "duration_s": 0.0,
        }
    except Exception:  # noqa: BLE001 — operational error already swallowed (fail-open)
        pass
    return None


# Generous-but-finite daily cap on quota-fallback runs per tenant. The fallback
# runs on the user's OWN engine credentials (un-metered by design,
# LIC-DELEGATE-MCP-COMPUTE-01), so this is not a paid-compute gate — it bounds
# the SCRIPTABLE surface adversarial review F6/Sec-F1 flagged: without it, after
# the single daily ACS unit is spent, every subsequent POST /compute/acs/runs
# (or scheduler tick) degrades to an un-metered 24 h-budgeted turn with no limit
# on repetition or concurrency. 50/day is far above any legitimate degraded-mode
# usage and still turns "unlimited" into "bounded". Fail-OPEN: a broken counter
# lets the fallback proceed (degradation must not become a hard failure on an
# I/O hiccup) — the cap is a backstop, not a security boundary.
_FALLBACK_MAX_PER_DAY = 50


# D3 (adversarial review): the counter below is a read-modify-write; unlocked,
# N parallel submissions each read the same count and overshoot the daily cap
# arbitrarily. Serialize with the LIC-1 pattern from
# operator/license/compute_quota.py: an in-process threading.Lock around the
# ENTIRE read-modify-write plus an advisory file lock (POSIX fcntl.flock;
# msvcrt.locking range lock on Windows) for cross-process safety.
_FALLBACK_COUNT_LOCK = threading.Lock()


def _fallback_quota_ok(tenant_id: str) -> "tuple[bool, int]":
    """Increment + check the per-UTC-day fallback counter. Returns
    (allowed, count_after). Fail-open on operational errors (any error returns
    (True, -1) — the cap is a backstop, not a security boundary), but the
    read-modify-write itself is atomic (LIC-1 lock pattern, review D3)."""
    try:
        import datetime as _dt  # noqa: PLC0415
        day = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
        path = _acs_runs_dir(tenant_id).parent / "fallback_count.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with _FALLBACK_COUNT_LOCK:
            _lf = None
            _locked = False
            try:
                _lf = open(path.with_suffix(".lock"), "a+b")  # noqa: SIM115
                if os.name == "nt":
                    try:
                        import msvcrt  # type: ignore  # noqa: PLC0415
                        msvcrt.locking(_lf.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
                        _locked = True
                    except OSError:
                        # Range lock unavailable — the in-process lock above
                        # still serializes this process (same as LIC-1).
                        log.warning("acs quota fallback: msvcrt lock unavailable "
                                    "— relying on in-process lock only")
                else:
                    import fcntl  # noqa: PLC0415
                    fcntl.flock(_lf, fcntl.LOCK_EX)
                    _locked = True
                cur = {}
                if path.exists():
                    try:
                        cur = json.loads(path.read_text(encoding="utf-8")) or {}
                    except Exception:  # noqa: BLE001 — corrupt counter → start fresh
                        cur = {}
                count = int(cur.get("count", 0)) if cur.get("utc_day") == day else 0
                if count >= _FALLBACK_MAX_PER_DAY:
                    return False, count
                _write_json_atomic(path, {"utc_day": day, "count": count + 1})
                return True, count + 1
            finally:
                if _lf is not None:
                    try:
                        if _locked:
                            if os.name == "nt":
                                import msvcrt  # type: ignore  # noqa: PLC0415
                                _lf.seek(0)
                                msvcrt.locking(_lf.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
                            else:
                                import fcntl  # noqa: PLC0415
                                fcntl.flock(_lf, fcntl.LOCK_UN)
                    except OSError:
                        pass
                    _lf.close()
    except Exception:  # noqa: BLE001 — never let the counter break degradation
        return True, -1


def run_acs_quota_fallback(
    spec: "dict | str | Path",
    inputs: "dict | None" = None,
    *,
    tenant_id: str = "_default",
    run_id: "str | None" = None,
    quota_error: str = "",
    budget_override: "dict | None" = None,
) -> dict[str, Any]:
    """Daily ACS quota exhausted → run the workflow GOAL as ONE direct Claude
    Code engine run instead of failing (maintainer decision 2026-07-20).

    Extends the ADR-0150 web-chat graceful degradation to every
    run_acs_workflow caller (workflow CLI, scheduler, orchestration MCP,
    console ACS route): "no ACS turn available" must degrade to the normal
    Claude Code delegation — Claude Code does its own built-in Task-tool
    delegation inside the single turn — never to a hard failure.

    Gate parity (load-bearing): the ACS path enforces L44 fail-closed inside
    ACSRuntime.run, which this fallback bypasses entirely — so the SAME
    check_l44 gate runs here, fail-closed, BEFORE any spawn. L34/tenant-policy/
    engines_allowed are enforced inside run_delegate itself. run_delegate is
    deliberately un-metered (LIC-DELEGATE-MCP-COMPUTE-01), so this does not
    re-open the quota: the fan-out stays blocked, only a single turn runs.
    """
    t0 = time.time()
    # Sanitize run_id BEFORE any path join: rid flows into runs_dir / rid and
    # then run_delegate(working_dir=..., allow_write=True). Every current caller
    # passes run_id=None or an internally-generated id, so this is defense-in-
    # depth — but a future caller threading a user-controlled run_id would
    # otherwise get an arbitrary-write escape from the tenant runs dir. Mirror
    # the sibling read routes' traversal guard via the canonical component
    # sanitizer (also fixes Windows-illegal chars). Adversarial review F3.
    # uuid4 suffix (review D8): a bare `acs-fb-<int(t0)>` has SECOND
    # resolution — two fallbacks starting in the same second shared one
    # run_dir and silently overwrote each other's manifest/result.json.
    _default_rid = f"acs-fb-{int(t0)}-{uuid.uuid4().hex[:8]}"
    rid = run_id or _default_rid
    try:
        from forge.paths import fs_safe_component as _fs_safe  # type: ignore
        rid = _fs_safe(rid) or _default_rid
    except Exception:  # noqa: BLE001 — never let sanitizer import break the fallback
        # Last-resort inline guard: reject traversal/separators outright.
        if (not rid) or ("/" in rid) or ("\\" in rid) or rid.startswith(".") or (".." in rid):
            rid = _default_rid

    def _failed(err: str) -> dict[str, Any]:
        return {
            "run_id": rid, "status": "failed", "error": err,
            "engine": "acs", "duration_s": round(time.time() - t0, 3),
            "quota_fallback": True,
        }

    # Resolve the spec dict (path callers: CLI/route pass a .awp.yaml path).
    if isinstance(spec, (str, Path)):
        try:
            import yaml  # noqa: PLC0415
            spec_dict: dict = yaml.safe_load(Path(spec).read_text("utf-8")) or {}
        except Exception as e:  # noqa: BLE001
            return _failed(f"{quota_error}; quota fallback unavailable "
                           f"(spec unreadable: {type(e).__name__})")
    else:
        spec_dict = spec or {}

    try:
        from acs_runtime import _workflow_goal_text  # type: ignore
        goal = _workflow_goal_text(spec_dict, inputs)
    except Exception as e:  # noqa: BLE001
        return _failed(f"{quota_error}; quota fallback unavailable "
                       f"(goal extraction failed: {type(e).__name__})")
    if not goal.strip():
        # No free-text instruction to hand to a single engine turn — nothing
        # a fallback run could act on. Surface the original quota stop.
        return _failed(f"{quota_error}; quota fallback skipped (workflow "
                       "carries no goal/description text)")

    # L44 acceptable-use — MANDATORY, fail-CLOSED (same contract as the
    # ACSRuntime.run chokepoint this path bypasses).
    try:
        from spawn_gates import check_l44 as _sg_l44  # type: ignore
    except Exception as _l44_exc:  # noqa: BLE001 — mandatory layer absent → DENY
        log.error("acs quota fallback: L44 spawn_gates import failed (%s) — "
                  "fail-closed deny", type(_l44_exc).__name__)
        return _failed("[house-rules] Acceptable-use gate unavailable — "
                       "fallback blocked (fail-closed). Contact the operator.")
    _l44_refusal = _sg_l44(
        goal, tenant_id, persona="orchestrator",
        channel="acs-quota-fallback", chat_key=rid, engine_id="claude_code",
    )
    if _l44_refusal is not None:
        # check_l44 already emitted the house_rules.* L16 event (audit-first).
        return _failed(_l44_refusal)

    # Bounded scriptable surface (review F6/Sec-F1) — checked AFTER L44 so a
    # gate-blocked task does not consume the daily fallback budget.
    _fb_ok, _fb_count = _fallback_quota_ok(tenant_id)
    if not _fb_ok:
        return _failed(
            f"{quota_error}; quota fallback limit reached "
            f"({_FALLBACK_MAX_PER_DAY}/day) — try again tomorrow or upgrade for "
            "unmetered ACS runs")

    # Single-turn wall-clock budget: honour the spec's own wall-time if set,
    # and an explicit caller budget_override (review F8: the console route drops
    # body.budget_override otherwise, so a caller who bounded the run to e.g.
    # 300 s got a turn allowed to run 24 h). Take the MIN of whatever bounds
    # were requested — a narrower request always wins.
    _dl = (spec_dict.get("orchestration", {}) or {}).get("delegation_loop", {}) \
        if isinstance(spec_dict.get("orchestration"), dict) else {}
    _b = _dl.get("budget", {}) if isinstance(_dl, dict) else {}
    _wall_bounds = [86400]
    for _src in (_b, budget_override or {}):
        try:
            _w = int((_src or {}).get("max_wall_time") or 0)
            if _w > 0:
                _wall_bounds.append(_w)
        except (TypeError, ValueError):
            pass
    budget_s = min(_wall_bounds)

    # run_delegate: installed package first, repo-relative second.
    try:
        from corvin_delegate.delegation import run_delegate  # type: ignore
    except ImportError:
        _dele = Path(__file__).resolve().parents[3] / "core" / "delegate"
        if str(_dele) not in sys.path:
            sys.path.insert(0, str(_dele))
        try:
            from corvin_delegate.delegation import run_delegate  # type: ignore
        except ImportError as e:
            return _failed(f"{quota_error}; quota fallback unavailable "
                           f"(corvin_delegate not importable: {e})")

    # Persist artifacts under the normal ACS runs index so the console
    # list/get endpoints see the fallback run like any other.
    runs_dir = _acs_runs_dir(tenant_id)
    run_dir = runs_dir / rid
    out_dir = run_dir / "output"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return _failed(f"{quota_error}; quota fallback unavailable "
                       f"(run dir not creatable: {type(e).__name__})")

    notice = (
        "Daily ACS quota exhausted — task ran as a single direct Claude Code "
        "turn (its built-in delegation) instead of the ACS worker fan-out."
    )
    prompt = (
        "The ACS multi-worker delegation is unavailable right now, so you are "
        "handling this workflow goal as a single agent turn. Use your own "
        "built-in subagent delegation where it helps.\n\nGoal:\n" + goal
    )
    log.info("acs quota fallback: running goal as direct claude_code turn "
             "(run_id=%s, budget_s=%d)", rid, budget_s)
    try:
        # budget_ceiling_s lets THIS path exceed the 600 s interactive cap that
        # bounds every other run_delegate caller (review F7): a whole-workflow
        # fallback goal legitimately needs longer, the MCP delegate_* tools do
        # not. run_delegate clamps budget_s to [BUDGET_MIN_S, budget_ceiling_s].
        from corvin_delegate.delegation import BUDGET_FALLBACK_MAX_S  # type: ignore
        res = run_delegate(
            engine="claude_code",
            prompt=prompt,
            budget_s=budget_s,
            budget_ceiling_s=BUDGET_FALLBACK_MAX_S,
            working_dir=out_dir,
            allow_write=True,
            persona="acs-quota-fallback",
        )
    except Exception as e:  # noqa: BLE001 — caller-side validation errors
        return _failed(f"quota fallback failed: {type(e).__name__}: {e}")

    duration_s = round(time.time() - t0, 3)
    status = "success" if res.ok else "failed"
    workflow_id = str((spec_dict.get("workflow", {}) or {}).get("name") or "unknown")
    manifest = {
        "run_id": rid, "workflow_id": workflow_id, "status": status,
        "engine": "claude_code", "quota_fallback": True,
        "started_at": t0, "completed_at": t0 + duration_s,
        "duration_s": duration_s, "iterations": 1, "workers_spawned": 0,
        "budget_breach": "", "run_dir": str(run_dir),
    }
    _write_json_atomic(run_dir / "manifest.json", manifest)
    _write_json_atomic(run_dir / "result.json", {
        "run_id": rid, "workflow_id": workflow_id, "status": status,
        "summary": notice, "final_output": res.final_text,
        "error": res.error, "iterations": 1, "workers_spawned": 0,
        "budget_breach": "", "elapsed_s": duration_s,
    })
    return {
        "run_id": rid, "status": status, "summary": notice,
        "final_output": res.final_text, "error": res.error,
        "engine": "claude_code", "duration_s": duration_s,
        "workflow_id": workflow_id, "iterations": 1, "workers_spawned": 0,
        "budget_breach": "", "quota_fallback": True, "notice": notice,
    }


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
            # Day limit reached → degrade to a single direct Claude Code turn
            # (maintainer decision 2026-07-20; mirrors the ADR-0150 web-chat
            # fallback). Only for genuine quota exhaustion — a missing license
            # module stays a hard fail-closed deny (reason=enforcement_unavailable).
            if _cq_block.get("reason") == "quota_exhausted":
                return run_acs_quota_fallback(
                    spec, inputs, tenant_id=tenant_id, run_id=run_id,
                    quota_error=str(_cq_block.get("error") or ""),
                    # Review D1 (companion of F8): thread the caller's budget
                    # bound through the chokepoint too — an MCP/scheduler run
                    # bounded to e.g. max_wall_time=300 must not degrade into
                    # a BUDGET_FALLBACK-length turn.
                    budget_override=budget_override,
                )
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
