"""audit_view.py — Layer 20: scoped audit-log read for /audit me + /audit chat.

The unified hash chain at ``<corvin_home>/global/forge/audit.jsonl``
records everything: forge / skill-forge / path-gate / consent / roles /
disclosure / quota / auth-elevation. Operators (and end-users with the
right capability) need to see relevant slices without grepping the
file by hand.

This module is read-only. It never writes back to the chain.

Two scopes:

  * **scope=me**   — events whose ``details.uid`` matches the caller's
                     uid (pairs with the ``audit_self`` capability that
                     every bundle has).
  * **scope=chat** — events whose ``details.chat_key`` matches the
                     caller's chat (pairs with the ``audit_chat``
                     capability — owner / admin only).

Output is JSON, sorted oldest-first, capped at ``MAX_EVENTS``. The JS
slash-command renderer formats the JSON into a chat reply.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

MAX_EVENTS = 50


# ── Path resolution ────────────────────────────────────────────────────

def _corvin_home() -> Path:
    """Resolve CORVIN_HOME: env var first, then repo-relative .corvin, then ~/.corvin."""
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
            return parent / ".corvin"
    return Path.home() / ".corvin"


def _audit_path() -> Path:
    return _corvin_home() / "global" / "forge" / "audit.jsonl"


# ── Iteration ──────────────────────────────────────────────────────────

def _iter_events(path: Path):
    """Yield every parseable event in the chain, in chain order."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


# ── Public API ─────────────────────────────────────────────────────────

def view_me(*, channel: str, chat_key: str, uid: str,
            limit: int = 20,
            event_type_prefix: str = "") -> dict:
    """Return the most recent events for ``uid`` in ``(channel, chat_key)``.

    Filtering rules:
      * ``details.uid`` == uid OR
      * ``details.target`` == uid OR
      * ``details.grantor`` == uid OR
      * ``details.granted_by`` == uid

    The four alternatives let a user see grants AT them (target) and
    grants BY them (grantor / granted_by) in one view.
    """
    if limit > MAX_EVENTS:
        limit = MAX_EVENTS
    if limit < 1:
        limit = 1
    out: list[dict] = []
    for ev in _iter_events(_audit_path()):
        det = ev.get("details") or {}
        if event_type_prefix and not str(ev.get("event_type", "")).startswith(event_type_prefix):
            continue
        ev_chan = det.get("channel")
        ev_chat = det.get("chat_key")
        # Loose chat scoping: when chat_key isn't recorded on the event,
        # we still admit if uid matches — some events (e.g. forge.tool_*)
        # don't carry a chat_key.
        if channel and ev_chan and ev_chan != channel:
            continue
        if chat_key and ev_chat and str(ev_chat) != str(chat_key):
            continue
        candidates = (
            det.get("uid"), det.get("target"),
            det.get("grantor"), det.get("granted_by"),
            det.get("revoker"), det.get("revoked_by"),
            det.get("user"),
        )
        if uid in [c for c in candidates if c]:
            out.append(ev)
    # Keep last N (most recent)
    if len(out) > limit:
        out = out[-limit:]
    return {
        "scope": "me",
        "channel": channel,
        "chat_key": chat_key,
        "uid": uid,
        "limit": limit,
        "count": len(out),
        "events": out,
    }


def view_chat(*, channel: str, chat_key: str,
              limit: int = 20,
              event_type_prefix: str = "") -> dict:
    """Return the most recent events scoped to ``(channel, chat_key)``.

    Owner / admin only — the JS dispatcher gates this. The function
    itself is open so the operator's CLI can use it without paying the
    role-lookup cost twice.
    """
    if limit > MAX_EVENTS:
        limit = MAX_EVENTS
    if limit < 1:
        limit = 1
    out: list[dict] = []
    for ev in _iter_events(_audit_path()):
        det = ev.get("details") or {}
        if event_type_prefix and not str(ev.get("event_type", "")).startswith(event_type_prefix):
            continue
        ev_chan = det.get("channel")
        ev_chat = det.get("chat_key")
        if channel and ev_chan and ev_chan != channel:
            continue
        if chat_key and ev_chat and str(ev_chat) != str(chat_key):
            continue
        # Admit events that name a channel matching the caller, even
        # without chat_key — chat-wide ops (e.g. session.reset) sometimes
        # only carry channel.
        if channel and ev_chan == channel and not ev_chat and chat_key:
            # If the caller asked for a specific chat_key, skip
            # chat-less channel events to avoid cross-chat noise.
            continue
        out.append(ev)
    if len(out) > limit:
        out = out[-limit:]
    return {
        "scope": "chat",
        "channel": channel,
        "chat_key": chat_key,
        "limit": limit,
        "count": len(out),
        "events": out,
    }


