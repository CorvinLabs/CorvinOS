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

_OPEN = "<voice>"
_CLOSE = "</voice>"


def extract_voice_override(text: str) -> tuple[str, str | None]:
    """Pull the optional `<voice>…</voice>` block out of *text*.

    Returns (chat_text_without_tag, voice_text_or_None). When present, the
    block is used verbatim as the spoken text — summarize.py is skipped
    entirely. Whitespace artifacts from the cut-out tag are cleaned up so the
    chat-text doesn't end up with weird gaps.

    Pairing is LAST-open-to-LAST-close, NOT leftmost-match. The producer
    appends exactly one real block, but the VISIBLE prose can legitimately
    *mention* the literal token ``<voice>`` (e.g. a reply explaining this very
    mechanism, or code in backticks). A leftmost `<voice>…</voice>` search would
    pair that stray earlier mention with the real block's closing tag and swallow
    everything in between as the "override" — the visible reply then truncated at
    the stray mention (reported 2026-07-13: a reply cut off mid-section because it
    said "the `<voice>` path" before its real block). Anchoring on the LAST
    `</voice>` and the nearest preceding `<voice>` extracts the real trailing
    block and leaves any earlier literal mention untouched in the chat text.
    """
    low = text.lower()
    close = low.rfind(_CLOSE)
    if close == -1:
        return text, None
    open_ = low.rfind(_OPEN, 0, close)
    if open_ == -1:
        return text, None
    voice_text = text[open_ + len(_OPEN):close].strip()
    stripped = text[:open_] + text[close + len(_CLOSE):]
    stripped = re.sub(r"\n{3,}", "\n\n", stripped).strip()
    return stripped, voice_text or None


def with_voice_override(visible: str, spoken: str) -> str:
    """Build a reply string carrying a `<voice>` override for *spoken*.

    *visible* may contain untrusted content (subprocess stderr, an HTTP error
    body, ...). Neutralizing any literal `<`/`>` in it — not just around a
    known-untrusted suffix — is defense-in-depth so that content can never
    contain a stray `<voice>`/`</voice>` sequence that interacts with
    extract_voice_override() at all. (The extractor now pairs the LAST
    `</voice>` with the nearest preceding `<voice>`, so a stray EARLIER opening
    tag no longer hijacks OUR trailing block — but escaping the visible half
    keeps a stray CLOSING tag in untrusted content from truncating our block
    either.) *spoken* is always a static, developer-authored sentence — never
    untrusted — so it is used as-is.
    """
    safe_visible = visible.replace("<", "‹").replace(">", "›")
    return f"{safe_visible}\n\n<voice>{spoken}</voice>"
