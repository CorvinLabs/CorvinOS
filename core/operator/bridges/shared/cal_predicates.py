"""cal_predicates.py — Compliance Assertion Layer predicate definitions.

Each predicate is a pure boolean function:

    predicate(action_type: str, details: dict) -> (allowed: bool, reason: str)

Predicates run independently of the LLM. They are called by
``compliance_assertion.py`` before any tool side-effect is executed.

Invariants
----------
* No I/O, no subprocess, no imports of anthropic.
* Each predicate MUST be fast (< 1 ms), deterministic, and side-effect-free.
* A failing predicate always returns (False, <non-empty reason string>).
* The predicate list is versioned here — operator-editable, never LLM-editable
  (path-gate blocks writes to this file).

Adding a predicate
------------------
1. Define the function with signature ``(action_type: str, details: dict) ->
   tuple[bool, str]``.
2. Add it to ``ALL_PREDICATES``.
3. Add a test in ``test_compliance_assertion.py`` covering both pass and deny.

CI lint: this module MUST NOT ``import anthropic``.
"""
from __future__ import annotations

from typing import Callable

PredicateFn = Callable[[str, dict], tuple[bool, str]]


# ── Predicate implementations ─────────────────────────────────────────────


def _p_forge_policy_write_always_deny(
    action_type: str, details: dict
) -> tuple[bool, str]:
    """Forge policy.json writes are ALWAYS denied via this layer.

    The path-gate (L10) is the primary defence; this predicate is
    defence-in-depth — it runs even when the path-gate is not in effect
    (e.g. MCP tool calls that bypass the hook chain).
    """
    if action_type in (
        "forge.policy_write",
        "forge_policy_write",
        "policy_write",
    ):
        return False, "forge policy writes are structurally forbidden (CAL-P1)"
    return True, ""


def _p_consent_self_grant_only(
    action_type: str, details: dict
) -> tuple[bool, str]:
    """Consent may only be granted by the affected user themselves.

    Blocks any path where ``grantor`` != ``grantee`` (owner granting on
    behalf of another observer violates GDPR Art. 7 — consent must be
    freely given by the data subject).

    FIX-10: also denies when grantor or grantee is missing/empty entirely.
    """
    if action_type not in ("consent.grant", "consent_grant"):
        return True, ""
    grantor = details.get("grantor") or details.get("uid")
    grantee = details.get("grantee") or details.get("uid")
    # Deny if identity is missing entirely (FIX-10)
    if not grantor or not grantee:
        return False, "consent grant blocked: missing grantor or grantee identity (CAL-P2)"
    if grantor != grantee:
        return (
            False,
            f"consent grant blocked: grantor {grantor!r} ≠ grantee {grantee!r} (CAL-P2)",
        )
    return True, ""


def _p_session_reset_requires_audit(
    action_type: str, details: dict
) -> tuple[bool, str]:
    """Session reset must confirm the audit-chain write completed first.

    The ``audit_write_confirmed`` flag MUST be True before any rmtree is
    allowed. Absent / False means the audit-first invariant was not met.
    """
    if action_type in ("session.reset", "session_reset"):
        confirmed = details.get("audit_write_confirmed")
        if not confirmed:
            return (
                False,
                "session reset blocked: audit_write_confirmed is not True — "
                "L16 audit-first invariant requires the audit event to be "
                "written before any rmtree (CAL-P3)",
            )
    return True, ""


def _p_compliance_mode_never_off(
    action_type: str, details: dict
) -> tuple[bool, str]:
    """A 'compliance-off mode' may never be activated via any path.

    Blocks action_types that would globally disable compliance checks.
    CLAUDE.md: 'Don't add a compliance-off mode via any env var or flag.'
    """
    _forbidden = {
        "compliance.disable",
        "compliance_disable",
        "compliance.off",
        "compliance_off",
        "audit.disable",
        "audit_disable",
        "path_gate.disable",
        "consent_gate.disable",
    }
    if action_type in _forbidden:
        return (
            False,
            f"compliance-off mode is structurally forbidden: {action_type!r} "
            "(CAL-P4 — CLAUDE.md absolute constraint)",
        )
    return True, ""


def _p_disclosure_bypass_never(
    action_type: str, details: dict
) -> tuple[bool, str]:
    """EU AI Act Art. 50 disclosure may never be bypassed.

    Blocks any action that would suppress or skip the one-time disclosure
    card delivery.
    """
    _forbidden = {
        "disclosure.skip",
        "disclosure_skip",
        "disclosure.disable",
        "disclosure_disable",
        "disclosure.bypass",
    }
    if action_type in _forbidden:
        return (
            False,
            f"disclosure bypass is structurally forbidden: {action_type!r} "
            "(CAL-P5 — EU AI Act Art. 50)",
        )
    return True, ""


def _p_audit_chain_integrity_never_skip(
    action_type: str, details: dict
) -> tuple[bool, str]:
    """Events may never be removed or selectively skipped from the audit chain.

    CLAUDE.md: 'Don't lower audit-chain integrity — no event skips the
    hash-chain link.'
    """
    _forbidden = {
        "audit.skip_event",
        "audit_skip",
        "audit.truncate",
        "audit.delete_record",
        "audit.rewrite",
    }
    if action_type in _forbidden:
        return (
            False,
            f"audit chain manipulation is structurally forbidden: "
            f"{action_type!r} (CAL-P6 — GDPR Art. 30/32)",
        )
    return True, ""


def _p_social_no_spawn(action_type: str, details: dict) -> tuple[bool, str]:
    """Social posts MUST NEVER trigger WorkerEngine spawns (CAL-P7, ADR-0053).

    The social layer (L39) is a no-spawn zone — receiving or publishing posts
    must never initiate a WorkerEngine subprocess. This is a structural
    invariant to prevent remote actors from triggering arbitrary code execution
    via the federation inbox.
    """
    _SOCIAL_SPAWN_ACTIONS = frozenset({
        "social.spawn_worker",
        "social.post_spawn",
        "social.trigger_worker",
        "social.incoming_post_spawn",
    })
    if action_type in _SOCIAL_SPAWN_ACTIONS:
        return False, (
            "social posts MUST NOT trigger WorkerEngine spawns — "
            "structural L39 invariant (CAL-P7)"
        )
    return True, ""


# ── Predicate registry ────────────────────────────────────────────────────

ALL_PREDICATES: list[PredicateFn] = [
    _p_forge_policy_write_always_deny,
    _p_consent_self_grant_only,
    _p_session_reset_requires_audit,
    _p_compliance_mode_never_off,
    _p_disclosure_bypass_never,
    _p_audit_chain_integrity_never_skip,
    _p_social_no_spawn,
]


def check_all(action_type: str, details: dict) -> tuple[bool, str]:
    """Run every predicate. Returns (True, '') if all pass.

    On first failure returns (False, <reason>) immediately — predicates
    are short-circuit evaluated in list order.
    """
    for pred in ALL_PREDICATES:
        allowed, reason = pred(action_type, details)
        if not allowed:
            return False, reason
    return True, ""


__all__ = [
    "ALL_PREDICATES",
    "PredicateFn",
    "check_all",
]
