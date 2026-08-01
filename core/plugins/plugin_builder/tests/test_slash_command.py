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
from plugin_builder import turn as pb_turn  # noqa: E402

TENANT = "test-tenant-plugin-builder"
FINGERPRINT = "fp-1"  # login identity — NOT the interview scoping key
SESSION_KEY = "chat-session-1"  # chat-conversation id — what interview state is actually keyed by


@pytest.fixture(autouse=True)
def _clear_session():
    session_store.clear(TENANT, SESSION_KEY)
    yield
    session_store.clear(TENANT, SESSION_KEY)


def _enable_flag(monkeypatch):
    monkeypatch.setattr(
        feature_flags, "is_enabled",
        lambda flag_id, tenant_id="_default": flag_id == "plugin_builder_enabled",
    )


def test_flag_off_returns_pointer_and_creates_no_session(monkeypatch):
    monkeypatch.setattr(feature_flags, "is_enabled", lambda *a, **k: False)
    reply = slash_commands.handle(
        "/plugin-builder", tier="free", tenant_id=TENANT, fingerprint=FINGERPRINT, session_key=SESSION_KEY,
        configured_engine="native",
    )
    assert reply is not None
    assert "off" in reply.lower()
    assert session_store.get(TENANT, SESSION_KEY) is None


def test_flag_off_plain_text_is_not_intercepted(monkeypatch):
    monkeypatch.setattr(feature_flags, "is_enabled", lambda *a, **k: False)
    reply = slash_commands.handle(
        "just a normal message", tier="free", tenant_id=TENANT,
        fingerprint=FINGERPRINT, session_key=SESSION_KEY, configured_engine="native",
    )
    assert reply is None  # falls through to the engine, unchanged from today


def test_flag_on_status_with_no_session(monkeypatch):
    _enable_flag(monkeypatch)
    reply = slash_commands.handle(
        "/plugin-builder status", tier="free", tenant_id=TENANT,
        fingerprint=FINGERPRINT, session_key=SESSION_KEY, configured_engine="native",
    )
    assert "no plugin-builder interview is active" in reply.lower()


def test_flag_on_full_interview_writes_artifacts(monkeypatch, tmp_path):
    _enable_flag(monkeypatch)
    monkeypatch.setattr(pb_turn, "output_dir", lambda tenant_id: tmp_path)

    def turn(text):
        return slash_commands.handle(
            text, tier="free", tenant_id=TENANT, fingerprint=FINGERPRINT, session_key=SESSION_KEY,
            configured_engine="native",
        )

    start = turn("/plugin-builder")
    assert "plugin-builder" in start.lower()
    assert session_store.get(TENANT, SESSION_KEY) is not None

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
    assert session_store.get(TENANT, SESSION_KEY) is None  # cleared after DONE
    assert (tmp_path / "community_postgres_connector").is_dir()


def test_flag_on_cancel_clears_session(monkeypatch):
    _enable_flag(monkeypatch)
    slash_commands.handle(
        "/plugin-builder", tier="free", tenant_id=TENANT, fingerprint=FINGERPRINT, session_key=SESSION_KEY,
        configured_engine="native",
    )
    assert session_store.get(TENANT, SESSION_KEY) is not None
    reply = slash_commands.handle(
        "/plugin-builder cancel", tier="free", tenant_id=TENANT,
        fingerprint=FINGERPRINT, session_key=SESSION_KEY, configured_engine="native",
    )
    assert "cancelled" in reply.lower()
    assert session_store.get(TENANT, SESSION_KEY) is None


def test_interview_does_not_leak_across_chat_tabs_with_same_login(monkeypatch):
    """Two conversations under the SAME login fingerprint (two browser tabs,
    or a saved chat re-opened) must never share one interview. Regression
    test for the 2026-08-01 fix: the console used to key /plugin-builder
    state on the login fingerprint instead of the chat-conversation id, so
    starting an interview in tab A made tab B's plain-text turns get
    silently captured by it too."""
    _enable_flag(monkeypatch)
    other_session_key = "chat-session-2"
    try:
        start = slash_commands.handle(
            "/plugin-builder", tier="free", tenant_id=TENANT,
            fingerprint=FINGERPRINT, session_key=SESSION_KEY, configured_engine="native",
        )
        assert "plugin-builder" in start.lower()
        assert session_store.get(TENANT, SESSION_KEY) is not None
        # Same login fingerprint, but a DIFFERENT chat conversation — must see
        # no active interview at all, and a plain-text turn must fall through
        # to the engine (None) rather than being read as an interview answer.
        assert session_store.get(TENANT, other_session_key) is None
        other_reply = slash_commands.handle(
            "some unrelated chat message",
            tier="free", tenant_id=TENANT,
            fingerprint=FINGERPRINT, session_key=other_session_key,
            configured_engine="native",
        )
        assert other_reply is None
        other_status = slash_commands.handle(
            "/plugin-builder status", tier="free", tenant_id=TENANT,
            fingerprint=FINGERPRINT, session_key=other_session_key, configured_engine="native",
        )
        assert "no plugin-builder interview is active" in other_status.lower()
        # The original tab's interview is untouched by the other tab's turns.
        assert session_store.get(TENANT, SESSION_KEY) is not None
    finally:
        session_store.clear(TENANT, other_session_key)


