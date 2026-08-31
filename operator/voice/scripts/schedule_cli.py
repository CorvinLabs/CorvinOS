#!/usr/bin/env python3
"""schedule_cli.py — small CLI in front of scheduler.py for the in-chat
`/schedule` commands. The Node daemons shell out to this; output is plain
text suitable as a chat reply.

Sub-commands:
    list <channel> <chat_id>
    add  <channel> <chat_id> <sender> <when_spec>::<text>          [persona]
    rm   <task_id>

`when_spec` follows scheduler.parse_when (ISO 8601, "in 5m" / "in 2h" / "in 3d",
or a 5-field cron expression). The `::` separator avoids quoting headaches when
the user types e.g. `/schedule add tomorrow 8am :: standup reminder`.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent.parent / "bridges" / "shared"
sys.path.insert(0, str(SHARED))

import scheduler  # noqa: E402


def cmd_list(args: list[str]) -> int:
    channel = args[0] if args else None
    chat_id = args[1] if len(args) > 1 else None
    items = scheduler.list_tasks(channel=channel, chat_id=chat_id)
    if not items:
        print("No scheduled tasks for this chat.")
        return 0
    print(f"{len(items)} scheduled task(s):")
    for it in sorted(items, key=lambda i: i.get("next_run", 0)):
        print("  " + scheduler.humanize(it))
    return 0


def cmd_add(args: list[str]) -> int:
    if len(args) < 4:
        print("Usage: schedule_cli.py add <channel> <chat_id> <sender> <when>::<text> [persona]")
        return 2
    channel, chat_id, sender = args[0], args[1], args[2]
    body = " ".join(args[3:])
    persona: str | None = None
    # Optional persona at the very end: " --persona <name>"
    if " --persona " in body:
        body, persona = body.rsplit(" --persona ", 1)
        persona = persona.strip()
    if "::" not in body:
        print("Error: need a `::` separator between the time spec and the message.")
        print('Example: add discord 123 456 "in 1h::stand-up reminder"')
        return 2
    when_spec, text = body.split("::", 1)
    when_spec = when_spec.strip()
    text = text.strip()
    if not text:
        print("Error: empty message text.")
        return 2
    try:
        item = scheduler.add_task(
            channel=channel, chat_id=chat_id, sender=sender,
            text=text, when=when_spec, persona=persona,
        )
    except ValueError as e:
        print(f"Error: {e}")
        return 1
    print("Scheduled.")
    print("  " + scheduler.humanize(item))
    return 0


def cmd_rm(args: list[str]) -> int:
    if not args:
        print("Usage: schedule_cli.py rm <task_id>")
        return 2
    if scheduler.remove_task(args[0]):
        print(f"Removed task {args[0]}.")
        return 0
    print(f"No task with id {args[0]}.")
    return 1


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: schedule_cli.py {list|add|rm} ...")
        return 2
    sub, rest = argv[1], argv[2:]
    if sub == "list":
        return cmd_list(rest)
    if sub == "add":
        return cmd_add(rest)
    if sub == "rm":
        return cmd_rm(rest)
    print(f"Unknown sub-command: {sub}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
