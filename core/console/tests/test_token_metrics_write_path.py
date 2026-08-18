"""Regression guard for the token-measurement write path (ADR-0365 amendment).

Every one of these assertions failed before 2026-08-18. The path was wrapped in
`except Exception: pass` at its call site in chat_runtime.py, so a broken
`end_turn()` looked exactly like "no turns yet" — which is why the dashboard sat
empty while appearing wired. Keep these: they are the only thing standing
between a silent telemetry regression and another round of seeded demo rows.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Point the hard-coded `~/.corvin/token_metrics.db` at a throwaway home."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / ".corvin"))
    import core.learning.token_measurement_hook as hook_mod
    # Drop any hook (and the one-shot latch) left by an earlier test.
    monkeypatch.setattr(hook_mod, "_hook", None, raising=False)
    monkeypatch.setattr(hook_mod, "_autoinit_attempted", False, raising=False)
    return tmp_path


def _rows(home: Path) -> list[sqlite3.Row]:
    db = home / ".corvin" / "token_metrics.db"
    assert db.exists(), "write path did not even create the database"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return list(conn.execute("SELECT * FROM token_metrics"))


def test_hook_auto_initialises_without_an_explicit_host_call(isolated_home):
    """The gateway host never called initialize_token_hook(); nothing recorded."""
    from core.learning.token_measurement_hook import get_token_hook

    assert get_token_hook() is not None


def test_record_turn_metrics_persists_a_row(isolated_home):
    """The exact call chat_runtime.py makes must land in the database."""
    from core.learning.token_measurement_hook import record_turn_metrics

    record_turn_metrics(
        turn_id="t1",
        session_id="s1",
        tenant_id="_default",
        input_tokens=1000,
        output_tokens=200,
        subsystems={"memory_lookup": 50, "skill_injection": 100},
    )

    rows = _rows(isolated_home)
    assert len(rows) == 1
    row = rows[0]
    assert row["turn_id"] == "t1"
    assert row["session_id"] == "s1"
    assert row["tenant_id"] == "_default"
    # Measured values — these are the point of the feature.
    assert row["input_tokens"] == 1000
    assert row["output_tokens"] == 200
    assert row["total_tokens"] == 1200
    # instance_id is NOT NULL; reading it from the payload silently dropped
    # every row via a swallowed IntegrityError.
    assert row["instance_id"]
    assert json.loads(row["subsystem_tokens"]) == {"memory_lookup": 50, "skill_injection": 100}


def test_baseline_is_filled_so_the_dashboard_is_not_blank(isolated_home):
    """baseline_tokens is an ESTIMATE (1800*complexity), never a measurement."""
    from core.learning.token_measurement_hook import record_turn_metrics

    record_turn_metrics(turn_id="t2", session_id="s2", tenant_id="_default",
                        input_tokens=800, output_tokens=100)

    row = _rows(isolated_home)[0]
    assert row["baseline_tokens"] and row["baseline_tokens"] > 0
    assert row["savings_tokens"] is not None
    assert row["savings_percent"] is not None


def test_rows_are_tenant_isolated(isolated_home):
    """A query for one tenant must never see another tenant's turns."""
    from core.learning.token_measurement_hook import record_turn_metrics
    from core.learning.token_metrics_db import TokenMetricsDB

    record_turn_metrics(turn_id="a", session_id="shared", tenant_id="tenant_a",
                        input_tokens=10, output_tokens=1)
    record_turn_metrics(turn_id="b", session_id="shared", tenant_id="tenant_b",
                        input_tokens=20, output_tokens=2)

    db = TokenMetricsDB()
    a = db.query_by_session("shared", "tenant_a")
    b = db.query_by_session("shared", "tenant_b")
    assert [r["turn_id"] for r in a] == ["a"]
    assert [r["turn_id"] for r in b] == ["b"]