def summarize_event(ev: dict) -> str:
    """Render one event into a single chat-friendly line."""
    et = ev.get("event_type", "?")
    sev = ev.get("severity", "")
    det = ev.get("details") or {}
    ts = ev.get("ts")
    when = (time.strftime("%m-%d %H:%M",
                          time.localtime(ts)) if ts else "??")
    sev_marker = "" if sev in ("", "INFO") else f"[{sev}] "
    # Compose a minimal summary based on common detail keys
    fragments = []
    if det.get("uid"): fragments.append(f"uid={det['uid']}")
    if det.get("target") and det.get("target") != det.get("uid"):
        fragments.append(f"target={det['target']}")
    if det.get("grantor"): fragments.append(f"grantor={det['grantor']}")
    if det.get("revoker"): fragments.append(f"revoker={det['revoker']}")
    if det.get("bundle"): fragments.append(f"bundle={det['bundle']}")
    if det.get("action"): fragments.append(f"action={det['action']}")
    if det.get("metric"): fragments.append(f"metric={det['metric']}")
    if det.get("count") is not None: fragments.append(f"count={det['count']}")
    if det.get("limit") is not None: fragments.append(f"limit={det['limit']}")
    if det.get("reason"): fragments.append(f"reason={det['reason']}")
    summary = " ".join(fragments)
    return f"{when}  {sev_marker}{et}  {summary}".rstrip()


# ── CLI ────────────────────────────────────────────────────────────────

def _cli_me(channel: str, chat_key: str, uid: str,
            limit: str, prefix: str) -> int:
    print(json.dumps(
        view_me(channel=channel, chat_key=chat_key, uid=uid,
                limit=int(limit or 20), event_type_prefix=prefix),
        ensure_ascii=False, indent=2))
    return 0


def _cli_chat(channel: str, chat_key: str,
              limit: str, prefix: str) -> int:
    print(json.dumps(
        view_chat(channel=channel, chat_key=chat_key,
                  limit=int(limit or 20), event_type_prefix=prefix),
        ensure_ascii=False, indent=2))
    return 0


def _cli_main(argv: list[str]) -> int:
    """Subcommands:

      me     <channel> <chat_key> <uid>       [<limit>] [<event_type_prefix>]
      chat   <channel> <chat_key>             [<limit>] [<event_type_prefix>]
    """
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(_cli_main.__doc__ or "")
        return 0
    sub = argv[0].lower()
    if sub == "me":
        if len(argv) < 4:
            print(json.dumps({"ok": False, "error": "usage: me <channel> <chat_key> <uid> [<limit>] [<prefix>]"}))
            return 1
        limit = argv[4] if len(argv) >= 5 else "20"
        prefix = argv[5] if len(argv) >= 6 else ""
        return _cli_me(argv[1], argv[2], argv[3], limit, prefix)
    if sub == "chat":
        if len(argv) < 3:
            print(json.dumps({"ok": False, "error": "usage: chat <channel> <chat_key> [<limit>] [<prefix>]"}))
            return 1
        limit = argv[3] if len(argv) >= 4 else "20"
        prefix = argv[4] if len(argv) >= 5 else ""
        return _cli_chat(argv[1], argv[2], limit, prefix)
    print(json.dumps({"ok": False, "error": f"unknown subcommand: {sub!r}"}))
    return 1


if __name__ == "__main__":
    import sys as _sys
    raise SystemExit(_cli_main(_sys.argv[1:]))
