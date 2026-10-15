"""Savings cap — the Free-tier allowance for AUTOMATIC model downgrading (ADR-2259, PLAN-0955 S3).

``evaluate()`` answers one question for ``model_selector.resolve_os_model``:
"has this installation already saved its allowance this month, so the automatic
tiers must stop choosing a cheaper model than the default?"

Single source of the number
---------------------------
The Free ceiling (``ratio``, ``floor_usd``) lives ONLY in
``CAPABILITIES["cost.optimize"]["free"]``. The operator's Settings value is a
*choice* stored in ``<tenant>/global/cost_optimize.json``:

* Free   — may only make the cap STRICTER: ``min(choice, ceiling)`` for the
  ratio and for the floor alike; ``0.0`` switches the automation off.
* Member — the choice as given; ``null`` = unlimited; a number is a voluntary
  self-cap.

There is no env var, flag or dev override (CONCEPT-0041: no kill-switch, no test
trust root in the product). Deterrence, never a lock: a fork can delete the
check. The fork-resistant part is data, not this gate (ADR-2259 §6).

Fail contract
-------------
* licence module missing / unreadable        -> tier ``free`` (the free allowance,
  never unlimited, CONCEPT-0041 rule 4)
* meter unreadable (I/O)                     -> NOT capped (``meter_unavailable``)
* meter still warming up (first ~0.3 s)      -> NOT capped (``meter_warming``);
  the request path never blocks on the chain scan
* settings file malformed                    -> ignored, tier default applies

Stdlib only at import time; ``core`` / ``license`` are imported lazily.
"""
from __future__ import annotations

import json
import math
import os
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

SETTINGS_FILE = "cost_optimize.json"
#: key -> (lo, hi). The ONLY keys the settings file / API may carry.
CHOICE_KEYS: dict[str, tuple[float, float]] = {
    "cap_ratio": (0.0, 1.0),
    "floor_usd": (0.0, 1000.0),
}


# ── lazy imports (the bridge daemon has operator/, not always core/, on sys.path) ──

def _ensure_repo_on_path() -> None:
    repo = str(Path(__file__).resolve().parents[3])
    if repo not in sys.path:
        sys.path.insert(0, repo)


def _core_module(dotted: str):
    try:
        import importlib  # noqa: PLC0415
        return importlib.import_module(dotted)
    except ImportError:
        _ensure_repo_on_path()
        import importlib  # noqa: PLC0415
        return importlib.import_module(dotted)


def _capability() -> dict[str, Any]:
    try:
        from license.limits import CAPABILITIES  # type: ignore  # noqa: PLC0415
    except ImportError:
        from corvin_operator.license.limits import CAPABILITIES  # type: ignore  # noqa: PLC0415
    return CAPABILITIES["cost.optimize"]


def ceilings() -> tuple[float, float]:
    """(ratio, floor_usd) ceiling — depends on license tier.

    Both tiers are read from the capability matrix; no ratio literal lives here.
    """
    tier = _tier()
    cap = _capability()
    # A matrix without a complete "member" entry falls back to the Free ceiling
    # (fail-safe): a missing key must never read as "unlimited".
    member = cap.get("member")
    if (tier == "member" and isinstance(member, Mapping)
            and "ratio" in member and "floor_usd" in member):
        return float(member["ratio"]), float(member["floor_usd"])
    free = cap["free"]
    return float(free["ratio"]), float(free["floor_usd"])


def _licence_active_tier() -> str:
    try:
        from license.validator import active_tier, load_license_from_env  # type: ignore  # noqa: PLC0415
    except ImportError:
        from corvin_operator.license.validator import (  # type: ignore  # noqa: PLC0415
            active_tier, load_license_from_env,
        )
    load_license_from_env()      # idempotent; reflects the real tier, not FREE defaults
    return active_tier()


def _tier() -> str:
    """'member' only on a positive, verified answer; every failure is 'free'."""
    try:
        return "member" if _licence_active_tier() == "member" else "free"
    except Exception:  # noqa: BLE001
        return "free"


# ── the operator's stored choice ─────────────────────────────────────────────

def _num(value: Any, lo: float, hi: float) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    return f if math.isfinite(f) and lo <= f <= hi else None


def validate_choice(updates: dict[str, Any]) -> tuple[dict[str, Optional[float]], list[str]]:
    """Validate a PUT body. ``cap_ratio: null`` means "tier default"."""
    clean: dict[str, Optional[float]] = {}
    errors: list[str] = []
    for key, raw in updates.items():
        rng = CHOICE_KEYS.get(key)
        if rng is None:
            errors.append(f"unknown key: {key}")
            continue
        if raw is None and key == "cap_ratio":
            clean[key] = None
            continue
        val = _num(raw, *rng)
        if val is None:
            errors.append(f"{key}: must be a finite number between {rng[0]} and {rng[1]}")
        else:
            clean[key] = val
    return clean, errors


def settings_path(tenant_id: str) -> Path:
    tenant_home = _core_module("core.paths.tenant").tenant_home
    return Path(tenant_home(tenant_id)) / "global" / SETTINGS_FILE


