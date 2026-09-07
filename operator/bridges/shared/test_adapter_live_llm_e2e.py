"""Live LLM E2E for the bridge adapter (adversarial hardening 2026-09-07).

Drives the REAL inbox → adapter → `claude -p` (haiku) → outbox path with no
``ADAPTER_FAKE_CLAUDE`` short-circuit: a genuine envelope file is dropped
into a sandboxed inbox under a temp ``CORVIN_HOME``, ``adapter.process_one``
consumes it exactly like the main loop does, and the test asserts the four
things an operator relies on:

  1. an outbox envelope with a NON-EMPTY reply appears for the same channel/chat
  2. the inbox file was moved to ``processed/`` (not unlinked, not left behind)
  3. the audit chain carries the whole turn, hash-chained:
     ``bridge.message_received`` → ``house_rules.*`` (L44 verdict) →
     ``os_turn.started`` → ``engine.span.start`` → … → ``os_turn.completed``
  4. no user text leaked into the audit records

The EU AI Act Art. 50 disclosure card is a DAEMON responsibility (shown once
per (chat, uid) before the first envelope is written — see
``discord/test_slash_task_gate.js`` and ``email/test_disclosure_ordering.js``);
the adapter path exercised here starts after that gate, so it is not asserted
here.

Opt-in: ``CLAUDE_LIVE_E2E=1`` (uses real API quota, ~10-30 s). Skipped
otherwise so the deterministic suite stays offline.

Run:
    CLAUDE_LIVE_E2E=1 .venv/bin/python -m pytest -q -o addopts="" \\
        -p no:cacheprovider operator/bridges/shared/test_adapter_live_llm_e2e.py
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

pytestmark = pytest.mark.live

_LIVE = os.environ.get("CLAUDE_LIVE_E2E") == "1"


def _audit_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


@pytest.mark.skipif(not _LIVE, reason="set CLAUDE_LIVE_E2E=1 to run the real claude -p turn")
@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not on PATH")
def test_real_inbox_turn_reaches_outbox_with_disclosure_and_audit(tmp_path: Path) -> None:
    home = tmp_path / "corvin_home"
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    processed = tmp_path / "processed"
    bridges = tmp_path / "bridges"
    for d in (home, inbox, outbox, processed, bridges / "discord"):
        d.mkdir(parents=True)
    audit_path = tmp_path / "audit.jsonl"
    # Minimal channel settings: the sender is whitelisted → owner path.
    (bridges / "discord" / "settings.json").write_text(json.dumps({
        "whitelist": ["u-live-e2e"],
    }), encoding="utf-8")
    shared_settings = tmp_path / "settings.json"
    shared_settings.write_text(json.dumps({
        "always_voice": False,
        "voice_summary_mode": "never",
    }), encoding="utf-8")

    env_overrides = {
        "CORVIN_HOME": str(home),
        "CORVIN_TENANT_ID": "_default",
        "ADAPTER_INBOX": str(inbox),
        "ADAPTER_OUTBOX": str(outbox),
        "ADAPTER_PROCESSED": str(processed),
        "ADAPTER_SETTINGS": str(shared_settings),
        "ADAPTER_BRIDGES_DIR": str(bridges),
        "VOICE_AUDIT_PATH": str(audit_path),
        "ADAPTER_ROUTING_MODE": "off",
        "ADAPTER_DISABLE_VOICE": "1",
        "BRIDGE_PROGRESS_UPDATES": "0",
        "CORVIN_VOICE_PREWARM": "0",
        # A cheap, fast model for the real turn.
        "CORVIN_OS_MODEL": "claude-haiku-4-5",
        "ADAPTER_MODEL": "claude-haiku-4-5",
    }
    saved = {k: os.environ.get(k) for k in list(env_overrides) + ["ADAPTER_FAKE_CLAUDE"]}
    os.environ.pop("ADAPTER_FAKE_CLAUDE", None)  # the whole point: a REAL turn
    for k, v in env_overrides.items():
        os.environ[k] = v
    try:
        sys.modules.pop("adapter", None)
        adapter = importlib.import_module("adapter")

        msg_id = "live-e2e-1"
        envelope = {
            "id": msg_id,
            "channel": "discord",
            "from": "u-live-e2e",
            "chat_id": "chat-live-e2e",
            "text": "Reply with exactly the single word PONG and nothing else.",
            "ts": time.time(),
        }
        in_file = inbox / f"{msg_id}.json"
        in_file.write_text(json.dumps(envelope), encoding="utf-8")

        t0 = time.time()
        adapter.process_one(in_file, settings=json.loads(shared_settings.read_text()))
        elapsed = time.time() - t0

        # 2. inbox file moved to processed (never unlinked / never left behind)
        assert not in_file.exists(), "inbox envelope still in inbox after the turn"
        assert (processed / in_file.name).exists(), "inbox envelope was not archived to processed/"

        # 1. outbox envelope with a non-empty reply for the same chat
        out_files = sorted(outbox.glob("*.json"))
        assert out_files, f"no outbox envelope written (turn took {elapsed:.1f}s)"
        payloads = [json.loads(p.read_text(encoding="utf-8")) for p in out_files]
        replies = [p for p in payloads
                   if p.get("channel") == "discord"
                   and str(p.get("chat_id")) == "chat-live-e2e"
                   and not p.get("_progress") and not p.get("_heartbeat")
                   and (p.get("text") or "").strip()]
        assert replies, f"no non-empty reply envelope among {[p.get('text') for p in payloads]!r}"
        assert any("pong" in (p.get("text") or "").lower() for p in replies), (
            f"model reply did not contain PONG: {[p.get('text') for p in replies]!r}")

        # 3. audit chain carries the whole turn, hash-chained
        events = _audit_lines(audit_path)
        types = [e.get("event_type") for e in events]
        assert "bridge.message_received" in types, f"audit types={types!r}"
        assert any(t and t.startswith("house_rules.") for t in types), (
            f"L44 house-rules verdict missing from the chain: {types!r}")
        for needed in ("os_turn.started", "engine.span.start", "os_turn.completed"):
            assert needed in types, f"{needed} missing; audit types={types!r}"
        assert types.index("bridge.message_received") < types.index("os_turn.started") \
            < types.index("os_turn.completed"), f"turn events out of order: {types!r}"
        assert all(isinstance(e.get("hash"), str) and e.get("hash") for e in events), (
            "audit records not hash-chained")
        for prev, cur in zip(events, events[1:]):
            assert cur.get("prev_hash") == prev.get("hash"), "hash chain link broken"

        # 4. no user text in the chain (metadata-only floor)
        raw_chain = audit_path.read_text(encoding="utf-8")
        assert "exactly the single word PONG" not in raw_chain, "prompt text leaked into audit"
        assert "u-live-e2e" not in raw_chain, "raw platform uid leaked into audit (PII floor)"
        print(f"LIVE E2E OK: {len(replies)} reply envelope(s), {len(events)} audit events, {elapsed:.1f}s")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        sys.modules.pop("adapter", None)