def test_help_lists_plugin_builder():
    reply = slash_commands.handle(
        "/help", tier="free", tenant_id=TENANT, fingerprint=FINGERPRINT, session_key=SESSION_KEY,
        configured_engine="native",
    )
    assert "/plugin-builder" in reply


# ── ADR-0262/0263 flags — flag-off regression + flag-on wiring ─────────────

def _enable(monkeypatch, *flag_ids):
    on = {"plugin_builder_enabled", *flag_ids}
    monkeypatch.setattr(
        feature_flags, "is_enabled",
        lambda flag_id, tenant_id="_default": flag_id in on,
    )


def test_new_flags_off_by_default_base_flag_still_gives_legacy_flow(monkeypatch):
    """Base flag on, all three ADR-0262 flags off (the actual shipped
    default combination) — session must be the ORIGINAL 4-phase flow."""
    _enable(monkeypatch)
    turn_reply = slash_commands.handle(
        "/plugin-builder", tier="free", tenant_id=TENANT, fingerprint=FINGERPRINT, session_key=SESSION_KEY,
        configured_engine="native",
    )
    assert turn_reply is not None
    session = session_store.get(TENANT, SESSION_KEY)
    assert session is not None
    assert session.idea_first is False
    assert session.checkpoint_enabled is False
    assert session.e2e_tests_enabled is False


def test_ideas_flag_off_returns_pointer_and_creates_no_session(monkeypatch):
    _enable(monkeypatch, "plugin_builder_idea_first_interview")  # ideas_mode NOT included
    reply = slash_commands.handle(
        "/plugin-builder --ideas", tier="free", tenant_id=TENANT,
        fingerprint=FINGERPRINT, session_key=SESSION_KEY, configured_engine="native",
    )
    assert reply is not None
    assert "off" in reply.lower()
    assert session_store.get(TENANT, SESSION_KEY) is None


def test_ideas_flag_on_starts_the_ack_gate(monkeypatch):
    from plugin_builder import ideation

    _enable(monkeypatch, "plugin_builder_idea_first_interview", "plugin_builder_ideas_mode")
    try:
        reply = slash_commands.handle(
            "/plugin-builder --ideas", tier="free", tenant_id=TENANT,
            fingerprint=FINGERPRINT, session_key=SESSION_KEY, configured_engine="native",
        )
        assert reply is not None
        assert "?" in reply
        assert ideation._get(TENANT, SESSION_KEY) is not None  # noqa: SLF001
    finally:
        ideation.clear(TENANT, SESSION_KEY)


def test_idea_first_and_checkpoint_and_e2e_tests_full_flow(monkeypatch, tmp_path):
    """Real end-to-end run through the actual console entry point with all
    three ADR-0262 flags on: idea-first -> checkpoint -> scaffold+tests."""
    pytest.importorskip("corvin_plugins")
    _enable(
        monkeypatch,
        "plugin_builder_idea_first_interview",
        "plugin_builder_checkpoint_review",
        "plugin_builder_generate_e2e_tests",
    )
    monkeypatch.setattr(pb_turn, "output_dir", lambda tenant_id: tmp_path)

    def turn(text):
        return slash_commands.handle(
            text, tier="free", tenant_id=TENANT, fingerprint=FINGERPRINT, session_key=SESSION_KEY,
            configured_engine="native",
        )

    turn("/plugin-builder")
    turn("Talks to api.example.com, needs an API key, uses `requests`, routes messages.")
    turn("Router Plugin")

    session = session_store.get(TENANT, SESSION_KEY)
    while session is not None and session.phase.value == "confirm_gaps":
        turn("none")
        session = session_store.get(TENANT, SESSION_KEY)

    checkpoint_reply = turn("confirm")
    assert "nothing beyond these documents" in checkpoint_reply.lower() or \
           "nichts" in checkpoint_reply.lower()
    session = session_store.get(TENANT, SESSION_KEY)
    assert session is not None
    assert session.phase.value == "checkpoint"

    final = turn("confirm")
    assert "written to" in final.lower()
    assert session_store.get(TENANT, SESSION_KEY) is None
    written_dirs = [p for p in tmp_path.iterdir() if p.name != "corvin_home"]
    assert written_dirs, "nothing was written"
    scaffold_dir = written_dirs[0]
    assert (scaffold_dir / "docs").is_dir()
    tests_dir = scaffold_dir / "tests"
    assert tests_dir.is_dir()
    assert list(tests_dir.glob("test_*_e2e.py"))
