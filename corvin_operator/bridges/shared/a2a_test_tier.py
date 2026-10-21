"""Test-only: run an A2A protocol test file as a MEMBER install.

``a2a.network`` is member-only (ADR-0700 §2.1; gate ``a2a_license_gate``, 28ad07b54). Under pytest
``conftest._a2a_member_tier`` pins the tier for the A2A test files; ``run-all-tests.sh`` runs the
same files as plain scripts, where no fixture applies — every L38 suite there failed from the day
the gate landed (review 2026-10-21). A script calls :func:`pin_member_tier` before its tests run.
The gate, the limits matrix and the audit run unmodified; only the tier RESOLVER answers "member".
Never imported by production code.
"""
from __future__ import annotations


def pin_member_tier(tier: str = "member") -> None:
    try:
        from corvin_operator.license import capability_api
    except ImportError:          # licensing absent: the gate refuses on its own (fail-closed)
        return
    capability_api.active_tier = lambda **_k: tier  # type: ignore[assignment]
    try:
        import a2a_license_gate

        a2a_license_gate.reset_allow_cache()
    except Exception:  # noqa: BLE001 — the cache is a convenience
        pass
