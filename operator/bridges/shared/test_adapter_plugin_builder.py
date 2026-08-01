#!/usr/bin/env python3
"""test_adapter_plugin_builder.py — /plugin-builder reachable from a bridge
(Discord, Slack, ...), not just the Console web-chat (ADR-0253 follow-up).

`adapter.process_one`'s new `_plugin_builder_bridge_reply` hook drives the
SAME `plugin_builder.turn`/`plugin_builder.session_store` modules the Console
uses, keyed by `(tenant_id, "<channel>:<chat_key>")` — NOT bare chat_key,
because chat_key (`chat_id or sender`) is just one messenger's id space; two
different channels can produce the identical string. An adversarial review
(2026-07-27) reproduced a cross-channel session hijack before the `channel:`
namespace prefix was added — `test_same_chat_id_on_two_channels_does_not_collide`
below is that repro turned into a permanent regression test.

CLAUDE.md's feature-flag rule requires both states tested: flag off must
fall through to a normal engine turn unchanged, flag on must drive the real
interview end to end through actual inbox->outbox files.

Run: python3 operator/bridges/shared/test_adapter_plugin_builder.py
  or: pytest -q operator/bridges/shared/test_adapter_plugin_builder.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
_REPO = HERE.parents[2]
_PLUGINS = _REPO / "core" / "plugins"
if str(_PLUGINS) not in sys.path:
    sys.path.insert(0, str(_PLUGINS))


def _session_key(channel: str, chat_id: str) -> str:
    """Mirrors adapter.py's `f"{channel}:{chat_key}"` session_store key."""
    return f"{channel}:{chat_id}"


def _fresh_adapter(env_overrides: dict):
    """Same recipe as test_bg_task.py/test_adapter_reset_prewarn.py: isolate
    CORVIN_HOME + inbox/outbox/processed, purge and re-import `adapter` so
    every lazily-imported module reads the fresh env, and stub the house-
    rules classifier so a real Hermes/Ollama call is never attempted."""
    os.environ["CORVIN_OS_ENGINE"] = "claude_code"
    os.environ["ADAPTER_FAKE_CLAUDE"] = "1"
    os.environ["ADAPTER_DISABLE_VOICE"] = "1"
    os.environ["ADAPTER_ROUTING_MODE"] = "off"
    os.environ.pop("CORVIN_TENANT_ID", None)  # defaults to "_default"
    for k, v in env_overrides.items():
        os.environ[k] = v
    for mod_name in list(sys.modules):
        if mod_name in ("adapter", "session_reset") or mod_name.startswith("forge"):
            del sys.modules[mod_name]
    import adapter  # type: ignore  # noqa: E402
    adapter._house_rules_classifier = (
        lambda task, rules, auth, **_kw: ("", 1.0, "test-benign")
    )
    return adapter


def _enable_plugin_builder(home: Path, tenant_id: str = "_default") -> None:
    overlay_dir = home / "tenants" / tenant_id / "global"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    (overlay_dir / "features.json").write_text(
        json.dumps({"flags": {"plugin_builder_enabled": True}}), encoding="utf-8"
    )


def _enable_flags(home: Path, *flag_ids: str, tenant_id: str = "_default") -> None:
    """Same as `_enable_plugin_builder`, plus any ADR-0262/0263 sub-flags —
    added for the bridge `--ideas` coverage gap flagged by the ADR-0262
    review round 1 (Quality finding: this file predated those flags and
    never exercised them, so `run-all-tests.sh` could pass green without
    ever running the new bridge code path)."""
    overlay_dir = home / "tenants" / tenant_id / "global"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    flags = {"plugin_builder_enabled": True}
    flags.update({f: True for f in flag_ids})
    (overlay_dir / "features.json").write_text(
        json.dumps({"flags": flags}), encoding="utf-8"
    )


def _sandbox():
    base = Path(tempfile.mkdtemp(prefix="adapter-plugin-builder-"))
    inbox, outbox, processed, home = (
        base / "inbox", base / "outbox", base / "processed", base / "home",
    )
    for p in (inbox, outbox, processed, home):
        p.mkdir(parents=True)
    return base, inbox, outbox, processed, home


