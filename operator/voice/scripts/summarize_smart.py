#!/usr/bin/env python3
"""Smart Voice Summary — Enhanced summarization with context & reasoning.

Wraps the existing summarize.py pipeline with semantic analysis:
1. Analyze the response for meaning (type, impact, trade-offs)
2. Pass analysis as context to the LLM summarizer
3. LLM uses analysis to create better narrative (not just text paraphrase)

Usage:
    summarize_smart.py --lang de|en [--max-chars 400] [--model claude-haiku-4-5]
    Reads input text from stdin, writes smart summary to stdout.

Replaces: summarize.py (backward compatible — calls summarize.py as fallback)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Import the smart analysis engine
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "console"))
try:
    from corvin_console.voice_summary_smart import (
        analyze_response,
        ResponseAnalysis,
    )
except Exception as e:
    print(f"Warning: Could not import smart analysis engine: {e}", file=sys.stderr)
    analyze_response = None


def build_analysis_prompt(analysis: ResponseAnalysis) -> str:
    """Build a prompt fragment that passes the semantic analysis to the LLM."""
    if not analysis:
        return ""

    parts = [
        "## SEMANTIC ANALYSIS (for context only — use this to understand the response better):",
        f"- Work Type: {analysis.work_type}",
        f"- Scope: {analysis.scope}",
        f"- Risk Level: {analysis.risk_level}",
    ]

    if analysis.key_files:
        parts.append(f"- Key Files: {', '.join(analysis.key_files)}")

    if analysis.blockers_resolved:
        parts.append(
            f"- Blockers Resolved: {', '.join(analysis.blockers_resolved)}"
        )

    if analysis.testing_mentioned:
        parts.append("- Testing: Mentioned (verification included)")

    if analysis.trade_offs:
        parts.append(
            f"- Trade-offs Considered: The response mentions considerations "
            f"about different approaches — make sure these deliberative aspects "
            f"come through in the narration."
        )

    parts.append(
        f"- User Benefit: {analysis.user_benefit}\n"
        f"Use this analysis to emphasize why the listener should care, "
        f"what problem is being solved, and what reasoning went into the decision. "
        f"Don't just paraphrase — narrate with purpose."
    )

    return "\n".join(parts)


def summarize_with_analysis(text: str, lang: str, max_chars: int, model: str) -> str:
    """Analyze the response, then pass analysis to summarize.py."""

    # Step 1: Analyze for semantic meaning
    analysis = None
    analysis_prompt = ""
    if analyze_response:
        try:
            analysis = analyze_response(text)
            analysis_prompt = build_analysis_prompt(analysis)
        except Exception as e:
            print(f"Warning: Analysis failed, falling back to regular summary: {e}", file=sys.stderr)

    # Step 2: Prepare input for summarize.py
    # Inject the analysis at the TOP so the LLM sees it before the content
    if analysis_prompt:
        full_input = f"{analysis_prompt}\n\n## RESPONSE TO SUMMARIZE:\n{text}"
    else:
        full_input = text

    # Step 3: Call the existing summarize.py with the enhanced input
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parent / "summarize.py"),
                "--lang", lang,
                "--max-chars", str(max_chars),
                "--model", model,
            ],
            input=full_input,
            capture_output=True,
            text=True,
            timeout=150,  # Leave room for analysis + summarize
        )

        if result.returncode != 0:
            print(f"Error from summarize.py: {result.stderr}", file=sys.stderr)
            # Fallback to basic truncation
            return text[:max_chars]

        return result.stdout.strip()

    except subprocess.TimeoutExpired:
        print("Timeout in smart summarization, falling back to truncation", file=sys.stderr)
        return text[:max_chars]
    except Exception as e:
        print(f"Error calling summarize.py: {e}", file=sys.stderr)
        return text[:max_chars]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smart voice summary with semantic analysis"
    )
    parser.add_argument(
        "--lang",
        choices=["de", "en"],
        default="en",
        help="Output language",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=400,
        help="Target summary length",
    )
    parser.add_argument(
        "--model",
        default="claude-haiku-4-5",
        help="Claude model to use",
    )

    args = parser.parse_args()

    # Read input from stdin
    text = sys.stdin.read().strip()
    if not text:
        sys.exit(0)

    # Generate smart summary
    summary = summarize_with_analysis(
        text=text,
        lang=args.lang,
        max_chars=args.max_chars,
        model=args.model,
    )

    print(summary)


if __name__ == "__main__":
    main()
