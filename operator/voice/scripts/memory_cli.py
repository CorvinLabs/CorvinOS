#!/usr/bin/env python3
"""memory_cli.py — CLI in front of memory.py for the in-chat `/memory`
commands. Sub-commands:

    list                  show all topics with one-line summaries
    show <topic>          print a topic's full body
    write <topic> <text>  create/overwrite a topic with the text
    append <topic> <text> append the text to an existing topic
    forget <topic>        delete a topic
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent.parent / "bridges" / "shared"
sys.path.insert(0, str(SHARED))

import memory as mem  # noqa: E402


def cmd_list(_args: list[str]) -> int:
    topics = mem.list_topics()
    if not topics:
        print("No memory topics yet.")
        print("Add one with: /memory write <topic> <text>")
        return 0
    print(f"{len(topics)} topic(s):")
    for t in topics:
        try:
            summary = mem.first_line_summary(mem.topic_path(t).read_text())
        except OSError:
            summary = ""
        print(f"  {t}" + (f" — {summary}" if summary else ""))
    return 0


def cmd_show(args: list[str]) -> int:
    if not args:
        print("Usage: memory_cli.py show <topic>")
        return 2
    body = mem.read_topic(args[0])
    if not body:
        print(f"(no topic named {args[0]})")
        return 1
    print(body, end="" if body.endswith("\n") else "\n")
    return 0


def _write(args: list[str], *, append: bool) -> int:
    verb = "append" if append else "write"
    if len(args) < 2:
        print(f"Usage: memory_cli.py {verb} <topic> <text...>")
        return 2
    topic = args[0]
    text = " ".join(args[1:])
    try:
        p = mem.write_topic(topic, text, append=append)
    except ValueError as e:
        print(f"Error: {e}")
        return 1
    print(f"{'Appended to' if append else 'Saved'} {p.name}.")
    return 0


def cmd_write(args: list[str]) -> int:
    return _write(args, append=False)


def cmd_append(args: list[str]) -> int:
    return _write(args, append=True)


def cmd_forget(args: list[str]) -> int:
    if not args:
        print("Usage: memory_cli.py forget <topic>")
        return 2
    if mem.forget_topic(args[0]):
        print(f"Removed topic {args[0]}.")
        return 0
    print(f"No topic named {args[0]}.")
    return 1


_DISPATCH = {
    "list": cmd_list,
    "show": cmd_show,
    "read": cmd_show,
    "write": cmd_write,
    "set": cmd_write,
    "append": cmd_append,
    "forget": cmd_forget,
    "rm": cmd_forget,
    "delete": cmd_forget,
}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return cmd_list([])
    sub = argv[1].lower()
    rest = argv[2:]
    fn = _DISPATCH.get(sub)
    if fn is None:
        print(f"Unknown sub-command: {sub}")
        print("Try: list | show <topic> | write <topic> <text> | append <topic> <text> | forget <topic>")
        return 2
    return fn(rest)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
