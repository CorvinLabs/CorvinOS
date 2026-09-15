"""provenance.py — single source of truth for the EU AI Act Art. 50 §4
AI-generated-content marking block stamped on outbound messenger envelopes.

The same marking is applied by three independent delivery paths — the normal
adapter reply (`adapter._envelope`), the background-completion backbone
(`completion_notify`), and the scheduler's autonomous workflow notifications
(`scheduler._run_workflow_to_outbox`). Each used to build the dict inline, so a
change to the marking contract could silently drift between them. Keep it here,
in ONE place, so they cannot.
"""
from __future__ import annotations

import datetime as _dt


def build_provenance(channel: str, chat_id, persona: str = "") -> dict:
    """Return the Art. 50 §4 provenance block for a final AI message envelope.

    `chat_id` is the routing id (or chat_key) — formatted into session_id only,
    never emitted raw elsewhere. This mirrors the shape long established in
    adapter._envelope; callers stamp it under the ``provenance`` key alongside
    ``_final: True``.
    """
    return {
        "ai_generated": True,
        "generator_id": "corvin_os",
        "persona": str(persona or ""),
        "session_id": f"{channel}:{chat_id if chat_id is not None else ''}",
        "timestamp_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }
