#!/usr/bin/env python3
"""End-to-end test for the parallel-dispatch refactor of adapter.py.

Spawns the real adapter as a subprocess pointed at sandbox dirs, drops
inbox files spanning multiple (channel, chat) keys, then checks:

  1. All outbox files arrive well below the sequential floor.
  2. Within the same chat key, outbox-mtime order matches inbox order.

ADAPTER_POLL_INTERVAL is dialed down so the polling cadence doesn't
dominate walltime.

Run:
    python3 operator/bridges/shared/test_adapter_parallel.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SANDBOX   = Path(os.environ.get("ADAPTER_TEST_SANDBOX", "/tmp/adapter-test-sandbox"))
INBOX     = SANDBOX / "inbox"
OUTBOX    = SANDBOX / "outbox"
PROCESSED = SANDBOX / "processed"
LOG_FILE  = SANDBOX / "adapter.log"

# adapter.py is the sibling file in this directory.
ADAPTER = Path(__file__).resolve().parent / "adapter.py"

# 5 items: 2 in discord:chatA, 2 in discord:chatB, 1 in whatsapp:chatX.
ITEMS = [
    ("01_discordA_first",  "discord",  "chatA"),
    ("02_discordB_first",  "discord",  "chatB"),
    ("03_whatsappX",       "whatsapp", "chatX"),
    ("04_discordA_second", "discord",  "chatA"),
    ("05_discordB_second", "discord",  "chatB"),
]

# 1.0s (not 0.5s): a larger per-item delay raises the signal/jitter ratio so
# the fixed overhead (subprocess startup, poll cadence, thread scheduling) is a
# small fraction of elapsed. With 0.5s the overhead dominated and the test
# flaked under machine load — the fix is a better ratio, not a looser margin.
FAKE_DELAY    = 1.0
MAX_PARALLEL  = 4
POLL_INTERVAL = 0.1
SEQ_FLOOR     = len(ITEMS) * FAKE_DELAY        # 5.0 s
# 15s deadline gibt der Voice-Synthese (OpenAI TTS oder lokaler Fallback)
# genug Spielraum, without dass die parallelism-Aussage verwässert wed:
# der parallelism-Check rechnet weiter mit elapsed bei step 1.
WAIT_TIMEOUT  = SEQ_FLOOR + 15.0


def reset_dirs() -> None:
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)
    for d in (INBOX, OUTBOX, PROCESSED):
        d.mkdir(parents=True, exist_ok=True)


def write_inbox(item_id: str, channel: str, chat: str) -> None:
    payload = {
        "id":      item_id,
        "channel": channel,
        "chat_id": chat,
        "from":    f"sender-{chat}",
        "text":    f"hello from {item_id}",
    }
    (INBOX / f"{item_id}.json").write_text(json.dumps(payload))


def wait_for_processed(n_expected: int, timeout: float) -> list[Path]:
    """Warte bis n_expected Inbox-Files in PROCESSED/ gelandet sind. Das ist
    der saubere Indikator for "Item complete verarbeitet" — Outbox-Files
    sind variabel (Text + Voice + Status + Heartbeat), processed/ ist 1:1."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        files = list(PROCESSED.glob("*.json"))
        if len(files) >= n_expected:
            return files
        time.sleep(0.05)
    return list(PROCESSED.glob("*.json"))


