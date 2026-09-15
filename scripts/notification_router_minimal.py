#!/usr/bin/env python3
"""Minimal NotificationRouter daemon for Phase 2 testing (ADR-0655).

This is a lightweight version without numpy/scipy dependencies for testing.
In production, use the full NotificationRouter from core.notification.

Monitors CompletionEvents and delivers to Discord outbox.
"""

import json
import sys
import time
import asyncio
from pathlib import Path
from datetime import datetime, timezone


async def monitor_completion_events(corvin_home: str = None, poll_interval: float = 5.0):
    """Monitor for CompletionEvents and deliver to Discord."""
    if not corvin_home:
        corvin_home = str(Path.home() / ".corvin")

    completion_dir = Path(corvin_home) / "completion_events"
    outbox_dir = Path(corvin_home) / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    completion_dir.mkdir(parents=True, exist_ok=True)

    print(f"🔍 Monitoring {completion_dir} for CompletionEvents...")
    print(f"📤 Discord outbox: {outbox_dir}")

    processed = set()
    while True:
        try:
            # Find new completion events
            if completion_dir.exists():
                for event_file in completion_dir.glob("*.json"):
                    if event_file.name in processed:
                        continue

                    try:
                        with open(event_file) as f:
                            event = json.load(f)

                        task_id = event.get("task_id")
                        print(f"📨 Processing CompletionEvent: {task_id}")

                        # Create Discord message in outbox
                        message = {
                            "task_id": task_id,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "event_type": "task_completed",
                            "task_name": event.get("task_name", "Unknown Task"),
                            "status": event.get("status", "completed"),
                            "voice_path": event.get("voice_path"),
                            "summary": event.get("summary", "Task completed successfully"),
                        }

                        # Write to outbox (Discord bridge will pickup)
                        msg_file = outbox_dir / f"msg_{task_id}_{int(time.time())}.json"
                        with open(msg_file, "w") as f:
                            json.dump(message, f, indent=2)

                        print(f"✅ Discord message written: {msg_file.name}")
                        processed.add(event_file.name)

                    except Exception as e:
                        print(f"⚠️  Failed to process {event_file.name}: {e}")

            await asyncio.sleep(poll_interval)

        except KeyboardInterrupt:
            print("\n🛑 NotificationRouter stopped")
            break
        except Exception as e:
            print(f"❌ Error in monitor_completion_events: {e}")
            await asyncio.sleep(poll_interval)


if __name__ == "__main__":
    corvin_home = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(monitor_completion_events(corvin_home=corvin_home))
