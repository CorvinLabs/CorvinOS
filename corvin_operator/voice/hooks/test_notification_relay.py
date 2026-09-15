#!/usr/bin/env python3
"""Test für notification_relay.py.

Prüft alle Pfade ohne Claude Code zu starten:
  1. Kein relay.json → No-Op (exit 0, kein Outbox-Write)
  2. enabled=false → No-Op
  3. Notification-Event mit message → Outbox-Envelope
  4. SessionStart startup → Outbox-Envelope mit cwd
  5. SessionStart resume → kein Forward
  6. Event nicht in events-Filter → kein Forward

Run:
    python3 operator/voice/hooks/test_notification_relay.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT     = Path(__file__).resolve().parent
RELAY    = ROOT / "notification_relay.py"
# The REAL outbox the messenger daemons poll (operator/bridges/shared/outbox).
# This is the regression anchor for the orphan-path bug: the relay MUST target
# this, not operator/voice/bridges/shared/outbox.
REAL_OUTBOX = ROOT.parent.parent / "bridges" / "shared" / "outbox"
# Each run points the relay at a temp dir via ADAPTER_OUTBOX so the test asserts
# against a controlled directory without polluting the repo tree — and, crucially,
# proves the relay honours the same override the daemons/adapter use.
OUTBOX: Path = REAL_OUTBOX  # rebound per-run in main()


def run_hook(payload: dict, config: dict | None,
             config_dir: Path) -> tuple[int, list[Path]]:
    """Ruft das Skript auf, liefert (exitcode, list_of_relay_files)."""
    config_dir.mkdir(parents=True, exist_ok=True)
    relay_cfg = config_dir / "relay.json"
    if config is None:
        relay_cfg.unlink(missing_ok=True)
    else:
        relay_cfg.write_text(json.dumps(config))

    OUTBOX.mkdir(parents=True, exist_ok=True)
    # Outbox vor dem Lauf säubern (nur unsere relay_*-Files).
    pre = {p.name for p in OUTBOX.glob("relay_*.json")}

    env = os.environ.copy()
    env["VOICE_CONFIG_DIR"] = str(config_dir)
    env["ADAPTER_OUTBOX"] = str(OUTBOX)
    res = subprocess.run(
        ["python3", str(RELAY)],
        input=json.dumps(payload), text=True, capture_output=True, env=env,
    )
    new_files = [p for p in OUTBOX.glob("relay_*.json") if p.name not in pre]
    return res.returncode, new_files


def cleanup(files: list[Path]) -> None:
    for f in files:
        f.unlink(missing_ok=True)


def main() -> int:
    global OUTBOX
    failures: list[str] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        OUTBOX = tmp / "real_outbox"  # controlled dir, injected via ADAPTER_OUTBOX
        OUTBOX.mkdir(parents=True, exist_ok=True)

        # --- 0. REGRESSION: default outbox path = the daemon-polled dir ---
        # Guards the orphan-path bug: with no override, the relay must resolve
        # to operator/bridges/shared/outbox, NOT operator/voice/bridges/...
        probe = subprocess.run(
            ["python3", "-c",
             "import os,importlib.util as u;"
             "os.environ.pop('ADAPTER_OUTBOX',None);"
             f"s=u.spec_from_file_location('nr',r'{RELAY}');"
             "m=u.module_from_spec(s);s.loader.exec_module(m);"
             "print(m._shared_outbox())"],
            capture_output=True, text=True,
            env={k: v for k, v in os.environ.items() if k != "ADAPTER_OUTBOX"},
        )
        default_path = Path(probe.stdout.strip())
        if default_path != REAL_OUTBOX:
            failures.append(
                f"default outbox path regressed: {default_path} != {REAL_OUTBOX}")
        elif "voice/bridges" in str(default_path):
            failures.append(f"default outbox still orphan: {default_path}")
        else:
            print(f"PASS: default outbox = daemon-polled dir ({default_path.name}/)")

        # --- 1. Kein relay.json → No-Op ----------------------------------
        rc, files = run_hook(
            {"hook_event_name": "Notification", "message": "ignore me"},
            config=None, config_dir=tmp / "noconfig",
        )
        if rc != 0 or files:
            failures.append(f"no-config: rc={rc} files={files}")
        else:
            print("PASS: no relay.json → no forward, exit 0")
        cleanup(files)

        # --- 2. enabled=false → No-Op ------------------------------------
        rc, files = run_hook(
            {"hook_event_name": "Notification", "message": "still ignore"},
            config={"enabled": False, "channel": "telegram", "to": "1"},
            config_dir=tmp / "disabled",
        )
        if rc != 0 or files:
            failures.append(f"disabled: rc={rc} files={files}")
        else:
            print("PASS: enabled=false → no forward")
        cleanup(files)

        # --- 3. Notification mit message → Outbox-Envelope ---------------
        rc, files = run_hook(
            {"hook_event_name": "Notification",
             "message": "Tool needs approval: Bash"},
            config={"enabled": True, "channel": "telegram", "to": "999",
                    "events": ["Notification"], "prefix": "🔔"},
            config_dir=tmp / "notify",
        )
        if rc != 0 or len(files) != 1:
            failures.append(f"notification: rc={rc} files={files}")
        else:
            env = json.loads(files[0].read_text())
            if (env.get("channel") != "telegram"
                    or env.get("chat_id") != "999"
                    or "Tool needs approval" not in env.get("text", "")
                    or not env["text"].startswith("🔔")):
                failures.append(f"notification envelope shape: {env}")
            else:
                print("PASS: Notification → telegram envelope with prefix + chat_id")
        cleanup(files)

        # --- 4. SessionStart startup → Forward ---------------------------
        rc, files = run_hook(
            {"hook_event_name": "SessionStart", "source": "startup",
             "cwd": "/home/foo/myproject"},
            config={"enabled": True, "channel": "discord", "to": "12345",
                    "events": ["Notification", "SessionStart"]},
            config_dir=tmp / "session",
        )
        if rc != 0 or len(files) != 1:
            failures.append(f"sessionstart: rc={rc} files={files}")
        else:
            env = json.loads(files[0].read_text())
            if "myproject" not in env.get("text", ""):
                failures.append(f"sessionstart text missing cwd: {env}")
            else:
                print("PASS: SessionStart startup → discord envelope with cwd")
        cleanup(files)

        # --- 5. SessionStart resume → kein Forward -----------------------
        rc, files = run_hook(
            {"hook_event_name": "SessionStart", "source": "resume",
             "cwd": "/home/foo/myproject"},
            config={"enabled": True, "channel": "discord", "to": "12345",
                    "events": ["Notification", "SessionStart"]},
            config_dir=tmp / "resume",
        )
        if rc != 0 or files:
            failures.append(f"resume: rc={rc} files={files}")
        else:
            print("PASS: SessionStart resume → no forward (only startup)")
        cleanup(files)

        # --- 6. Event nicht im Filter → kein Forward ---------------------
        rc, files = run_hook(
            {"hook_event_name": "Stop", "message": "should not pass"},
            config={"enabled": True, "channel": "telegram", "to": "1",
                    "events": ["Notification"]},
            config_dir=tmp / "filter",
        )
        if rc != 0 or files:
            failures.append(f"event-filter: rc={rc} files={files}")
        else:
            print("PASS: events-filter blocks Stop event")
        cleanup(files)

        # --- 7. Slack with explicit chat_id → envelope carries chat_id ----
        # Slack/Signal daemons route on chat_id; the old code only set it for
        # telegram/discord, so slack relays were dropped. Also proves an
        # explicit config chat_id (≠ to) is honoured, not overwritten by `to`.
        rc, files = run_hook(
            {"hook_event_name": "Notification", "message": "task done"},
            config={"enabled": True, "channel": "slack", "to": "UUSER123",
                    "chat_id": "C0CHANNEL9", "events": ["Notification"]},
            config_dir=tmp / "slack",
        )
        if rc != 0 or len(files) != 1:
            failures.append(f"slack: rc={rc} files={files}")
        else:
            env = json.loads(files[0].read_text())
            if env.get("channel") != "slack" or env.get("chat_id") != "C0CHANNEL9":
                failures.append(f"slack chat_id not honoured: {env}")
            else:
                print("PASS: slack → envelope carries explicit chat_id")
        cleanup(files)

    if failures:
        print(f"\n{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nALL CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