_SANDBOX_ENV_KEYS = (
    "ADAPTER_INBOX", "ADAPTER_OUTBOX", "ADAPTER_PROCESSED", "CORVIN_HOME",
    "ADAPTER_BRIDGES_DIR",
)


def _sandbox_env(base: Path, inbox: Path, outbox: Path, home: Path) -> dict:
    return {
        "ADAPTER_INBOX": str(inbox), "ADAPTER_OUTBOX": str(outbox),
        "ADAPTER_PROCESSED": str(base / "processed"), "CORVIN_HOME": str(home),
        # Without this, _load_channel_settings() falls back to the real
        # operator's operator/bridges/<channel>/settings.json — a synthetic
        # sender then fails the whitelist re-check and the message is
        # silently dropped as "private" (the exact test-vs-real-config
        # contamination test_adapter_btw.py's own comment documents).
        "ADAPTER_BRIDGES_DIR": str(base),
    }


def _send(adapter, inbox: Path, *, msg_id: str, channel: str, chat_id: str,
          sender: str, text: str) -> None:
    env = {"id": msg_id, "channel": channel, "from": sender,
           "chat_id": chat_id, "text": text, "ts": 0}
    f = inbox / f"{msg_id}.json"
    f.write_text(json.dumps(env))
    adapter.process_one(f, settings={"whitelist": [sender]})


def _last_ack_text(outbox: Path, msg_id: str) -> str:
    files = sorted(outbox.glob(f"{msg_id}_*.json"))
    assert files, f"no ack written for {msg_id}"
    return json.loads(files[-1].read_text())["text"]


