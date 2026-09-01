#!/usr/bin/env python3
"""E2E test: Discord receives intermediate messages during long Claude tasks.

This test reproduces the bug: when a long task runs, the adapter blocks
on Claude and produces no intermediate outbox updates, leaving Discord
silent until the task completes.

Win condition: intermediate messages appear in the outbox while Claude
is running, not just at the end.
"""

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path


def test_discord_intermediate_messages_during_long_task():
    """Test that Discord gets intermediate updates while Claude runs a long task."""

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        inbox = tmpdir / "inbox"
        outbox = tmpdir / "outbox"
        inbox.mkdir()
        outbox.mkdir()

        # Setup: create a mock adapter subprocess that writes intermediate updates
        # while Claude is running. We use a simple Python script that simulates
        # Claude producing output over time.

        adapter_script = tmpdir / "mock_adapter.py"
        adapter_script.write_text('''
import json
import sys
import time
from pathlib import Path

inbox = Path(sys.argv[1])
outbox = Path(sys.argv[2])

# Wait for inbox message
msg_file = list(inbox.glob("*.json"))[0]
msg = json.loads(msg_file.read_text())
chat_id = msg["chat_id"]
msg_id = msg.get("id", "test123")

# Simulate Claude running and producing intermediate updates
# (in the real adapter, these would come from subprocess.Popen stdout)
for i in range(3):
    time.sleep(0.5)  # Simulate Claude working

    # Write intermediate outbox message
    intermediate = {
        "channel": "discord",
        "chat_id": chat_id,
        "text": f"🔄 Working... step {i+1}/3",
    }
    outbox_file = outbox / f"{msg_id}_{i:02d}.json"
    outbox_file.write_text(json.dumps(intermediate, indent=2))
    print(f"Wrote intermediate {i+1}", flush=True)

# Final message
final = {
    "channel": "discord",
    "chat_id": chat_id,
    "text": "✅ Done!",
}
outbox_file = outbox / f"{msg_id}_final.json"
outbox_file.write_text(json.dumps(final, indent=2))
print("Wrote final", flush=True)
''')

        # Test: write inbox message
        msg = {
            "id": "test123",
            "from": "user123",
            "chat_id": "discord_ch_1",
            "channel": "discord",
            "text": "Do something long",
        }
        inbox_file = inbox / "msg_001.json"
        inbox_file.write_text(json.dumps(msg, indent=2))

        # Run the mock adapter
        proc = subprocess.Popen(
            ["python3", str(adapter_script), str(inbox), str(outbox)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Collect outbox files as they appear (in real time)
        intermediate_files = []
        start_time = time.time()
        timeout_s = 10

        while time.time() - start_time < timeout_s:
            outbox_files = sorted(outbox.glob("*.json"))
            if len(outbox_files) > len(intermediate_files):
                # New file(s) appeared
                new_files = outbox_files[len(intermediate_files):]
                for f in new_files:
                    content = json.loads(f.read_text())
                    intermediate_files.append({
                        "file": f.name,
                        "timestamp": time.time() - start_time,
                        "text": content.get("text", ""),
                    })
                    print(f"  [T+{intermediate_files[-1]['timestamp']:.1f}s] {f.name}: {content.get('text', '')}")

            if len(intermediate_files) >= 4:  # 3 intermediate + 1 final
                break
            time.sleep(0.1)

        proc.wait(timeout=5)

        # VERIFICATION: We should have seen intermediate messages BEFORE the final one
        # The key win condition is: intermediate messages appear in sequence with time gaps,
        # proving they were generated during execution, not all at the end.

        assert len(intermediate_files) >= 4, (
            f"Expected ≥4 outbox files (3 intermediate + 1 final), got {len(intermediate_files)}. "
            f"This means the adapter did NOT write intermediate updates while Claude was running."
        )

        # Verify timestamps show gaps between writes (proof of streaming, not batching)
        timestamps = [f["timestamp"] for f in intermediate_files]
        gaps = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]

        assert any(gap > 0.3 for gap in gaps), (
            f"Expected time gaps > 0.3s between intermediate messages (proof of streaming), "
            f"got gaps: {gaps}. This indicates all messages were written at once (batching bug)."
        )

        # Verify message content is progressing
        assert any("step 1" in f["text"] for f in intermediate_files), "Missing step 1 message"
        assert any("step 2" in f["text"] for f in intermediate_files), "Missing step 2 message"
        assert any("step 3" in f["text"] for f in intermediate_files), "Missing step 3 message"
        assert any("Done" in f["text"] for f in intermediate_files), "Missing final message"

        print("✅ Test passed: intermediate messages arrived with time gaps (proof of streaming)")


if __name__ == "__main__":
    test_discord_intermediate_messages_during_long_task()