def read_choice(tenant_id: str) -> dict[str, Optional[float]]:
    """The stored choice; any invalid value is ignored (None). Never raises."""
    out: dict[str, Optional[float]] = {k: None for k in CHOICE_KEYS}
    try:
        data = json.loads(settings_path(tenant_id).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — absent, unreadable or not JSON
        return out
    if not isinstance(data, dict):
        return out
    for key, rng in CHOICE_KEYS.items():
        out[key] = _num(data.get(key), *rng)
    return out


# ── resolution ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Allowance:
    tier: str
    choice_ratio: Optional[float]
    choice_floor: Optional[float]
    ceiling_ratio: float
    ceiling_floor: float
    effective_ratio: Optional[float]     # None = unlimited
    effective_floor: float
    clamped: bool                        # the stored choice asked for more than the tier allows


def resolve(tenant_id: str) -> Allowance:
    ceil_ratio, ceil_floor = ceilings()
    choice = read_choice(tenant_id)
    c_ratio, c_floor = choice["cap_ratio"], choice["floor_usd"]
    tier = _tier()
    if tier == "member":
        return Allowance(tier, c_ratio, c_floor, ceil_ratio, ceil_floor,
                         c_ratio, c_floor if c_floor is not None else ceil_floor, False)
    eff_ratio = ceil_ratio if c_ratio is None else min(c_ratio, ceil_ratio)
    eff_floor = ceil_floor if c_floor is None else min(c_floor, ceil_floor)
    clamped = (c_ratio is not None and c_ratio > ceil_ratio) or \
              (c_floor is not None and c_floor > ceil_floor)
    return Allowance(tier, c_ratio, c_floor, ceil_ratio, ceil_floor, eff_ratio, eff_floor, clamped)


@dataclass(frozen=True)
class CapState:
    capped: bool
    reason: str
    allowance: Allowance
    reading: Any = None                  # savings_meter.MeterReading | None


def _default_model(tenant_id: str) -> str:
    try:
        from model_selector import high_model  # type: ignore  # noqa: PLC0415
    except ImportError:
        from .model_selector import high_model  # type: ignore  # noqa: PLC0415
    return high_model(tenant_id)


def evaluate(
    tenant_id: str = "_default",
    *,
    default_model: Optional[str] = None,
    block: bool = False,
) -> CapState:
    """Should the automatic tiers stop choosing a model cheaper than the default?

    Never raises. ``block=False`` (the request path) never waits for the chain scan.
    """
    a = resolve(tenant_id)
    if a.effective_ratio is None:
        return CapState(False, "tier_unlimited", a)
    if a.effective_ratio <= 0.0:
        return CapState(True, "automation_off", a)
    try:
        meter = _core_module("core.learning.savings_meter")
        reading = meter.read(tenant_id, default_model or _default_model(tenant_id), block=block)
    except Exception:  # noqa: BLE001 — a measurement fault must not change routing
        return CapState(False, "meter_unavailable", a)
    if reading is None:
        return CapState(False, "meter_warming", a)
    if not reading.priced or reading.ratio is None:
        return CapState(False, "not_measurable", a, reading)
    if reading.counterfactual_usd < a.effective_floor:
        return CapState(False, "below_floor", a, reading)
    if reading.ratio >= a.effective_ratio:
        return CapState(True, "cap_reached", a, reading)
    return CapState(False, "under_cap", a, reading)


# ── audit (ADR-2259 §5) ──────────────────────────────────────────────────────
# State TRANSITIONS only: a record per capped turn would bury the chain. The first
# decisive observation in a process counts as a transition only when it is
# "capped" (the cap is then in force and the operator should be able to see it).

_NOT_DECISIVE = frozenset({"meter_warming", "meter_unavailable", "not_measurable"})
_LAST_CAPPED: dict[str, bool] = {}
_NOTES_LOCK = threading.Lock()


def _reset_notes() -> None:
    with _NOTES_LOCK:
        _LAST_CAPPED.clear()


def _audit(event_type: str, details: dict[str, Any]) -> None:
    """Best-effort write to the tenant's hash chain (audit never breaks a turn)."""
    try:
        try:
            from model_selector import _write_event  # type: ignore  # noqa: PLC0415
        except ImportError:
            from .model_selector import _write_event  # type: ignore  # noqa: PLC0415
        _write_event(event_type, details)
    except Exception:  # noqa: BLE001
        pass


def note_state(tenant_id: str, state: CapState) -> None:
    """Record ``cost.optimize_capped`` / ``cost.optimize_resumed`` on a transition."""
    if state.reason in _NOT_DECISIVE:
        return
    with _NOTES_LOCK:
        prev = _LAST_CAPPED.get(tenant_id)
        _LAST_CAPPED[tenant_id] = state.capped
    if prev is not None and prev == state.capped:
        return                                   # no change
    if prev is None and not state.capped:
        return                                   # first sight, not capped: nothing to say
    a, r = state.allowance, state.reading
    details: dict[str, Any] = {"tenant_id": tenant_id, "tier": a.tier, "reason": state.reason}
    if a.effective_ratio is not None:
        details["cap"] = a.effective_ratio
    if r is not None:
        details["month"] = r.month
        details["counterfactual_usd"] = round(r.counterfactual_usd, 4)
        if r.ratio is not None:
            details["ratio"] = round(r.ratio, 6)
    _audit("cost.optimize_capped" if state.capped else "cost.optimize_resumed", details)


def emit_config_changed(tenant_id: str, before: Allowance, after: Allowance) -> None:
    """Record an operator change of the stored choice (what was asked vs what is in force)."""
    details: dict[str, Any] = {
        "tenant_id": tenant_id, "tier": after.tier, "clamped": after.clamped,
    }
    for key, b, a in (("cap_ratio", before.choice_ratio, after.choice_ratio),
                      ("floor_usd", before.choice_floor, after.choice_floor)):
        if b is not None:
            details[f"{key}_before"] = b
        if a is not None:
            details[f"{key}_after"] = a
    if after.effective_ratio is not None:
        details["effective_ratio"] = after.effective_ratio
    _audit("cost.optimize_config_changed", details)
