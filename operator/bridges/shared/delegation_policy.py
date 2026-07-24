"""ADR-0221 P1 — the shared delegation-routing RULE, one source of truth.

Today the TDE-vs-ACS routing decision lives only in the web console
(chat_runtime._delegation_engine_target). To run TDE from the bridge adapter
(all messengers) without duplicating — and drifting — the rule, the pure
decision lives here and BOTH surfaces call it.

The rule is intentionally a pure function of already-computed booleans, not of
the raw prompt: the big-data signal and the pool/availability checks are
surface-specific to compute (the console peeks its own pool, the bridge its
own), but the DECISION they feed must be identical everywhere. Keep it a pure
function so the routing matrix stays unit-testable and both callers cannot
diverge.
"""
from __future__ import annotations


def delegation_engine_target(
    *,
    force_delegate: bool,
    is_big_data: bool,
    tde_available: bool,
    quota_ok: bool,
) -> str:
    """Engine choice WITHIN the delegated branch: ``"tde"`` | ``"acs"`` (ADR-0217).

    1. ``force_delegate`` (an explicit ``/delegate``) → ACS — explicit user
       commands beat every classifier (delegation-routing.md §6 invariant).
    2. ``is_big_data`` → ACS — the manager/worker fan-out's per-worker context
       isolation genuinely beats TDE's full-context steps on volume; the ONLY
       auto-routed ACS trigger left.
    3. TDE unavailable OR the shared pool is exhausted (peek) → ACS — its branch
       owns the hardened ADR-0201 degrade ladder.
    4. Everything else → TDE, the default delegation engine.
    """
    if force_delegate:
        return "acs"
    if is_big_data:
        return "acs"
    if not (tde_available and quota_ok):
        return "acs"
    return "tde"
