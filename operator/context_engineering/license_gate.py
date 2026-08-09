"""Context-Engineering license gate (Vibe Engineering, ADR-0276).

``enforce_ce_quota`` meters context-engineered turns on Free-Tier
(``context_engineering_units_per_day``, default 10) and DEGRADES over budget —
the OPPOSITE of the ACS gate, which blocks its run. ``True`` = run the CEL
(enrich); ``False`` = run the turn on **plain context** (no CE stages), a quiet,
fully-functional degrade (invariant I2), NEVER a block.

Design (review R1/R2 corrections, baked in):
- Own ``counter_file`` (``context_engineering_quota.json``) + ``feature`` key →
  a SEPARATE daily pool from ``compute_units_per_day`` (H1); CE and delegation
  never starve each other.
- ``load_license_from_env()`` FIRST — the console/bridge/scheduler process never
  loads the license at startup; without it ``_ACTIVE_LICENSE`` is None and
  ``get_limit()`` falls back to FREE_TIER, capping a PAID tenant at 10/day (M-B).
- Fail-CLOSED on a missing/shadowed license module: deny ENRICHMENT (the turn
  still runs on plain context — deny-enrichment, not deny-service), never
  fail-open into unmetered CE (I3).
- Idempotency ("one unit/turn") is the caller's job: call this ONCE at the
  ``build_brief`` boundary, never per stage (H3 — the counter always increments).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_OPERATOR = str(Path(__file__).resolve().parents[1])  # operator/


def _corvin_home() -> Path:
    """Same corvin_home the counter file lives under (global/license/…)."""
    try:
        if _OPERATOR not in sys.path:
            sys.path.insert(0, _OPERATOR)
        from forge.paths import corvin_home  # type: ignore  # noqa: PLC0415
        return Path(corvin_home())
    except Exception:  # noqa: BLE001
        return Path(os.environ.get("CORVIN_HOME") or (Path.home() / ".corvin"))


def enforce_ce_quota(tenant_id: str = "_default") -> bool:
    """Charge one context-engineering unit for this turn.

    Returns ``True`` to run the CEL (enrich), ``False`` to DEGRADE to plain
    context (the turn still runs — never a block). Fail-closed on import (deny
    enrichment); fail-open on operational I/O (enrich, don't punish a hiccup).
    """
    try:
        if _OPERATOR not in sys.path:
            sys.path.insert(0, _OPERATOR)
        from license.compute_quota import increment_and_check as _cq_inc  # type: ignore  # noqa: PLC0415
        from license.limits import LicenseLimitError as _CQErr  # type: ignore  # noqa: PLC0415
        from license.validator import load_license_from_env as _load_lic  # type: ignore  # noqa: PLC0415
        _load_lic()  # reflect the real tier, not FREE defaults (M-B)
    except ImportError:
        # Fail-CLOSED: deny enrichment (turn still served on plain context);
        # never fail-open into unmetered CE.
        return False

    try:
        _cq_inc(_corvin_home(), channel="context_engineering",
                chat_key=f"ce:{tenant_id}",
                feature="context_engineering_units_per_day",
                counter_file="context_engineering_quota.json")
    except _CQErr:
        # Over the daily CE budget → degrade to plain context (turn still runs).
        return False
    except Exception:  # noqa: BLE001 — operational I/O hiccup → fail-open (enrich)
        return True
    return True
