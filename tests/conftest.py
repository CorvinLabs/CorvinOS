"""Repo-root test suite conftest.

Audit-chain isolation (adversarial review 2026-07-24, empirically verified):
several TDE suites construct AdaptiveDelegationExecutor / SendIntegration /
bench directly, whose tde_audit.emit() resolves the REAL audit backend — a
plain `pytest tests/` run appended dozens of permanent events to the live
hash-chained audit.jsonl (unremovable test noise in a GDPR Art. 30 record;
on a pinned-service host it would land in the production chain). Redirect
every test's audit chain to tmp by default. Tests that need a specific path
still win: monkeypatch.setenv overrides this env for their own scope.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_audit_chain_for_all_tests(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    # ADR-0219 R4: the loss-profile tracker now persists MEASURED entries to a
    # TENANT-scoped file under the real corvin_home. A test using a real-looking
    # session_key ("tenant:sid") would otherwise write into (and read stale data
    # from) the LIVE tenant dir, polluting the filesystem AND leaking across
    # tests by collection order (same failure class as the audit chain above).
    # Off by default; the R4 persistence tests opt in by passing persist_path
    # directly to LossProfileTracker, which bypasses this env gate.
    monkeypatch.setenv("CORVIN_TDE_LOSS_PERSIST", "0")
