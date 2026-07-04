"""ACO Layer 5 — Actuating Self-Repair (ADR-0178, Tier LOCAL).

Promotes L5 from log-only (repair.py) to BOUNDED, REVERSIBLE, ALLOWLISTED repair
of the runtime ENVIRONMENT — never the installed code. Every action:

  * is registered (closed registry — an unknown fault escalates, never improvises);
  * writes ONLY within CORVIN_HOME (asserted by _assert_within_home; the installed
    package / site-packages / repo are structurally immutable here — program code is
    updated only via pip);
  * is REVERSIBLE (records an undo) and LOSS-GATED (if applying it did not clear the
    fault, the action is rolled back);
  * is AUDITED (L16 hash chain, best-effort) + logged to <home>/aco_repair.jsonl;
  * performs ZERO network egress (private/offline — GDPR Art. 5/25, EU AI Act).

Tiering: ``risk="safe"`` actions run by default; ``risk="risky"`` need
``CORVIN_ACO_L5_RISKY=1``. Global kill switch: ``CORVIN_ACO_L5_OFF=1`` (back to
log-only). This is Tier LOCAL only — it NEVER commits code or reaches ``main``
(that is L6, ADR-0178, gated by a signed maintainer capability).
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

RISK_SAFE = "safe"
RISK_RISKY = "risky"


# ── Context + result types ────────────────────────────────────────────────────

@dataclass
class RepairContext:
    """Everything an action needs. ``corvin_home`` is the ONLY writable root."""
    corvin_home: Path
    tenant_id: str = "_default"
    now: float = 0.0  # epoch; 0 → resolved at run time (kept injectable for tests)

    def time(self) -> float:
        return self.now or time.time()


@dataclass
class RepairOutcome:
    action_id: str
    status: str            # applied | reverted | failed | skipped | would_apply
    detail: str = ""
    fixed: int = 0         # how many fault instances were cleared


class RepairScopeError(Exception):
    """Raised when an action tries to write outside CORVIN_HOME — fail-closed."""


# ── Path-gate: the code-immutability guarantee ────────────────────────────────

def _assert_within_home(home: Path, target: Path) -> Path:
    """Refuse any path outside CORVIN_HOME. This is what makes Tier LOCAL unable to
    touch the installed package / repo / site-packages."""
    home_r = home.resolve()
    t = Path(target).resolve()
    if t != home_r and home_r not in t.parents:
        raise RepairScopeError(f"refused write outside CORVIN_HOME: {t}")
    return t


# ── Registry ──────────────────────────────────────────────────────────────────

class RepairAction:
    action_id: str = ""
    risk: str = RISK_SAFE
    blast_radius: str = "home"        # session | tenant | home (documentation)

    def precondition(self, ctx: RepairContext) -> list[Any]:
        """Return the list of fault instances present (empty = nothing to do)."""
        raise NotImplementedError

    def apply(self, ctx: RepairContext, faults: list[Any]) -> int:
        """Repair the faults. Return how many were addressed. Must write only
        within ctx.corvin_home (use _assert_within_home)."""
        raise NotImplementedError

    def undo(self, ctx: RepairContext) -> None:
        """Best-effort reversal of the last apply() (uses recorded before-state)."""
        return None


_REGISTRY: dict[str, RepairAction] = {}


def register_repair(cls: type[RepairAction]) -> type[RepairAction]:
    inst = cls()
    if not inst.action_id:
        raise ValueError(f"{cls.__name__} has no action_id")
    _REGISTRY[inst.action_id] = inst
    return cls


def registered_actions() -> dict[str, RepairAction]:
    return dict(_REGISTRY)


# ── Gating ────────────────────────────────────────────────────────────────────

def _tenant_config() -> dict[str, Any]:
    """Best-effort read of tenant.corvin.yaml — the SAME file the console
    healing-config toggles write (routes/healing_config.py). Returns {} on any
    error so config-reads can never crash the repair loop."""
    try:
        import yaml as _yaml  # noqa: PLC0415

        from .. import _bootstrap as _bs  # noqa: PLC0415
        cfg_path = _bs.forge_paths.tenant_global_dir() / "tenant.corvin.yaml"
        if cfg_path.exists():
            data = _yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:  # noqa: BLE001
        pass
    return {}


def _kill_switch() -> bool:
    # Env var takes precedence (operator-level kill switch).
    if os.environ.get("CORVIN_ACO_L5_OFF", "").strip().lower() in ("1", "true", "yes"):
        return True
    # Then fall back to the tenant config toggle (aco.l5_enabled: false → kill).
    data = _tenant_config()
    spec = data.get("spec", data)  # manifest has spec: wrapper
    aco = spec.get("aco")
    if isinstance(aco, dict):
        return not aco.get("l5_enabled", True)
    return False


def _risky_enabled() -> bool:
    # Env var takes precedence (operator-level opt-in).
    if os.environ.get("CORVIN_ACO_L5_RISKY", "").strip().lower() in ("1", "true", "yes"):
        return True
    # Then fall back to the tenant config toggle (aco.l5_risky: true → enabled).
    data = _tenant_config()
    spec = data.get("spec", data)  # manifest has spec: wrapper
    aco = spec.get("aco")
    if isinstance(aco, dict):
        return bool(aco.get("l5_risky", False))
    return False


# ── Audit (best-effort, never raises) ─────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _audit(ctx: RepairContext, event: str, **fields: Any) -> None:
    rec = {"ts": _now_iso(), "event": event, **fields}
    # 1) durable, append-only repair journal under the home (always local).
    try:
        jp = _assert_within_home(ctx.corvin_home, ctx.corvin_home / "aco_repair.jsonl")
        jp.parent.mkdir(parents=True, exist_ok=True)
        with jp.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — observability, never enforcement
        pass
    # 2) L16 hash-chained audit (metadata only), best-effort.
    try:
        from forge import security_events as _sec  # type: ignore  # noqa: PLC0415
        chain = ctx.corvin_home / "tenants" / ctx.tenant_id / "global" / "audit.jsonl"
        _sec.write_event(chain, event, details={k: v for k, v in fields.items()
                                                 if k in ("action_id", "risk", "status",
                                                          "fixed", "reason")})
    except Exception:  # noqa: BLE001
        pass


# ── Healing-trace production (ADR-0180 B5) ────────────────────────────────────

def _instance_token(home: Path) -> str:
    """Read the pre-provisioned HMAC instance token; '' if missing (fail-soft).

    Mirrors htrace_uploader._load_instance_token — the token is provisioned at
    consent time and lives at ``<home>/aco/telemetry/htrace-token.txt``.
    """
    try:
        p = home / "aco" / "telemetry" / "htrace-token.txt"
        return p.read_text(encoding="utf-8").strip()[:64]
    except OSError:
        return ""


# Map a RepairOutcome.status to the (heal_outcome, event_sequence) pair recorded
# in the ADR-0180 healing trace. Only allowlisted heal_outcome values
# ({success, failure, skipped}) and EVENT_SEQ_ALLOWLIST event names are used, so
# every emitted record passes _assert_safe_htrace (client) and validate_record
# (server) unchanged. "reverted" = no progress → rolled back = net no-change.
_HTRACE_STATUS_MAP: dict[str, tuple[str, list[str]]] = {
    "applied":  ("success", ["heal.triggered", "heal.action", "heal.success"]),
    "reverted": ("skipped", ["heal.triggered", "heal.action", "heal.skipped"]),
    "failed":   ("failure", ["heal.triggered", "heal.action", "heal.failure"]),
}


def _emit_htrace(ctx: RepairContext, action: "RepairAction", status: str) -> None:
    """Emit one scrubbed ADR-0180 healing trace for a completed repair attempt.

    Double-gated + fail-soft: ``write_trace`` only persists when consent is
    active (``healing_traces_enabled``); otherwise it is a no-op. Any error here
    is swallowed — telemetry must never break or slow a repair.
    """
    try:
        from .htrace import HealingTrace, write_trace  # noqa: PLC0415
        from .htrace_consent import (  # noqa: PLC0415
            healing_traces_enabled,
            load_consent_act_id,
        )

        outcome, events = _HTRACE_STATUS_MAP.get(
            status, ("skipped", ["heal.triggered", "heal.action", "heal.skipped"])
        )
        home = ctx.corvin_home
        trace = HealingTrace(
            event_sequence=events,
            heal_action=action.action_id,
            heal_outcome=outcome,
            tenant_shape="multi" if ctx.tenant_id not in ("_default", "") else "single",
            consent_act_id=load_consent_act_id(home),
            instance_token=_instance_token(home),
        )
        write_trace(trace, home, consent_active=healing_traces_enabled(home))
    except Exception:  # noqa: BLE001 — telemetry is best-effort, never load-bearing
        pass


# ── Executor ──────────────────────────────────────────────────────────────────

def run_local_repairs(ctx: RepairContext, *, dry_run: bool = False) -> list[RepairOutcome]:
    """Run every applicable repair action once. Loss-gated + reversible + audited.

    Returns one RepairOutcome per action that had work (or would, in dry_run).
    NEVER raises — a failing action is rolled back and recorded, the rest proceed.
    """
    if _kill_switch():
        return []
    risky_ok = _risky_enabled()
    out: list[RepairOutcome] = []

    for action in _REGISTRY.values():
        if action.risk == RISK_RISKY and not risky_ok:
            continue
        try:
            faults = action.precondition(ctx)
        except Exception as exc:  # noqa: BLE001 — detection must not crash the loop
            logger.debug("precondition %s failed: %s", action.action_id, exc)
            continue
        if not faults:
            continue
        if dry_run:
            out.append(RepairOutcome(action.action_id, "would_apply",
                                     f"{len(faults)} fault(s)", fixed=0))
            continue

        _audit(ctx, "repair.attempt", action_id=action.action_id, risk=action.risk,
               count=len(faults))
        try:
            n_before = len(faults)
            fixed = action.apply(ctx, faults)
            # Loss gate (PROGRESS-based, not all-or-nothing): re-detect and revert
            # ONLY if the action made NO progress (fault count did not drop). A
            # partial fix — or an unrelated new fault appearing during the cycle —
            # must NOT roll back the repairs that succeeded (security/correctness
            # review 2026-06-29: all-or-nothing reverted good work → net-zero loop).
            remaining = action.precondition(ctx)
            if len(remaining) >= n_before:
                action.undo(ctx)
                _audit(ctx, "repair.reverted", action_id=action.action_id,
                       reason="no_progress", status="reverted")
                out.append(RepairOutcome(action.action_id, "reverted",
                                         "no progress → rolled back"))
                _emit_htrace(ctx, action, "reverted")
            else:
                _audit(ctx, "repair.applied", action_id=action.action_id,
                       status="applied", fixed=fixed)
                out.append(RepairOutcome(action.action_id, "applied",
                                         f"cleared {n_before - len(remaining)} fault(s)",
                                         fixed=fixed))
                _emit_htrace(ctx, action, "applied")
        except RepairScopeError as exc:
            _audit(ctx, "repair.blocked", action_id=action.action_id, reason=str(exc),
                   status="failed")
            out.append(RepairOutcome(action.action_id, "failed", f"scope: {exc}"))
            _emit_htrace(ctx, action, "failed")
        except Exception as exc:  # noqa: BLE001
            try:
                action.undo(ctx)
            except Exception:  # noqa: BLE001
                pass
            _audit(ctx, "repair.failed", action_id=action.action_id, reason=str(exc)[:200],
                   status="failed")
            out.append(RepairOutcome(action.action_id, "failed", str(exc)[:200]))
            _emit_htrace(ctx, action, "failed")
    return out


def run_default(*, tenant_id: str = "_default", dry_run: bool = False) -> list[RepairOutcome]:
    """Convenience entry point: resolve CORVIN_HOME and run."""
    from forge import paths as _paths  # type: ignore  # noqa: PLC0415
    return run_local_repairs(RepairContext(corvin_home=_paths.corvin_home(),
                                           tenant_id=tenant_id), dry_run=dry_run)


# ── SAFE seed actions (M1) ────────────────────────────────────────────────────

@register_repair
class SessionWorkdirMissing(RepairAction):
    """A session meta JSON exists but its on-disk workdir is gone → recreate it.

    Reads the workdir straight from the persisted meta (no path-rebuild → no drift
    with chat_runtime / fs_safe_component). Directly addresses the 'chat exists but
    its folder vanished' class."""
    action_id = "session_workdir_missing"
    risk = RISK_SAFE
    blast_radius = "session"

    def _meta_files(self, ctx: RepairContext) -> list[Path]:
        root = ctx.corvin_home / "tenants"
        if not root.is_dir():
            return []
        return list(root.glob("*/global/web_chat/sessions/*.json"))

    def precondition(self, ctx: RepairContext) -> list[Any]:
        faults = []
        for mf in self._meta_files(ctx):
            try:
                meta = json.loads(mf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            wd = meta.get("workdir")
            if not wd:
                continue
            wd_path = Path(wd)
            try:
                _assert_within_home(ctx.corvin_home, wd_path)
            except RepairScopeError:
                continue  # never recreate a workdir that escaped the home
            if not wd_path.is_dir():
                faults.append(wd_path)
        return faults

    def apply(self, ctx: RepairContext, faults: list[Any]) -> int:
        self._created: list[Path] = []
        for wd_path in faults:
            t = _assert_within_home(ctx.corvin_home, wd_path)
            t.mkdir(parents=True, exist_ok=True)
            self._created.append(t)
        return len(self._created)

    def undo(self, ctx: RepairContext) -> None:
        for t in reversed(getattr(self, "_created", [])):
            try:
                if t.is_dir() and not any(t.iterdir()):
                    t.rmdir()  # only remove if still empty (we just made it)
            except OSError:
                pass


@register_repair
class StaleLockSweep(RepairAction):
    """Remove orphaned ``*.lock`` files under the home older than the TTL — a crash
    or kill can leave a lock that wedges the next start."""
    action_id = "stale_lock"
    risk = RISK_SAFE
    blast_radius = "home"
    _TTL_S = 6 * 3600  # 6 h — beyond legitimate holds incl. long L24/L25 compute

    def precondition(self, ctx: RepairContext) -> list[Any]:
        faults = []
        now = ctx.time()
        for lk in ctx.corvin_home.rglob("*.lock"):
            try:
                # Skip symlinks: stat() would follow the link to its target
                # (possibly outside the home) and mis-age it. Only real files here.
                if lk.is_symlink() or not lk.is_file():
                    continue
                if now - lk.stat().st_mtime <= self._TTL_S:
                    continue
                # Age alone is not enough: if the lock names a LIVE owner pid, the
                # holder is still running (e.g. a long compute job) — never sweep it.
                pid = self._owner_pid(lk)
                if pid and _pid_alive(pid):
                    continue
                faults.append(lk)
            except OSError:
                continue
        return faults

    @staticmethod
    def _owner_pid(lk: Path) -> int:
        try:
            txt = lk.read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            return 0
        # accept a bare pid or a "pid: N" / JSON-ish "pid":N form
        import re as _re
        m = _re.search(r"\b(\d{1,7})\b", txt)
        return int(m.group(1)) if m else 0

    def apply(self, ctx: RepairContext, faults: list[Any]) -> int:
        self._removed: list[tuple[Path, bytes]] = []
        n = 0
        for lk in faults:
            try:
                t = _assert_within_home(ctx.corvin_home, lk)  # per-file: skip, don't abort
            except RepairScopeError:
                continue
            try:
                data = t.read_bytes()
            except OSError:
                data = b""
            try:
                t.unlink()
            except OSError:
                continue
            self._removed.append((t, data))
            n += 1
        return n

    def undo(self, ctx: RepairContext) -> None:
        for t, data in getattr(self, "_removed", []):
            try:
                if not t.exists():
                    t.write_bytes(data)
            except OSError:
                pass


@register_repair
class OrphanTmpSweep(RepairAction):
    """Remove leftover ``*.tmp`` partial-write files under the home older than the
    TTL (interrupted atomic temp+rename writes)."""
    action_id = "orphan_tmp"
    risk = RISK_SAFE
    blast_radius = "home"
    _TTL_S = 86400  # 24 h

    def precondition(self, ctx: RepairContext) -> list[Any]:
        faults = []
        now = ctx.time()
        for tmp in ctx.corvin_home.rglob("*.tmp"):
            try:
                if tmp.is_symlink() or not tmp.is_file():
                    continue
                if now - tmp.stat().st_mtime > self._TTL_S:
                    faults.append(tmp)
            except OSError:
                continue
        return faults

    def apply(self, ctx: RepairContext, faults: list[Any]) -> int:
        n = 0
        for tmp in faults:
            try:
                t = _assert_within_home(ctx.corvin_home, tmp)  # per-file: skip, don't abort
            except RepairScopeError:
                continue
            try:
                t.unlink()
                n += 1
            except OSError:
                pass
        return n

    # undo: an orphan tmp is by definition disposable; no restore needed.


@register_repair
class HermesHealthRepair(RepairAction):
    """ADR-0178 Tier LOCAL — Automated Hermes/Ollama health restoration.

    Detects when Ollama (the local engine fallback) is unavailable and attempts
    corrective actions: (1) start the stopped server, (2) re-pull the configured
    model if missing. Loss-gated: if neither action succeeds in restoring reachability,
    the entire repair is rolled back (Ollama.start left running but model_pulled stays False).

    This action is risky (network I/O, subprocess fork) and requires
    CORVIN_ACO_L5_RISKY=1 to run. Never raises — any error is logged and ignored."""
    action_id = "hermes_health"
    risk = RISK_RISKY
    blast_radius = "home"  # starts a process; affects the home's Ollama server

    def precondition(self, ctx: RepairContext) -> list[Any]:
        """Return a list of missing components: [] if healthy, ['not_reachable']
        if Ollama is down, ['model_missing'] if the model is absent."""
        try:
            import sys
            sys.path.insert(0, str(ctx.corvin_home.parent.parent.parent / "operator" / "bridges" / "shared"))
            from hermes_healing import (  # type: ignore  # noqa: PLC0415
                get_health_status,
            )
            status = get_health_status()
            faults = []
            if not status["reachable"]:
                faults.append("not_reachable")
            elif not status["has_model"]:
                faults.append("model_missing")
            return faults
        except (ImportError, Exception):  # noqa: BLE001
            return []  # module not available or error → nothing to repair

    def apply(self, ctx: RepairContext, faults: list[Any]) -> int:
        try:
            import sys
            sys.path.insert(0, str(ctx.corvin_home.parent.parent.parent / "operator" / "bridges" / "shared"))
            from hermes_healing import repair_hermes  # type: ignore  # noqa: PLC0415
            result = repair_hermes(timeout_server=30.0, timeout_pull=600.0)
            if result.get("reachable"):
                count = (1 if result.get("server_started") else 0) + (1 if result.get("model_pulled") else 0)
                return count if count > 0 else 1  # at least 1 to avoid rolling back
            return 0
        except Exception:  # noqa: BLE001
            return 0

    def undo(self, ctx: RepairContext) -> None:
        """No-op: we want Ollama to stay running once started. The system will
        benefit from having it available as a fallback engine."""


@register_repair
class SecretFileMode(RepairAction):
    """Tighten known secret files under the home to 0600 if looser (POSIX only;
    a no-op on Windows where POSIX bits are not enforced). Tightening-only, so it
    can never widen access."""
    action_id = "secret_file_mode"
    risk = RISK_SAFE
    blast_radius = "home"
    _NAMES = ("service.env", "license.key", "session.key", "identity_registry.json")

    def _candidates(self, ctx: RepairContext) -> list[Path]:
        if os.name == "nt":
            return []  # POSIX mode bits are not meaningful on Windows
        out = []
        for name in self._NAMES:
            out.extend(ctx.corvin_home.rglob(name))
        # Real files only — never chmod a symlink (would affect its target).
        return [p for p in out if p.is_file() and not p.is_symlink()]

    def precondition(self, ctx: RepairContext) -> list[Any]:
        faults = []
        for p in self._candidates(ctx):
            try:
                mode = p.stat().st_mode & 0o777
            except OSError:
                continue
            if mode & 0o077:  # any group/other bit set
                faults.append(p)
        return faults

    def apply(self, ctx: RepairContext, faults: list[Any]) -> int:
        self._prev: list[tuple[Path, int]] = []
        n = 0
        for p in faults:
            t = _assert_within_home(ctx.corvin_home, p)
            try:
                self._prev.append((t, t.stat().st_mode & 0o777))
                t.chmod(0o600)
                n += 1
            except OSError:
                pass
        return n

    def undo(self, ctx: RepairContext) -> None:
        for t, mode in getattr(self, "_prev", []):
            try:
                t.chmod(mode)
            except OSError:
                pass


# ── RISKY seed actions (M2, opt-in via CORVIN_ACO_L5_RISKY=1) ──────────────────

def _pid_alive(pid: int) -> bool:
    """Best-effort liveness. Unknown → treat as ALIVE (conservative: never reset
    a task we can't prove is dead)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # exists, owned by someone else
    except (OSError, AttributeError, OverflowError):
        return True          # can't tell (e.g. Windows quirk) → assume alive


@register_repair
class CorruptConfigReset(RepairAction):
    """A ``*.config.json`` file under the home that no longer parses → back it up to
    ``<name>.corrupt`` and write an empty-object default, so a corrupted (e.g.
    half-written) config stops wedging boot. Reversible from the backup. SCOPED to
    the ``.config.json`` naming convention ONLY — never audit/policy/secret/session
    files."""
    action_id = "corrupt_config_reset"
    risk = RISK_RISKY
    blast_radius = "tenant"
    _FORBIDDEN = ("audit.jsonl", "policy.json", "license.key", "identity_registry.json")

    def _candidates(self, ctx: RepairContext) -> list[Path]:
        return [p for p in ctx.corvin_home.rglob("*.config.json")
                if p.is_file() and p.name not in self._FORBIDDEN]

    def precondition(self, ctx: RepairContext) -> list[Any]:
        faults = []
        for p in self._candidates(ctx):
            try:
                json.loads(p.read_text(encoding="utf-8"))
            except (OSError,):
                continue
            except json.JSONDecodeError:
                faults.append(p)   # parses-as-file but not as JSON → corrupt
        return faults

    def apply(self, ctx: RepairContext, faults: list[Any]) -> int:
        self._backups: list[tuple[Path, Path]] = []
        n = 0
        for p in faults:
            t = _assert_within_home(ctx.corvin_home, p)
            backup = _assert_within_home(ctx.corvin_home, t.with_suffix(".json.corrupt"))
            try:
                t.replace(backup)            # move the corrupt file aside (reversible)
                t.write_text("{}\n", encoding="utf-8")
                self._backups.append((t, backup))
                n += 1
            except OSError:
                pass
        return n

    def undo(self, ctx: RepairContext) -> None:
        for t, backup in getattr(self, "_backups", []):
            try:
                if backup.exists():
                    backup.replace(t)        # restore the original corrupt file
            except OSError:
                pass


@register_repair
class StaleRunningTaskReset(RepairAction):
    """A task record (``tasks/*.json``) stuck in ``status="running"`` whose ``pid``
    is dead → mark it ``failed`` so the queue stops treating a crashed task as live
    (the stale-running-zombie class). Reversible (restores the prior status)."""
    action_id = "stale_running_task_reset"
    risk = RISK_RISKY
    blast_radius = "tenant"

    def _task_files(self, ctx: RepairContext) -> list[Path]:
        return list(ctx.corvin_home.rglob("tasks/*.json"))

    def precondition(self, ctx: RepairContext) -> list[Any]:
        faults = []
        for tf in self._task_files(ctx):
            try:
                rec = json.loads(tf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(rec, dict) or rec.get("status") != "running":
                continue
            pid = rec.get("pid")
            if isinstance(pid, int) and not _pid_alive(pid):
                faults.append(tf)
        return faults

    def apply(self, ctx: RepairContext, faults: list[Any]) -> int:
        self._prev: list[tuple[Path, str]] = []
        n = 0
        for tf in faults:
            t = _assert_within_home(ctx.corvin_home, tf)
            try:
                rec = json.loads(t.read_text(encoding="utf-8"))
                self._prev.append((t, rec.get("status", "running")))
                rec["status"] = "failed"
                rec["failed_reason"] = "stale_running_pid_dead (ACO L5)"
                t.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
                n += 1
            except (OSError, json.JSONDecodeError):
                pass
        return n

    def undo(self, ctx: RepairContext) -> None:
        for t, prev_status in getattr(self, "_prev", []):
            try:
                rec = json.loads(t.read_text(encoding="utf-8"))
                rec["status"] = prev_status
                rec.pop("failed_reason", None)
                t.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
            except (OSError, json.JSONDecodeError):
                pass


@register_repair
class VoiceTtsPinnedProviderReset(RepairAction):
    """Detect when ``tts_provider`` is pinned to a provider that is not usable
    (no API key for OpenAI, piper/edge-tts not installed) and reset it to None
    (auto-chain) in the user profile so voice keeps working.

    This is the safety net for the "voice went silent after profile save" class
    of bugs: even if say.py's own fallback-chain kicks in for playback, this
    repair removes the stale pin so the user doesn't have to open Settings to
    fix it manually.

    Uses profile.py's own save API directly — it handles atomic writes and
    caching internally, so no ``_assert_within_home`` is needed here.

    Risk: safe — only clears a field in the user's profile; does not touch any
    system file, secret, or audit chain entry. Reversible via undo()."""
    action_id = "voice_tts_pinned_provider_reset"
    risk = RISK_SAFE
    blast_radius = "home"

    def _load_profile_module(self, ctx: RepairContext):
        """Return the profile module, resolving both source-tree and wheel layouts."""
        import sys as _sys
        for candidate in (
            ctx.corvin_home.parent.parent.parent / "operator" / "bridges" / "shared",
            ctx.corvin_home.parent / "_vendor" / "operator" / "bridges" / "shared",
        ):
            if candidate.is_dir() and str(candidate) not in _sys.path:
                _sys.path.insert(0, str(candidate))
        try:
            import profile as _p  # type: ignore  # noqa: PLC0415
            return _p
        except ImportError:
            return None

    def _provider_usable(self, provider: str) -> bool:
        """Quick, offline check: is the given provider likely to work?"""
        if provider == "openai":
            return bool(os.environ.get("OPENAI_API_KEY", "").strip())
        if provider == "edge":
            try:
                import importlib
                return importlib.util.find_spec("edge_tts") is not None
            except Exception:  # noqa: BLE001
                return False
        if provider == "piper":
            import shutil
            return shutil.which("piper") is not None
        return True  # unknown provider — don't touch it

    def precondition(self, ctx: RepairContext) -> list[Any]:
        """Return [provider_name] if tts_provider is pinned to an unusable provider."""
        mod = self._load_profile_module(ctx)
        if mod is None:
            return []
        try:
            profile = mod.load()
            provider = profile.get("tts_provider")
            if not isinstance(provider, str) or not provider.strip() or provider.strip() == "auto":
                return []
            p = provider.strip()
            if not self._provider_usable(p):
                return [p]
        except Exception:  # noqa: BLE001
            pass
        return []

    def apply(self, ctx: RepairContext, faults: list[Any]) -> int:
        mod = self._load_profile_module(ctx)
        if mod is None:
            return 0
        try:
            profile = mod.load()
            self._prev_provider = profile.get("tts_provider")
            profile["tts_provider"] = None
            mod.save(profile)
            logger.info("VoiceTtsPinnedProviderReset: cleared tts_provider (was %r)", self._prev_provider)
            return 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("VoiceTtsPinnedProviderReset.apply failed: %s", exc)
            return 0

    def undo(self, ctx: RepairContext) -> None:
        mod = self._load_profile_module(ctx)
        if mod is None:
            return
        try:
            profile = mod.load()
            profile["tts_provider"] = getattr(self, "_prev_provider", None)
            mod.save(profile)
        except Exception:  # noqa: BLE001
            pass
