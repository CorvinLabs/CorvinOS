#!/usr/bin/env python3
"""Strip code blocks, markdown formatting, and tool noise so TTS doesn't read garbage.

Reads stdin, writes a cleaned version to stdout that is suitable for being
spoken aloud. Two modes:

  --mode full       (default) Strip everything: code, headings, bullets, bold,
                    links, tables. Output is plain prose ready for TTS.

  --mode code-only  Only remove things that would actively destroy a summarizer's
                    understanding (fenced code blocks, raw HTML, table separator
                    lines). Keeps headings, bullets, bold so the LLM still sees
                    the structural shape of the text. Use this BEFORE summarize.py
                    so the summarizer can recognize "this is a list of N items"
                    and verbalize it instead of dropping items on the floor.
"""

from __future__ import annotations

import argparse
import re
import sys


def _force_utf8_stdio() -> None:
    """Read stdin / write stdout as UTF-8 regardless of the locale codec.

    This script is a pure text filter in the middle of the voice pipeline, so it
    must be byte-honest about characters the locale codec cannot represent. On
    Windows the streams default to cp1252, which has no mapping for 0x81, 0x8d,
    0x8f, 0x90 or 0x9d — bytes that occur inside perfectly ordinary UTF-8 emoji
    (U+1F410 is ``f0 9f 90 90``), so decoding a caller's UTF-8 stdin raises and
    the filter dies mid-pipeline. Twin of the same helper in ``summarize.py``;
    both are duplicated on purpose because these scripts are executed by PATH
    from Python and from ``daemon.js`` and share no importable package.

    Guard: ``tests/test_voice_subprocess_encoding.py``.
    """
    for stream, errs in ((sys.stdin, "replace"), (sys.stdout, "replace"),
                         (sys.stderr, "backslashreplace")):
        try:
            stream.reconfigure(encoding="utf-8", errors=errs)  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


_force_utf8_stdio()


def strip_code_only(text: str) -> str:
    # Fenced code blocks: drop entirely. Their contents are noise for the
    # summarizer and the prose around them is what we want to read aloud.
    # The \Z arm also drops an UNTERMINATED trailing fence (streaming
    # cut-off / missing closing ```) — mirrors detect_lang._CODE_FENCE_RE.
    text = re.sub(r"```[\s\S]*?(?:```|\Z)", "", text)
    # Inline code → unwrap, the words inside are usually identifiers worth speaking.
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Table separator rows (---|---|---) — the pipes confuse models, the row is content-free.
    text = re.sub(r"^\s*\|?[\s:|-]+\|[\s:|-]+\|?\s*$", "", text, flags=re.MULTILINE)
    # Collapse runs of blank lines but keep paragraph breaks.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_full(text: str) -> str:
    # Start from code-stripped text so we never read code aloud.
    text = strip_code_only(text)
    # Headings: drop leading #s but keep the heading text.
    text = re.sub(r"^\s*#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Bold/italic markers.
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", text)
    # Links: keep the visible text only.
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Bullet markers at line start.
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    # Blockquote markers.
    text = re.sub(r"^\s*>\s?", "", text, flags=re.MULTILINE)
    # Remaining table pipes.
    text = re.sub(r"\|", " ", text)
    # Collapse whitespace.
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["full", "code-only"], default="full")
    args = ap.parse_args()
    raw = sys.stdin.read()
    out = strip_code_only(raw) if args.mode == "code-only" else strip_full(raw)
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