POSTGRES_ANSWERS = [
    "Postgres Connector",
    "Query Postgres from a bridge turn without hand-rolled SQL glue.",
    "Data analysts on Discord",
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


def test_flag_off_plugin_builder_command_gets_pointer_not_engine_call() -> None:
    """Flag off: the LITERAL `/plugin-builder` command still gets a
    deterministic "it's off" pointer — mirroring the Console's own
    `_plugin_builder_command`, which answers this even while disabled rather
    than leaking the command text to the model as a confusing normal prompt.
    Ordinary plain text (not the command, and no active session) is the one
    that must reach the engine untouched — checked separately below."""
    base, inbox, outbox, _processed, home = _sandbox()
    try:
        adapter = _fresh_adapter(_sandbox_env(base, inbox, outbox, home))
        # No features.json written — flag defaults off.
        _send(adapter, inbox, msg_id="pb-off-1", channel="discord",
              chat_id="chan-pb-off", sender="u-pb-off", text="/plugin-builder")
        ack = _last_ack_text(outbox, "pb-off-1")
        assert "off" in ack.lower(), ack
        assert not ack.startswith("[fake]"), "must not have reached the engine"

        _send(adapter, inbox, msg_id="pb-off-2", channel="discord",
              chat_id="chan-pb-off", sender="u-pb-off",
              text="just a normal message, nothing to do with plugins")
        ack2 = _last_ack_text(outbox, "pb-off-2")
        assert "[fake" in ack2, ack2
        print("PASS: flag off -> /plugin-builder gets a pointer, plain text "
              "still reaches the engine unchanged")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_flag_on_full_interview_via_discord_channel() -> None:
    """Flag on: drive the whole 4-phase interview through process_one() calls
    shaped exactly like a real Discord message sequence, then confirm the
    scaffold + docs actually landed on disk."""
    base, inbox, outbox, _processed, home = _sandbox()
    try:
        _enable_plugin_builder(home)
        adapter = _fresh_adapter(_sandbox_env(base, inbox, outbox, home))
        from plugin_builder import session_store as pb_store
        channel, chat_id, sender = "discord", "chan-pb-on", "u-pb-on"
        key = _session_key(channel, chat_id)
        pb_store.clear("_default", key)

        _send(adapter, inbox, msg_id="pb-1", channel=channel, chat_id=chat_id,
              sender=sender, text="/plugin-builder")
        start_ack = _last_ack_text(outbox, "pb-1")
        assert "plugin-builder" in start_ack.lower()
        assert pb_store.get("_default", key) is not None

        for i, answer in enumerate(POSTGRES_ANSWERS):
            _send(adapter, inbox, msg_id=f"pb-a{i}", channel=channel,
                  chat_id=chat_id, sender=sender, text=answer)
            reply = _last_ack_text(outbox, f"pb-a{i}")
            assert reply  # every question re-prompts or advances, never empty

        final_ack = _last_ack_text(outbox, "pb-a11")
        assert "confirm" in final_ack.lower() or "review" in final_ack.lower()

        _send(adapter, inbox, msg_id="pb-confirm", channel=channel,
              chat_id=chat_id, sender=sender, text="confirm")
        confirm_ack = _last_ack_text(outbox, "pb-confirm")
        assert "written to" in confirm_ack.lower(), confirm_ack
        assert pb_store.get("_default", key) is None  # cleared after DONE

        scaffold_dir = home / "tenants" / "_default" / "plugin-builder" / "community_postgres_connector"
        assert scaffold_dir.is_dir(), list((home / "tenants" / "_default" / "plugin-builder").iterdir())
        assert (scaffold_dir / "docs" / "plugin-idea.md").is_file()

        # Also visible in the Plugin-Builder scaffold index (Console's
        # Settings -> Plugins -> "Scaffolded by Plugin-Builder" reads this).
        from plugin_builder import index_store
        recorded = index_store.list_scaffolds("_default")
        assert any(e["plugin_id"] == "community.postgres-connector" for e in recorded), recorded
        print("PASS: full interview driven through Discord-shaped process_one() calls, "
              "scaffold + index recorded")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_flag_on_cancel_via_process_one() -> None:
    base, inbox, outbox, _processed, home = _sandbox()
    try:
        _enable_plugin_builder(home)
        adapter = _fresh_adapter(_sandbox_env(base, inbox, outbox, home))
        from plugin_builder import session_store as pb_store
        channel, chat_id, sender = "slack", "chan-pb-cancel", "u-pb-cancel"
        key = _session_key(channel, chat_id)
        pb_store.clear("_default", key)

        _send(adapter, inbox, msg_id="pbc-1", channel=channel, chat_id=chat_id,
              sender=sender, text="/plugin-builder")
        assert pb_store.get("_default", key) is not None

        _send(adapter, inbox, msg_id="pbc-2", channel=channel, chat_id=chat_id,
              sender=sender, text="/plugin-builder cancel")
        ack = _last_ack_text(outbox, "pbc-2")
        assert "cancelled" in ack.lower(), ack
        assert pb_store.get("_default", key) is None
        print("PASS: /plugin-builder cancel clears the bridge-side session")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_two_distinct_channels_do_not_share_an_interview() -> None:
    """A Discord interview and a Slack interview with DIFFERENT chat ids for
    the same tenant must never see each other's answers."""
    base, inbox, outbox, _processed, home = _sandbox()
    try:
        _enable_plugin_builder(home)
        adapter = _fresh_adapter(_sandbox_env(base, inbox, outbox, home))
        from plugin_builder import session_store as pb_store
        discord_key = _session_key("discord", "chan-x-discord")
        slack_key = _session_key("slack", "chan-x-slack")
        pb_store.clear("_default", discord_key)
        pb_store.clear("_default", slack_key)

        _send(adapter, inbox, msg_id="x1", channel="discord", chat_id="chan-x-discord",
              sender="ua", text="/plugin-builder")
        _send(adapter, inbox, msg_id="x2", channel="slack", chat_id="chan-x-slack",
              sender="ub", text="/plugin-builder")
        _send(adapter, inbox, msg_id="x3", channel="discord", chat_id="chan-x-discord",
              sender="ua", text="Discord Plugin")

        discord_session = pb_store.get("_default", discord_key)
        slack_session = pb_store.get("_default", slack_key)
        assert discord_session is not None and slack_session is not None
        # Discord answered its first question; Slack must still be waiting on it.
        assert discord_session.current_question().id != "plugin_name"
        assert slack_session.current_question().id == "plugin_name"
        print("PASS: two channels with distinct chat ids keep independent state")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_same_chat_id_on_two_channels_does_not_collide() -> None:
    """Adversarial-review regression: a Discord chat_id and a Telegram
    chat_id that happen to be the IDENTICAL string must not share one
    interview — reproduced end to end before the `f"{channel}:{chat_key}"`
    session-key namespace was added. Bob's plain message on Telegram must
    reach the (faked) engine, never get swallowed as Alice's Discord answer."""
    base, inbox, outbox, _processed, home = _sandbox()
    try:
        _enable_plugin_builder(home)
        adapter = _fresh_adapter(_sandbox_env(base, inbox, outbox, home))
        from plugin_builder import session_store as pb_store
        shared_chat_id = "999888777"  # deliberately identical on both channels
        discord_key = _session_key("discord", shared_chat_id)
        telegram_key = _session_key("telegram", shared_chat_id)
        pb_store.clear("_default", discord_key)
        pb_store.clear("_default", telegram_key)

        _send(adapter, inbox, msg_id="c1", channel="discord", chat_id=shared_chat_id,
              sender="alice", text="/plugin-builder")
        assert pb_store.get("_default", discord_key) is not None
        assert pb_store.get("_default", telegram_key) is None, (
            "starting a Discord interview must not create a Telegram session "
            "for the same raw chat_id"
        )

        _send(adapter, inbox, msg_id="c2", channel="telegram", chat_id=shared_chat_id,
              sender="bob", text="hi there, unrelated message")
        bob_reply = _last_ack_text(outbox, "c2")
        assert "[fake" in bob_reply, (
            f"Bob's message must reach the engine, not Alice's interview: {bob_reply!r}"
        )

        # Alice's interview must be completely unaffected by Bob's message.
        alice_session = pb_store.get("_default", discord_key)
        assert alice_session is not None
        assert alice_session.current_question().id == "plugin_name"
        print("PASS: identical chat_id on two different channels never collide")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_flag_on_but_audio_message_is_not_intercepted() -> None:
    """A transcription-shaped prompt (audio/image/document/video) must never
    be treated as an interview answer, even with an interview active and the
    literal transcript happening to start with the command word."""
    base, inbox, outbox, _processed, home = _sandbox()
    try:
        _enable_plugin_builder(home)
        adapter = _fresh_adapter(_sandbox_env(base, inbox, outbox, home))
        from plugin_builder import session_store as pb_store
        channel, chat_id, sender = "discord", "chan-pb-audio", "u-pb-audio"
        key = _session_key(channel, chat_id)
        pb_store.clear("_default", key)

        _send(adapter, inbox, msg_id="pba-1", channel=channel, chat_id=chat_id,
              sender=sender, text="/plugin-builder")
        assert pb_store.get("_default", key) is not None

        env = {"id": "pba-2", "channel": channel, "from": sender,
               "chat_id": chat_id, "document_path": "/nonexistent/file.txt",
               "document_name": "file.txt", "text": "Ignored Name", "ts": 0}
        f = inbox / "pba-2.json"
        f.write_text(json.dumps(env))
        adapter.process_one(f, settings={"whitelist": [sender]})

        # The interview must NOT have advanced past its first question.
        session = pb_store.get("_default", key)
        assert session is not None
        assert session.current_question().id == "plugin_name"
        print("PASS: a document/audio/image/video turn never feeds an active interview")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_unrelated_slash_command_mid_interview_is_not_read_as_an_answer() -> None:
    """Adversarial-review finding: only NON-slash text may be fed to
    `turn.drive()` as an interview answer — some other (unrecognized or
    future) slash command must fall through, exactly like the console's
    `handle()` excludes all `/`-leading text from `_plugin_builder_continue`.
    The interview must stay parked on the same question, not silently record
    the stray command text as the answer."""
    base, inbox, outbox, _processed, home = _sandbox()
    try:
        _enable_plugin_builder(home)
        adapter = _fresh_adapter(_sandbox_env(base, inbox, outbox, home))
        from plugin_builder import session_store as pb_store
        channel, chat_id, sender = "discord", "chan-pb-stray-cmd", "u-pb-stray"
        key = _session_key(channel, chat_id)
        pb_store.clear("_default", key)

        _send(adapter, inbox, msg_id="s1", channel=channel, chat_id=chat_id,
              sender=sender, text="/plugin-builder")
        assert pb_store.get("_default", key) is not None

        _send(adapter, inbox, msg_id="s2", channel=channel, chat_id=chat_id,
              sender=sender, text="/some-unrecognized-command arg")
        reply = _last_ack_text(outbox, "s2")
        # Falls through to the (faked) engine — never silently consumed.
        assert "[fake" in reply, reply

        session = pb_store.get("_default", key)
        assert session is not None
        assert session.current_question().id == "plugin_name", (
            "a stray slash command must not have been recorded as the "
            "plugin_name answer"
        )
        print("PASS: an unrelated slash command mid-interview falls through, "
              "never becomes an interview answer")
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ── ADR-0262/0263: idea-first + checkpoint + --ideas, reachable from a bridge ──

def test_ideas_flag_off_returns_pointer_and_creates_no_ideation_session() -> None:
    base, inbox, outbox, _processed, home = _sandbox()
    try:
        _enable_plugin_builder(home)  # base flag on, plugin_builder_ideas_mode NOT
        adapter = _fresh_adapter(_sandbox_env(base, inbox, outbox, home))
        from plugin_builder import ideation as pb_ideation
        channel, chat_id, sender = "discord", "chan-pb-ideas-off", "u-pb-ideas-off"
        key = _session_key(channel, chat_id)
        pb_ideation.clear("_default", key)

        _send(adapter, inbox, msg_id="pbi-off-1", channel=channel, chat_id=chat_id,
              sender=sender, text="/plugin-builder --ideas")
        ack = _last_ack_text(outbox, "pbi-off-1")
        assert "off" in ack.lower(), ack
        assert pb_ideation._get("_default", key) is None  # noqa: SLF001
        print("PASS: --ideas flag off -> pointer, no ideation session created")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_ideas_flag_on_full_round_trip_via_discord_channel() -> None:
    """--ideas through a real bridge turn: ack -> accept -> user's own free
    text converges -> hands off into a real idea-first interview session
    (session_store, not ideation's own store) -> drains CONFIRM_GAPS ->
    review -> checkpoint -> scaffold + generated test on disk."""
    base, inbox, outbox, _processed, home = _sandbox()
    try:
        _enable_flags(
            home, "plugin_builder_idea_first_interview",
            "plugin_builder_checkpoint_review", "plugin_builder_generate_e2e_tests",
            "plugin_builder_ideas_mode",
        )
        adapter = _fresh_adapter(_sandbox_env(base, inbox, outbox, home))
        from plugin_builder import ideation as pb_ideation
        from plugin_builder import session_store as pb_store
        channel, chat_id, sender = "discord", "chan-pb-ideas-on", "u-pb-ideas-on"
        key = _session_key(channel, chat_id)
        pb_ideation.clear("_default", key)
        pb_store.clear("_default", key)

        _send(adapter, inbox, msg_id="pbi-1", channel=channel, chat_id=chat_id,
              sender=sender, text="/plugin-builder --ideas")
        assert "?" in _last_ack_text(outbox, "pbi-1")
        assert pb_ideation._get("_default", key) is not None  # noqa: SLF001

        _send(adapter, inbox, msg_id="pbi-2", channel=channel, chat_id=chat_id,
              sender=sender, text="yes")
        assert pb_ideation._get("_default", key) is not None  # noqa: SLF001, still in ROUNDS

        _send(adapter, inbox, msg_id="pbi-3", channel=channel, chat_id=chat_id,
              sender=sender,
              text="Talks to api.example.com, needs an API key, uses `requests`, routes messages.")
        handoff_ack = _last_ack_text(outbox, "pbi-3")
        assert "normal flow" in handoff_ack.lower() or "übernommen" in handoff_ack.lower()
        assert pb_ideation._get("_default", key) is None  # noqa: SLF001 — handed off
        interview = pb_store.get("_default", key)
        assert interview is not None
        assert interview.phase.value == "idea"  # idea_text pre-filled, name still pending

        _send(adapter, inbox, msg_id="pbi-4", channel=channel, chat_id=chat_id,
              sender=sender, text="Bridge Router")
        session = pb_store.get("_default", key)
        while session is not None and session.phase.value == "confirm_gaps":
            _send(adapter, inbox, msg_id=f"pbi-gap-{session._answer_index}",  # noqa: SLF001
                  channel=channel, chat_id=chat_id, sender=sender, text="none")
            session = pb_store.get("_default", key)

        _send(adapter, inbox, msg_id="pbi-review", channel=channel, chat_id=chat_id,
              sender=sender, text="confirm")
        checkpoint_ack = _last_ack_text(outbox, "pbi-review")
        assert "nothing beyond these documents" in checkpoint_ack.lower()

        _send(adapter, inbox, msg_id="pbi-done", channel=channel, chat_id=chat_id,
              sender=sender, text="confirm")
        final_ack = _last_ack_text(outbox, "pbi-done")
        assert "written to" in final_ack.lower(), final_ack
        assert pb_store.get("_default", key) is None

        plugin_builder_dir = home / "tenants" / "_default" / "plugin-builder"
        scaffold_dirs = [p for p in plugin_builder_dir.iterdir() if p.is_dir()]
        assert scaffold_dirs, list(plugin_builder_dir.iterdir())
        assert (scaffold_dirs[0] / "tests").is_dir()
        assert list((scaffold_dirs[0] / "tests").glob("test_*_e2e.py"))
        print("PASS: --ideas -> idea-first interview -> checkpoint -> scaffold+tests, "
              "all driven through real Discord-shaped process_one() calls")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_disabling_flag_mid_ideas_session_stops_it_not_just_plain_interview() -> None:
    """Round-2 regression: `_plugin_builder_bridge_reply` used to check
    `ideation.continue_active()` BEFORE the `plugin_builder_enabled` flag,
    so disabling the flag mid-`--ideas` left the dialogue running (including
    live surface_map lookups) — only the plain-interview branch checked the
    flag. Reproduced live before the fix; this pins the fix."""
    base, inbox, outbox, _processed, home = _sandbox()
    try:
        _enable_flags(home, "plugin_builder_ideas_mode")
        adapter = _fresh_adapter(_sandbox_env(base, inbox, outbox, home))
        from plugin_builder import ideation as pb_ideation
        channel, chat_id, sender = "discord", "chan-pb-ideas-toggle", "u-pb-ideas-toggle"
        key = _session_key(channel, chat_id)
        pb_ideation.clear("_default", key)

        _send(adapter, inbox, msg_id="pbt-1", channel=channel, chat_id=chat_id,
              sender=sender, text="/plugin-builder --ideas")
        assert pb_ideation._get("_default", key) is not None  # noqa: SLF001

        # Disable the base flag mid-dialogue.
        (home / "tenants" / "_default" / "global" / "features.json").write_text(
            json.dumps({"flags": {"plugin_builder_enabled": False}}), encoding="utf-8"
        )

        _send(adapter, inbox, msg_id="pbt-2", channel=channel, chat_id=chat_id,
              sender=sender, text="yes")
        ack = _last_ack_text(outbox, "pbt-2")
        assert "[fake" in ack, (
            f"turn must fall through to the (faked) engine once disabled, not "
            f"keep running the ideation dialogue: {ack!r}"
        )
        assert pb_ideation._get("_default", key) is None, (  # noqa: SLF001
            "the orphaned ideation session must be cleared when the flag "
            "is found off, not left running"
        )
        print("PASS: disabling plugin_builder_enabled mid --ideas stops it, "
              "same as it already did for a plain interview")
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    test_flag_off_plugin_builder_command_gets_pointer_not_engine_call()
    test_flag_on_full_interview_via_discord_channel()
    test_flag_on_cancel_via_process_one()
    test_two_distinct_channels_do_not_share_an_interview()
    test_same_chat_id_on_two_channels_does_not_collide()
    test_flag_on_but_audio_message_is_not_intercepted()
    test_unrelated_slash_command_mid_interview_is_not_read_as_an_answer()
    test_ideas_flag_off_returns_pointer_and_creates_no_ideation_session()
    test_ideas_flag_on_full_round_trip_via_discord_channel()
    test_disabling_flag_mid_ideas_session_stops_it_not_just_plain_interview()
    print("\nALL PASS")
