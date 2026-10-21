"""Chat status markers — ONE icon vocabulary for every chat surface.

The bridge adapter (Discord, Telegram, WhatsApp, ...) and the console web chat
both append :func:`status_marker_rule` to their system prompt, so a ✅ means the
same thing on every surface. The mapping lives here and nowhere else; a surface
that needs a new meaning extends ``STATUS_MARKERS`` and the concept, never its
own prompt text.

Design: Corvin-Knowledge ``CONCEPT-0114`` (vocabulary, limits) and the ADR that
binds it to both prompt builders. The text is prompt content for the model, so
it is English like the rest of the system prompt; the model answers in the
user's language at runtime.

The JavaScript bridge runtime (``discord/daemon.js``, ``shared/js``) cannot import this
module, so it reads ``status_markers.json`` next to it through ``shared/js/status_markers.js``.
That file is GENERATED from here (``python3 status_markers.py --write-json``) and
``test_status_marker_literals.py`` pins it to ``as_json()`` — it is an export of the
vocabulary, not a second copy (T-0249).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Icon constants — the ONLY place an icon literal for a status lives. System-generated
# chat messages (slash-command replies, notifications) take their icon from here via
# :func:`icon`, so they cannot drift from what the model is taught below.
DONE = "✅"
FAILED = "❌"
WARNING = "⚠️"
NOTE = "ℹ️"
INVESTIGATING = "🔍"
RUNNING = "⏳"
SUGGESTION = "💡"
DECISION = "📌"

# (icon, short meaning). Order is the order shown to the model. Exactly one
# meaning per icon — a second meaning makes the signal worthless.
STATUS_MARKERS: tuple[tuple[str, str], ...] = (
    (DONE, "done / succeeded / verified"),
    (FAILED, "failed / rejected / broken"),
    (WARNING, "warning / risk / limitation"),
    (NOTE, "note / context"),
    (INVESTIGATING, "investigating / finding still open"),
    (RUNNING, "running / waiting"),
    (SUGGESTION, "suggestion / option for the user"),
    (DECISION, "decision / final result"),
)

_BY_NAME = {
    "done": DONE, "failed": FAILED, "warning": WARNING, "note": NOTE,
    "investigating": INVESTIGATING, "running": RUNNING,
    "suggestion": SUGGESTION, "decision": DECISION,
}


def icon(name: str) -> str:
    """The icon for a status name (``done``, ``failed``, ``warning``, ...).

    Raises ``KeyError`` for an unknown name — a typo must fail a test, not render
    a missing icon in a chat.
    """
    return _BY_NAME[name]


#: The export the JavaScript bridges read (``shared/js/status_markers.js``).
JSON_PATH = Path(__file__).with_name("status_markers.json")


def as_json() -> dict:
    """The vocabulary as plain data: ``icons`` (name -> icon) and ``markers`` (icon, meaning)."""
    return {
        "icons": dict(_BY_NAME),
        "markers": [[glyph, meaning] for glyph, meaning in STATUS_MARKERS],
    }


def write_json(path: Path = JSON_PATH) -> Path:
    """Write the export; the test suite fails when the file on disk differs from it."""
    path.write_text(json.dumps(as_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def status_marker_rule() -> str:
    """The prompt block that teaches the marker vocabulary and its limits."""
    legend = "\n".join(f"   {glyph} {meaning}" for glyph, meaning in STATUS_MARKERS)
    return (
        "Status markers (use them to make structured replies scannable):\n"
        "   Use exactly these icons, each with its one meaning:\n"
        f"{legend}\n"
        "   Rules:\n"
        "   - Put ONE icon at the start of a line, followed by a full sentence "
        "(the sentence must carry the meaning without the icon). Use them in "
        "result lists, step lists, test summaries and decisions; a section "
        "heading may start with one when the whole section has that status.\n"
        "   - Only for replies that report several results or states. Explanations "
        "and conversation stay plain prose with no icons. Be sparing: a handful "
        "per reply at most, never rows of icons, never decoration, never inside "
        "a sentence.\n"
        "   - ✅ only for something you actually verified (test run, exit code, "
        "live check). An assumption is ℹ️ or 🔍, never ✅.\n"
        "   - NEVER inside code blocks, inline code, file names, identifiers, "
        "shell commands, logs, JSON/YAML, commit messages, PR text or code "
        "comments. Tool output and anything machine-read stays unchanged.\n"
        "   - Do not invent other icons for status."
    )


if __name__ == "__main__":  # pragma: no cover - generator entry point
    if "--write-json" in sys.argv[1:]:
        print(write_json())
    else:
        print(json.dumps(as_json(), ensure_ascii=False, indent=2))
