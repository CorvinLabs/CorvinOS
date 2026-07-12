"""voice_tag.py — the `<voice>…</voice>` override mechanism.

A reply's visible chat text and its spoken (TTS) text are usually the same
string. When they should differ — most commonly because the visible text
contains technical detail (CLI syntax, stack fragments, `--flags`,
`ALL_CAPS_ENV_VARS`) that would sound like "reading out the command line" if
read aloud verbatim — the producer appends a `<voice>…</voice>` block with a
natural spoken alternative. :func:`extract_voice_override` pulls it out
before the visible text is shown and before build_voice_summary() runs.

Bug report (2026-07-12): a hardcoded engine-fallback string was used as BOTH
the visible AND (unmodified) the spoken text, so TTS read out backticks and
env-var names verbatim. The fix is this override mechanism — already used by
model-authored replies — applied to every hardcoded fallback string that
carries technical detail.

Extracted from adapter.py (which used to define this inline) into its own
module so completion_notify.py can use it too without importing all of
adapter.py (which would be circular — adapter.py imports completion_notify).
"""
from __future__ import annotations

import re

_VOICE_TAG_RE = re.compile(r"<voice>\s*(.+?)\s*</voice>", re.DOTALL | re.IGNORECASE)


def extract_voice_override(text: str) -> tuple[str, str | None]:
    """Pull the optional `<voice>…</voice>` block out of *text*.

    Returns (chat_text_without_tag, voice_text_or_None). When present, the
    block is used verbatim as the spoken text — summarize.py is skipped
    entirely. Whitespace artifacts from the cut-out tag are cleaned up so the
    chat-text doesn't end up with weird gaps.
    """
    if "<voice>" not in text.lower() or "</voice>" not in text.lower():
        return text, None
    m = _VOICE_TAG_RE.search(text)
    if not m:
        return text, None
    voice_text = m.group(1).strip()
    stripped = _VOICE_TAG_RE.sub("", text, count=1)
    stripped = re.sub(r"\n{3,}", "\n\n", stripped).strip()
    return stripped, voice_text or None


def with_voice_override(visible: str, spoken: str) -> str:
    """Build a reply string carrying a `<voice>` override for *spoken*.

    *visible* may contain untrusted content (subprocess stderr, an HTTP error
    body, ...). Neutralizing any literal `<`/`>` in it — not just around a
    known-untrusted suffix — is what stops that content from ever containing
    a stray `<voice>`/`</voice>` sequence that could hijack
    extract_voice_override()'s leftmost-match regex (it always matches from
    the FIRST `<voice>` it finds to the NEXT `</voice>`, so an earlier stray
    opening tag would otherwise pair with OUR closing tag instead of its
    own). *spoken* is always a static, developer-authored sentence — never
    untrusted — so it is used as-is.
    """
    safe_visible = visible.replace("<", "‹").replace(">", "›")
    return f"{safe_visible}\n\n<voice>{spoken}</voice>"
