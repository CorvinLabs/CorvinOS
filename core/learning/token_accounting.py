"""Canonical token accounting for an Anthropic usage object.

An Anthropic usage carries FOUR disjoint billed token classes, not two:

    input_tokens                  fresh, non-cached input (the remainder ONLY)
    cache_creation_input_tokens   input newly written to the prompt cache (~1.25x price)
    cache_read_input_tokens       input read from the prompt cache (~0.1x price)
    output_tokens                 generated output (~5x price)

The three input classes are DISJOINT and ADDITIVE — `input_tokens` does NOT include the
cached ones. Reading only `input_tokens` (as chat_runtime + the old benchmark did) undercounts
real input massively: a measured turn showed input_tokens=2 while cache_read=24433 and
cache_creation=38060 — i.e. it captured 0.003% of the true input.

This module is the ONE place that sums them, so the benchmark, the dashboard fix, and any
cost model agree. It matches the existing inline canon at
`operator/orchestration/tde/worker_ipc.py` and `operator/orchestration/tde/tde_engine.py`
(which the TDE engine derived independently); those inline copies should eventually be
refactored to call this. Prices are deliberately NOT hardcoded here — there is no trustworthy
price table in the repo (the only one, claude_engine.py, is a ~10x-off placeholder with no
cache tiers), so cost is a separate, config-supplied concern; this module reports raw counts.
"""
from __future__ import annotations

from typing import Any


def _i(usage: Any, key: str) -> int:
    try:
        return int((usage or {}).get(key, 0) or 0)
    except Exception:  # noqa: BLE001 — a malformed usage must never crash accounting
        return 0


def token_components(usage: Any) -> dict[str, int]:
    """The four disjoint token classes, each as an int (0 when absent/malformed)."""
    return {
        "fresh_input": _i(usage, "input_tokens"),
        "cache_creation": _i(usage, "cache_creation_input_tokens"),
        "cache_read": _i(usage, "cache_read_input_tokens"),
        "output": _i(usage, "output_tokens"),
    }


def input_tokens_total(usage: Any) -> int:
    """True input = fresh + cache-creation + cache-read (disjoint, additive)."""
    c = token_components(usage)
    return c["fresh_input"] + c["cache_creation"] + c["cache_read"]


def total_tokens(usage: Any) -> int:
    """Total raw tokens processed = full input (all three classes) + output.

    This is the honest 'tokens processed' count. It is NOT a cost: cache-read is ~0.1x and
    cache-creation ~1.25x the price of fresh input, so a cost figure must weight the classes
    with a real price source — see the module docstring."""
    return input_tokens_total(usage) + _i(usage, "output_tokens")
