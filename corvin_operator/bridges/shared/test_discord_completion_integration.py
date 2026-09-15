#!/usr/bin/env python3
"""test_discord_completion_integration.py — Verify Discord daemon picks up completions.

Tests that the outbox envelope produced by bg_task_worker's deliver_ready()
is in the correct format for the Discord daemon to process.

This is a focused integration test (not end-to-end with the real Discord bot,
which requires network/auth). It validates the envelope shape that the
Discord daemon's startOutboxPoller() expects.

Run: python3 operator/bridges/shared/test_discord_completion_integration.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    """Test Discord envelope format."""
    with tempfile.TemporaryDirectory() as td:
        home = Path(td) / "home"
        home.mkdir()
        corvin_home = home / ".corvin"
        corvin_home.mkdir()
        outbox = corvin_home / "shared" / "outbox"
        outbox.mkdir(parents=True)

        os.environ["CORVIN_HOME"] = str(corvin_home)
        os.environ["ADAPTER_OUTBOX"] = str(outbox)

        sys.path.insert(0, str(HERE))
        if "completion_notify" in sys.modules:
            del sys.modules["completion_notify"]
        import completion_notify as cn  # type: ignore

        print("=" * 70)
        print("DISCORD COMPLETION INTEGRATION TEST")
        print("=" * 70)

        # Create a Discord background task completion
        print("\n[Setup] Register Discord task...")
        task_id = cn.register(
            channel="discord",
            chat_id="987654321",  # Discord channel ID
            sender="user_abc",
            label="test_task"
        )
        print(f"  ✓ Registered: {task_id}")

        print("\n[Execution] Mark task done...")
        cn.mark_done(task_id, text="✅ Background task completed successfully!", ok=True)
        print(f"  ✓ Marked done")

        print("\n[Delivery] Deliver to outbox...")
        sent = cn.deliver_ready(outbox)
        print(f"  ✓ Delivered: {sent} envelope(s)")

        if sent != 1:
            print(f"  ✗ FAIL: expected 1 envelope, got {sent}")
            return 1

        # Load and validate the envelope
        print("\n[Validation] Check envelope format...")
        env_file = next(outbox.glob("cn_*.json"))
        envelope = json.loads(env_file.read_text())

        # Required fields for Discord daemon's processOutboxPayload
        # Discord uses chat_id for channel routing, text for message body
        required_fields = ["channel", "chat_id", "text"]
        for field in required_fields:
            if field not in envelope:
                print(f"  ✗ FAIL: missing required field '{field}'")
                return 1
            print(f"  ✓ Has '{field}': {envelope[field]!r}")

        # Validate Discord-specific fields
        if envelope["channel"] != "discord":
            print(f"  ✗ FAIL: channel is '{envelope['channel']}', expected 'discord'")
            return 1
        print(f"  ✓ Channel correct: discord")

        if envelope.get("chat_id") and envelope["chat_id"] != "987654321":
            print(f"  ✗ FAIL: chat_id mismatch")
            return 1
        print(f"  ✓ Chat ID correct: {envelope.get('chat_id')}")

        if not envelope.get("text"):
            print(f"  ✗ FAIL: text is empty")
            return 1
        print(f"  ✓ Text present: {len(envelope['text'])} chars")

        # Optional but good-to-have fields
        optional_fields = ["msg_id", "to"]
        for field in optional_fields:
            if field in envelope:
                print(f"  ✓ Has optional '{field}'")

        print("\n" + "=" * 70)
        print("✅ DISCORD COMPLETION INTEGRATION TEST PASSED")
        print("=" * 70)
        print("\nEnvelope ready for Discord daemon:")
        print(json.dumps(envelope, indent=2, ensure_ascii=False)[:500] + "...")
        return 0


if __name__ == "__main__":
    sys.exit(main())
