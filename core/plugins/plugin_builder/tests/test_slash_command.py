"""``/plugin-builder`` console-command wiring tests (ADR-0253).

CLAUDE.md's feature-flag rule requires tests for BOTH states: flag-off must
preserve the pre-feature behaviour exactly (an unknown/pointer response, no
session created), and flag-on must exercise the real interview end to end.
"""
from __future__ import annotations

import pytest

corvin_console = pytest.importorskip("corvin_console")
from corvin_console import feature_flags, slash_commands  # noqa: E402

from plugin_builder import session_store  # noqa: E402

TENANT = "test-tenant-plugin-builder"
FINGERPRINT = "fp-1"


@pytest.fixture(autouse=True)
def _clear_session():
    session_store.clear(TENANT, FINGERPRINT)
    yield
    session_store.clear(TENANT, FINGERPRINT)


def _enable_flag(monkeypatch):
    monkeypatch.setattr(
        feature_flags, "is_enabled",
        lambda flag_id, tenant_id="_default": flag_id == "plugin_builder_enabled",
    )


def test_flag_off_returns_pointer_and_creates_no_session(monkeypatch):
    monkeypatch.setattr(feature_flags, "is_enabled", lambda *a, **k: False)
    reply = slash_commands.handle(
        "/plugin-builder", tier="free", tenant_id=TENANT, fingerprint=FINGERPRINT,
        configured_engine="native",
    )
    assert reply is not None
    assert "off" in reply.lower()
    assert session_store.get(TENANT, FINGERPRINT) is None


def test_flag_off_plain_text_is_not_intercepted(monkeypatch):
    monkeypatch.setattr(feature_flags, "is_enabled", lambda *a, **k: False)
    reply = slash_commands.handle(
        "just a normal message", tier="free", tenant_id=TENANT,
        fingerprint=FINGERPRINT, configured_engine="native",
    )
    assert reply is None  # falls through to the engine, unchanged from today


def test_flag_on_status_with_no_session(monkeypatch):
    _enable_flag(monkeypatch)
    reply = slash_commands.handle(
        "/plugin-builder status", tier="free", tenant_id=TENANT,
        fingerprint=FINGERPRINT, configured_engine="native",
    )
    assert "no plugin-builder interview is active" in reply.lower()


def test_flag_on_full_interview_writes_artifacts(monkeypatch, tmp_path):
    _enable_flag(monkeypatch)
    monkeypatch.setattr(slash_commands, "_plugin_builder_output_dir", lambda tenant_id: tmp_path)

    def turn(text):
        return slash_commands.handle(
            text, tier="free", tenant_id=TENANT, fingerprint=FINGERPRINT,
            configured_engine="native",
        )

    start = turn("/plugin-builder")
    assert "plugin-builder" in start.lower()
    assert session_store.get(TENANT, FINGERPRINT) is not None

    answers = [
        "Postgres Connector",
        "Query Postgres from a turn.",
        "Data analysts",
        "none",
        "MVP",
        "psycopg",
        "no",
        "yes",
        "db.example.com",
        "none",
        "mvp",
        "none",
    ]
    for a in answers:
        reply = turn(a)
        assert reply is not None

    final = turn("confirm")
    assert "written to" in final.lower()
    assert session_store.get(TENANT, FINGERPRINT) is None  # cleared after DONE
    assert (tmp_path / "community_postgres_connector").is_dir()


def test_flag_on_cancel_clears_session(monkeypatch):
    _enable_flag(monkeypatch)
    slash_commands.handle(
        "/plugin-builder", tier="free", tenant_id=TENANT, fingerprint=FINGERPRINT,
        configured_engine="native",
    )
    assert session_store.get(TENANT, FINGERPRINT) is not None
    reply = slash_commands.handle(
        "/plugin-builder cancel", tier="free", tenant_id=TENANT,
        fingerprint=FINGERPRINT, configured_engine="native",
    )
    assert "cancelled" in reply.lower()
    assert session_store.get(TENANT, FINGERPRINT) is None


def test_help_lists_plugin_builder():
    reply = slash_commands.handle(
        "/help", tier="free", tenant_id=TENANT, fingerprint=FINGERPRINT,
        configured_engine="native",
    )
    assert "/plugin-builder" in reply
