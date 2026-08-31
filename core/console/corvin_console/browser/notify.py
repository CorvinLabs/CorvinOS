"""ADR-0189 — voice-capable proactive notifications for browser-agent pauses.

Thin wrapper around ``operator/bridges/shared/completion_notify.py``'s
register/mark_done one-shot pattern: a browser session pausing on
``needs_login`` or ``needs_approval`` IS the completion signal itself (there
is nothing further to wait for before notifying), so each call here is a
self-contained register()+mark_done() pair using a FRESH task id — the
underlying queue is intentionally one-shot per record, and a single browser
session may pause more than once over its lifetime (e.g. needs_approval,
then later needs_login), so reusing one task id across pauses would silently
drop every notification after the first (mark_done refuses to resurrect an
already-delivered record).

Routing (channel + chat_id) is NOT auto-discovered — there is no mapping
today from a console chat session (cookie-authenticated) to a Discord/other
messenger identity (see docs/browser-voice-guided-navigation.md §4.3 for
why). Callers that don't have routing context simply don't pass it, and
notify_pause() then no-ops silently — the existing in-chat text message and
the (now correctly session-linked) live view remain the primary UX; this is
a best-effort ADDITION for callers that do have routing context, not a
required delivery path.
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SHARED = _HERE.parents[3] / "operator" / "bridges" / "shared"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))


def notify_pause(
    *,
    channel: str | None,
    chat_id: str | int | None,
    tenant_id: str = "_default",
    label: str = "browser task",
    text: str,
) -> bool:
    """Best-effort: push a voice-capable notification for a paused browser
    session. Returns True if a notification was registered, False if there
    was no routing context to deliver to (not an error — see module
    docstring) or the underlying queue was unavailable.
    """
    if not channel or not chat_id:
        return False
    try:
        import completion_notify as _cn  # type: ignore
    except Exception:  # noqa: BLE001 — never let a missing/broken notify
        # queue take down the browser command itself; the text delta +
        # live view already carry the information.
        return False
    try:
        tid = _cn.register(channel=channel, chat_id=chat_id, tenant_id=tenant_id,
                           label=label, want_voice=True)
        return bool(_cn.mark_done(tid, text=text, ok=True))
    except Exception:  # noqa: BLE001
        return False