def main() -> int:
    reset_dirs()

    env = os.environ.copy()
    env["ADAPTER_INBOX"]         = str(INBOX)
    env["ADAPTER_OUTBOX"]        = str(OUTBOX)
    env["ADAPTER_PROCESSED"]     = str(PROCESSED)
    # Isolate from the LIVE deployment config — a real operator's
    # bridges/discord/settings.json whitelist otherwise leaks in and this
    # test's discord senders get SPG-dropped as private (same contamination
    # class ADAPTER_BRIDGES_DIR was introduced for in test_adapter_btw).
    settings = SANDBOX / "settings.json"
    settings.write_text("{}")
    env["ADAPTER_SETTINGS"]      = str(settings)
    env["ADAPTER_BRIDGES_DIR"]   = str(SANDBOX / "bridges")
    env["ADAPTER_FAKE_CLAUDE"]   = "1"
    env["ADAPTER_FAKE_DELAY"]    = str(FAKE_DELAY)
    env["ADAPTER_MAX_PARALLEL"]  = str(MAX_PARALLEL)
    env["ADAPTER_POLL_INTERVAL"] = str(POLL_INTERVAL)
    # parallelism-Test soll nicht von echter TTS-Latenz dependent sein.
    env["ADAPTER_DISABLE_VOICE"] = "1"
    # Auto-Routing aus: dieser Test misst per-chat-sequential vs cross-chat-
    # parallel without LLM-Router-Latenz dazwischen.
    env["ADAPTER_ROUTING_MODE"]  = "off"

    proc = subprocess.Popen(
        ["python3", str(ADAPTER)],
        env=env,
        stdout=open(LOG_FILE, "w"),
        stderr=subprocess.STDOUT,
    )
    try:
        time.sleep(0.4)   # let the adapter start its event loop

        t0 = time.monotonic()
        for item_id, channel, chat in ITEMS:
            write_inbox(item_id, channel, chat)
        processed_files = wait_for_processed(len(ITEMS), WAIT_TIMEOUT)
        # Outbox files (text + voice + status) to the Auswertung weiter unten;
        # echte completion-Indikation kommt aus processed/.
        files = list(OUTBOX.glob("*.json"))
        elapsed = time.monotonic() - t0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    # 90% headroom — best case (parallel) is 2.0s vs 5.0s sequential floor, so
    # a target of 4.5s leaves margin for subprocess startup + poll latency
    # + thread scheduling jitter when the full test suite runs concurrently,
    # while still firmly proving parallelism occurred (4.5s << 5.0s sequential).
    target = SEQ_FLOOR * 0.9
    print(f"\n[result] {len(processed_files)}/{len(ITEMS)} processed in {elapsed:.2f}s "
          f"({len(files)} outbox files; seq floor {SEQ_FLOOR:.2f}s, "
          f"parallel target <={target:.2f}s)\n")

    if len(processed_files) != len(ITEMS):
        print(f"FAIL: expected {len(ITEMS)} items processed, got {len(processed_files)}")
        print(f"adapter log: {LOG_FILE}")
        return 1

    # --- 1. Wall-clock parallelism --------------------------------------
    # 5 items, 4 workers, 1.0s each. Two chats hold 2 items, one chat 1.
    # Best case: 2 batches = 2.0s. Allow up to 90% of seq floor (4.5s)
    # to absorb subprocess startup + poll latency + thread scheduling
    # + full-suite concurrent load jitter.
    if elapsed >= target:
        print(f"FAIL: elapsed {elapsed:.2f}s too close to sequential floor "
              f"{SEQ_FLOOR:.2f}s — parallelism didn't kick in")
        print(f"--- adapter log ---")
        print(LOG_FILE.read_text())
        return 1
    print(f"PASS: elapsed {elapsed:.2f}s < {target:.2f}s "
          f"(beats sequential floor by {(1 - elapsed/SEQ_FLOOR)*100:.0f}%)")

    # --- 2. Per-chat ordering -------------------------------------------
    # processed/ enthält die ursprünglichen Inbox-Files unchanged
    # (process_one verschiebt sie am Ende dorthin). Das ist die saubere
    # Referenz for den Verarbeitungs-Zeitpunkt — Outbox-Files can vorab
    # geschrieben werden (Heartbeats, Status), processed/ erst am Ende.
    by_chat: dict[str, list[tuple[float, str]]] = {}
    for f in processed_files:
        base = f.stem
        meta = next((it for it in ITEMS if it[0] == base), None)
        if meta is None:
            print(f"FAIL: processed file {f.name} doesn't map to inbox")
            return 1
        chat_key = f"{meta[1]}:{meta[2]}"
        by_chat.setdefault(chat_key, []).append((f.stat().st_mtime, base))

    for chat_key, entries in by_chat.items():
        sorted_by_mtime = [b for _, b in sorted(entries)]
        sorted_by_id    = sorted(b for _, b in entries)
        if sorted_by_mtime != sorted_by_id:
            print(f"FAIL: chat {chat_key} processed out of order")
            print(f"  by mtime: {sorted_by_mtime}")
            print(f"  by id:    {sorted_by_id}")
            return 1
        print(f"PASS: chat {chat_key} kept order: {sorted_by_id}")

    # --- 3. Adapter log shows fake-claude calls covering all items ------
    # Default-path ist call_claude_streaming → loggt [fake-stream] sleep;
    # Legacy-path call_claude → [fake] sleep. Beide zählen.
    log_text = LOG_FILE.read_text()
    fakes = [ln for ln in log_text.splitlines()
             if "[fake] sleep" in ln or "[fake-stream] sleep" in ln]
    if len(fakes) != len(ITEMS):
        print(f"FAIL: expected {len(ITEMS)} fake calls, log shows {len(fakes)}")
        print(log_text)
        return 1
    print(f"PASS: adapter logged all {len(fakes)} fake-claude calls")

    print("\nALL CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
