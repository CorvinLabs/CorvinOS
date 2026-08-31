"""ato_loss.py — Orchestration Loss Tracking (ADR-0164 M4).

Tracks three EMA loss signals per task_type:
  - convergence_rate          : did loop converge before K_MAX? (high = good)
  - goal_revision_rate        : was the goal text changed mid-execution? (low = good)
  - strategy_correction_rate  : did the user override the selected strategy? (low = good)

Storage: <corvin_home>/tenants/<tid>/global/ato/loss_stats.json (mode 0600)
Atomic writes via mkstemp+rename. Thread-safe. Advisory only — no auto-tuning.

MUST NOT import anthropic (CI AST lint enforces).
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

_EMA_ALPHA: float = 0.2       # same as ULO compliance_rate (ADR-0163)
_MIN_SAMPLES: int  = 5        # minimum samples before emitting advisory alerts

# Alert thresholds (advisory only — emitted as L16 WARNING, never auto-tuned)
_CONV_ALERT_THRESHOLD: float     = 0.60  # convergence below this → goal template weak
_GOAL_ALERT_THRESHOLD: float     = 0.30  # goal revision above this → template underspecified
_STRATEGY_ALERT_THRESHOLD: float = 0.20  # strategy correction above this → classifier drift

_write_lock: threading.Lock = threading.Lock()


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _ato_dir(tenant_id: str | None = None) -> Path:
    """Return per-tenant ATO directory (created lazily by _save).

    Uses paths.tenant_home() unconditionally so that:
    - tenant_id=None  → _default tenant path (not the shared global/ root)
    - invalid tenant_id → ValueError propagates (no silent cross-tenant bleed)
    Falls back to CORVIN_HOME/global/ato only when paths module is unavailable
    (standalone / test context without the bridge runtime).
    """
    try:
        from paths import tenant_home as _th  # type: ignore[import]
        return Path(_th(tenant_id)) / "global" / "ato"
    except ImportError:
        # Standalone mode — paths module not on sys.path.
        corvin_home = Path(os.environ.get("CORVIN_HOME", Path.home() / ".corvin"))
        return corvin_home / "global" / "ato"
    # Any other exception (e.g. ValueError for invalid tenant_id) propagates.


def _stats_path(tenant_id: str | None = None) -> Path:
    return _ato_dir(tenant_id) / "loss_stats.json"


def _load(tenant_id: str | None = None) -> dict[str, Any]:
    p = _stats_path(tenant_id)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save(data: dict[str, Any], tenant_id: str | None = None) -> None:
    p = _stats_path(tenant_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, ensure_ascii=False, indent=2).encode()
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    try:
        os.write(fd, raw)
    finally:
        os.close(fd)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    Path(tmp).replace(p)


def _ema(current: float | None, new_val: float) -> float:
    if current is None:
        return float(new_val)
    return _EMA_ALPHA * float(new_val) + (1.0 - _EMA_ALPHA) * float(current)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_outcome(
    task_type: str,
    *,
    did_converge: bool,
    goal_revised: bool = False,
    strategy_corrected: bool = False,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Record a task outcome and update EMA stats for this task_type.

    Returns the updated stats dict. Emits advisory L16 WARNING events when
    alert thresholds are crossed and enough samples have accumulated.
    """
    with _write_lock:
        data = _load(tenant_id)
        entry: dict[str, Any] = dict(data.get(task_type) or {})

        samples = int(entry.get("samples", 0)) + 1
        conv_rate  = _ema(entry.get("convergence_rate"),         1.0 if did_converge else 0.0)
        goal_rate  = _ema(entry.get("goal_revision_rate"),       1.0 if goal_revised else 0.0)
        strat_rate = _ema(entry.get("strategy_correction_rate"), 1.0 if strategy_corrected else 0.0)

        entry = {
            "samples":                  samples,
            "convergence_rate":         round(conv_rate,  4),
            "goal_revision_rate":       round(goal_rate,  4),
            "strategy_correction_rate": round(strat_rate, 4),
        }
        data[task_type] = entry
        _save(data, tenant_id)

    if samples >= _MIN_SAMPLES:
        _maybe_alert(task_type, entry, tenant_id)

    return dict(entry)


def get_stats(task_type: str, tenant_id: str | None = None) -> dict[str, Any] | None:
    """Return EMA stats for a specific task_type, or None if not yet tracked."""
    return _load(tenant_id).get(task_type)


def get_summary(tenant_id: str | None = None) -> dict[str, Any]:
    """Return all tracked task_type stats as a dict."""
    return _load(tenant_id)


# ---------------------------------------------------------------------------
# Advisory alert emitter (best-effort — never blocks record_outcome)
# ---------------------------------------------------------------------------

def _maybe_alert(
    task_type: str,
    entry: dict[str, Any],
    tenant_id: str | None,
) -> None:
    """Best-effort L16 advisory alert. Uses audit_event() — never raises."""
    try:
        from audit import audit_event as _ae  # type: ignore[import]
    except ImportError:
        return

    tid = tenant_id or ""
    base: dict[str, Any] = {
        "task_type": task_type,
        "samples":   entry["samples"],
        "tenant_id": tenant_id or "_default",
    }

    if entry["convergence_rate"] < _CONV_ALERT_THRESHOLD:
        try:
            _ae("task_orchestrator.convergence_low",
                details={**base, "conv_rate": entry["convergence_rate"]},
                tenant_id=tid)
        except Exception:  # noqa: BLE001
            pass
    if entry["goal_revision_rate"] > _GOAL_ALERT_THRESHOLD:
        try:
            _ae("task_orchestrator.goal_template_weak",
                details={**base, "goal_revision_rate": entry["goal_revision_rate"]},
                tenant_id=tid)
        except Exception:  # noqa: BLE001
            pass
    if entry["strategy_correction_rate"] > _STRATEGY_ALERT_THRESHOLD:
        try:
            _ae("task_orchestrator.strategy_drift",
                details={**base, "strategy_correction_rate": entry["strategy_correction_rate"]},
                tenant_id=tid)
        except Exception:  # noqa: BLE001
            pass


__all__ = [
    "record_outcome",
    "get_stats",
    "get_summary",
]
