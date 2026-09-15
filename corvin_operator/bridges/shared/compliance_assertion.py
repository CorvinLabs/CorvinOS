"""compliance_assertion.py — Compliance Assertion Layer (CAL).

Runs a fixed set of pure-Python predicate functions (``cal_predicates.py``)
against every action *before* it produces a side effect. The predicates are
structurally independent of the LLM's reasoning chain — they run as Python
code, not as model instructions.

Usage
-----
Call ``assert_compliant(action_type, details)`` at every callsite that
transitions a compliance-sensitive state. On predicate failure, the function:

  1. Emits a CRITICAL ``compliance_assertion.violated`` event to the L16
     audit chain (best-effort — failure to audit does NOT flip the result
     to allow).
  2. Raises ``ComplianceViolation`` — callers MUST NOT catch this exception
     silently.

Design decisions
----------------
* **No LLM dependency** — predicates are pure functions over (str, dict).
* **Fail-closed** — any exception inside a predicate is treated as a
  violation (not as an allow). Predicate errors are surfaced as the reason
  string ``"predicate_error:<ExcType>"`` so operators can diagnose.
* **Best-effort audit** — the audit write path is wrapped in try/except so
  that a disk-full or misconfigured chain never causes a double-fault. The
  RAISE happens regardless.
* **No 'compliance-off' switch** — this module exports no env-var or flag
  that disables it. CLAUDE.md absolute constraint.
* **No anthropic import** — CI AST lint enforces this.

CI lint: this module MUST NOT ``import anthropic``.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path


class ComplianceViolation(Exception):
    """Raised when a CAL predicate denies an action.

    Callers must NOT catch this silently. It is a structural compliance
    boundary, not a soft validation error.
    """

    def __init__(self, action_type: str, reason: str) -> None:
        super().__init__(
            f"CAL violation [{action_type}]: {reason}"
        )
        self.action_type = action_type
        self.reason = reason


def assert_compliant(action_type: str, details: dict | None = None) -> None:
    """Assert that *action_type* with *details* passes all CAL predicates.

    Raises :class:`ComplianceViolation` on first predicate failure.
    Emits ``compliance_assertion.violated`` (CRITICAL) to the audit chain.

    ``details`` defaults to ``{}`` when omitted.
    """
    if details is None:
        details = {}

    # Import predicates lazily so the module is importable even when
    # cal_predicates.py is not yet on the path (tests can inject a fake).
    try:
        from cal_predicates import check_all  # type: ignore[import-not-found]
    except ImportError:
        _shared = Path(__file__).resolve().parent
        if str(_shared) not in sys.path:
            sys.path.insert(0, str(_shared))
        from cal_predicates import check_all  # type: ignore[import-not-found]

    try:
        allowed, reason = check_all(action_type, details)
    except Exception as exc:  # noqa: BLE001
        allowed = False
        reason = f"predicate_error:{type(exc).__name__}"

    if not allowed:
        _emit_violation_audit(action_type, reason, details)
        raise ComplianceViolation(action_type, reason)


def check_compliant(action_type: str, details: dict | None = None) -> tuple[bool, str]:
    """Non-raising variant. Returns (True, '') or (False, reason).

    Use when you need to branch on the result rather than propagate the
    exception. Still emits the CRITICAL audit event on failure.
    """
    if details is None:
        details = {}

    try:
        from cal_predicates import check_all  # type: ignore[import-not-found]
    except ImportError:
        _shared = Path(__file__).resolve().parent
        if str(_shared) not in sys.path:
            sys.path.insert(0, str(_shared))
        from cal_predicates import check_all  # type: ignore[import-not-found]

    try:
        allowed, reason = check_all(action_type, details)
    except Exception as exc:  # noqa: BLE001
        allowed = False
        reason = f"predicate_error:{type(exc).__name__}"

    if not allowed:
        _emit_violation_audit(action_type, reason, details)

    return allowed, reason


def run_predicate_self_test() -> tuple[bool, list[str]]:
    """Verify all predicates pass their own unit assertions.

    Called by boot self-test (self_test.py). Returns (ok, failures) where
    failures is a list of description strings for any predicate that
    raises an unexpected exception during introspection.

    This is NOT a full behavioral test — that lives in
    test_compliance_assertion.py. This only checks that every predicate is
    callable and returns the right shape.
    """
    try:
        from cal_predicates import ALL_PREDICATES  # type: ignore[import-not-found]
    except ImportError:
        _shared = Path(__file__).resolve().parent
        if str(_shared) not in sys.path:
            sys.path.insert(0, str(_shared))
        from cal_predicates import ALL_PREDICATES  # type: ignore[import-not-found]

    failures: list[str] = []
    for pred in ALL_PREDICATES:
        name = getattr(pred, "__name__", repr(pred))
        try:
            result = pred("__self_test__", {})
            if not isinstance(result, tuple) or len(result) != 2:
                failures.append(f"{name}: wrong return shape {result!r}")
                continue
            allowed, reason = result
            if not isinstance(allowed, bool):
                failures.append(f"{name}: allowed must be bool, got {type(allowed)}")
            if not isinstance(reason, str):
                failures.append(f"{name}: reason must be str, got {type(reason)}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{name}: raised {type(exc).__name__}: {exc}")

    return len(failures) == 0, failures


# ── Audit emission ────────────────────────────────────────────────────────


def _emit_violation_audit(
    action_type: str, reason: str, details: dict
) -> None:
    """Best-effort CRITICAL audit emit. Never raises — the ComplianceViolation
    raise is what actually enforces the block."""
    try:
        _forge_pkg = Path(__file__).resolve().parents[2] / "forge"
        if str(_forge_pkg) not in sys.path:
            sys.path.insert(0, str(_forge_pkg))
        from forge.security_events import write_event  # type: ignore

        # Resolve audit path the same way bridge/audit.py does.
        _corvin_home = _resolve_corvin_home()
        audit_path = _corvin_home / "global" / "forge" / "audit.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)

        # Strip details fields that could contain PII. Only pass
        # action_type, reason, and safe metadata to the chain.
        safe_details: dict = {
            "action_type": action_type[:200],
            "reason": reason[:500],
            "predicate_count": _predicate_count(),
        }
        write_event(
            audit_path,
            "compliance_assertion.violated",
            severity="CRITICAL",
            tool="",
            run_id="",
            details=safe_details,
        )
    except Exception:  # noqa: BLE001
        pass


def _resolve_corvin_home() -> Path:
    """Minimal resolver — mirrors forge.paths.corvin_home() inline."""
    env = os.environ.get("CORVIN_HOME") or os.environ.get("CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    return Path.home() / ".corvin"


def _predicate_count() -> int:
    try:
        from cal_predicates import ALL_PREDICATES  # type: ignore[import-not-found]
        return len(ALL_PREDICATES)
    except Exception:  # noqa: BLE001
        return -1


__all__ = [
    "ComplianceViolation",
    "assert_compliant",
    "check_compliant",
    "run_predicate_self_test",
]
