#!/usr/bin/env python3
"""Smart Voice Summary — Enhanced summarization with context & reasoning.

Direct usage of smart analysis + voice generation (not LLM-based):
1. Analyze the response for meaning (type, impact, trade-offs)
2. Generate natural narrative directly using `generate_voice_summary()` with tone
3. Polish for audio (remove code, expand acronyms)

This bypasses the LLM entirely for faster, consistent, human-sounding summaries
that respect the voice profile tone (warm/formal).

Usage:
    summarize_smart.py --lang de|en [--max-chars 400] [--tone warm|formal]
    Reads input text from stdin, writes smart summary to stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Import the smart analysis & generation engine
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "console"))
try:
    from corvin_console.voice_summary_smart import (
        analyze_response,
        generate_voice_summary,
        polish_for_audio,
        ResponseAnalysis,
    )
except Exception as e:
    print(f"Error: Could not import smart analysis engine: {e}", file=sys.stderr)
    sys.exit(1)


def summarize_with_smart_engine(
    text: str,
    lang: str,
    max_chars: int,
    tone: str = "warm",
    user_name: str = "",
) -> str:
    """Analyze response and generate summary using voice_summary_smart engine.

    This bypasses LLM entirely and uses direct semantic analysis + natural
    generation. Much faster and more consistent than LLM-based approach,
    and respects voice profile tone (warm/formal/casual).

    Args:
        text: Response text to summarize
        lang: Language (de/en)
        max_chars: Target character limit
        tone: Voice tone from profile (warm/formal/casual)
        user_name: User's name for personalization
    """
    if not text or not text.strip():
        return ""

    try:
        # Step 1: Analyze the response for meaning
        analysis = analyze_response(text)

        # Step 2: Generate summary using smart engine (respects tone)
        summary = generate_voice_summary(
            analysis,
            max_words=int(max_chars / 4),  # ~4 chars per word in German/English
            tone=tone,
            user_name=user_name,
        )

        # Step 3: Polish for audio (expand acronyms, remove code, etc)
        # Map lang codes to language names for polish_for_audio
        lang_code = "de" if lang.lower().startswith("de") else "en"
        polished = polish_for_audio(summary, max_length=max_chars, lang=lang_code)

        return polished

    except Exception as e:
        print(f"Warning: Smart summary generation failed: {e}", file=sys.stderr)
        # Fallback: just return first N characters
        return text[:max_chars].strip()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smart voice summary with semantic analysis + tone respect"
    )
    parser.add_argument(
        "--lang",
        choices=["de", "en"],
        default="de",
        help="Output language",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=400,
        help="Target summary length (characters)",
    )
    parser.add_argument(
        "--tone",
        choices=["warm", "formal", "casual"],
        default="warm",
        help="Voice tone from profile (affects phrasing)",
    )
    parser.add_argument(
        "--user-name",
        default="",
        help="User's name for personalization (optional)",
    )

    args = parser.parse_args()

    # Read input from stdin
    text = sys.stdin.read().strip()
    if not text:
        sys.exit(0)

    # Generate smart summary with tone respect
    summary = summarize_with_smart_engine(
        text=text,
        lang=args.lang,
        max_chars=args.max_chars,
        tone=args.tone,
        user_name=args.user_name,
    )

    print(summary)


if __name__ == "__main__":
    main()
