"""Corvin Gateway — multi-tenant REST API for third-party integration.

Introduced by ADR-0007 Phase 2.

The Gateway is the only third-party-facing surface for Corvin.
Direct access to bridges / adapter / forge / skill-forge / engine-registry
from outside the operator host is forbidden by ADR-0007. The Gateway
is **opt-in**: single-tenant operators never enable it and never pay
its complexity tax.

Sub-phases:
  * 2.1 (this commit) — bearer-token auth module + CLI
  * 2.2 — FastAPI app + ``POST /v1/tenants/{tid}/runs``
  * 2.3 — Run dispatch via engine layer
  * 2.4 — Webhook dispatch (HMAC-SHA256, at-least-once)
  * 2.5 — SSE streaming for run events
  * 2.6 — End-to-end smoke + audit chain + CLAUDE.md closure

See ``docs/decisions/0007-phase-2-implementation-plan.md`` for the full
sub-phase plan.
"""
from __future__ import annotations

try:
    from importlib.metadata import version as _dist_version

    __version__ = _dist_version("corvinos")
except Exception:  # noqa: BLE001 — not installed as a distribution (source checkout)
    __version__ = "0.0.0+unknown"

# Wheel-install: vendor operator subtrees onto sys.path so `from forge import paths`
# and similar bare imports resolve. No-op in source-tree mode (no _vendor/ dir).
try:
    from corvin_console._operator_bootstrap import ensure_operator_on_path as _boot
    _boot()
except ImportError:
    pass
