"""goal.py — Per-chat session goal backend (CLI + module API).

Lets users set a sticky goal for a chat via ``/goal <text>``. The goal is
injected into every LLM turn as a ``<session_goal>`` block so the model
always has the user's objective in context.

Storage
-------
Single JSON file per (channel, chat_key)::

    <corvin_home>/global/goals/<safe_channel>__<safe_chat>.json

    {
      "goal": "Ship the billing integration by Friday.",
      "set_at": 1778204770.0
    }

CLI
---
::

    python3 goal.py set   <channel> <chat_key> <text>
    python3 goal.py get   <channel> <chat_key>
    python3 goal.py clear <channel> <chat_key>

All output is JSON: ``{"ok": true, "goal": "..." | null}`` or
``{"ok": false, "error": "..."}``.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


# ── Path helpers ──────────────────────────────────────────────────────────

def _corvin_home() -> Path:
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
            p = parent / ".corvin"
            return p
    return Path.home() / ".corvin"


def _safe_component(s: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in s)[:64] or "anon"


def _store_path(channel: str, chat_key: str) -> Path:
    base = _corvin_home() / "global" / "goals"
    name = f"{_safe_component(channel or 'unknown')}__{_safe_component(str(chat_key) if chat_key else 'anon')}.json"
    return base / name


# ── Core operations ───────────────────────────────────────────────────────

def load_goal(channel: str, chat_key: str) -> str | None:
    """Return the current goal text, or None if none is set."""
    p = _store_path(channel, chat_key)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        return data.get("goal") or None
    except Exception:
        return None


def set_goal(channel: str, chat_key: str, text: str) -> str:
    """Persist goal text. Returns the stored text."""
    text = text.strip()
    if not text:
        raise ValueError("goal text must not be empty")
    if len(text) > 2000:
        raise ValueError("goal text exceeds 2000 characters")
    p = _store_path(channel, chat_key)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"goal": text, "set_at": time.time()}, ensure_ascii=False, indent=2))
    tmp.replace(p)
    return text


def clear_goal(channel: str, chat_key: str) -> None:
    """Remove the stored goal, silently no-ops if none exists."""
    p = _store_path(channel, chat_key)
    try:
        p.unlink()
    except FileNotFoundError:
        pass


# ── System-prompt render ──────────────────────────────────────────────────

def render_block(goal_text: str) -> str:
    """Format the goal as a <session_goal> system-prompt block."""
    return (
        "<session_goal>\n"
        f"The user has set the following goal for this conversation:\n{goal_text}\n"
        "Keep this goal in mind. Help the user make progress toward it.\n"
        "</session_goal>"
    )


# ── CLI entry-point ───────────────────────────────────────────────────────

def _emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False))


def main(argv: list[str]) -> None:
    if len(argv) < 4:
        _emit({"ok": False, "error": "Usage: goal.py <set|get|clear> <channel> <chat_key> [text]"})
        return

    cmd, channel, chat_key = argv[1], argv[2], argv[3]

    if cmd == "get":
        goal = load_goal(channel, chat_key)
        _emit({"ok": True, "goal": goal})

    elif cmd == "clear":
        clear_goal(channel, chat_key)
        _emit({"ok": True, "goal": None})

    elif cmd == "set":
        text = " ".join(argv[4:]).strip() if len(argv) > 4 else ""
        if not text:
            _emit({"ok": False, "error": "set requires non-empty text"})
            return
        try:
            stored = set_goal(channel, chat_key, text)
            _emit({"ok": True, "goal": stored})
        except ValueError as e:
            _emit({"ok": False, "error": str(e)})

    else:
        _emit({"ok": False, "error": f"unknown command: {cmd!r}"})


if __name__ == "__main__":
    main(sys.argv)
