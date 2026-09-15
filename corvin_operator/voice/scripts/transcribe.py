#!/usr/bin/env python3
"""Transcribe an audio file to text via the STT provider chain.

Thin CLI wrapper over ``scripts/stt/`` — the actual provider logic
lives in the package. Resolution order:

1. ``--provider <name>`` flag (no fallback if pinned)
2. ``CORVIN_STT_PROVIDER`` env var (same semantics)
3. ``CORVIN_STT_CHAIN`` env var (comma-separated, e.g. ``local,openai``)
4. Default chain: ``openai → local``

Usage:
    transcribe.py <audio_file> [--lang de|en|auto] [--provider openai|local]
                  [--timeout-s 60]

Exit codes:
    0   success — transcript printed to stdout
    1   file-not-found / no providers available
    2   provider-side error (API failure, model failure)
    3   timeout (caller's budget exhausted)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the stt package importable when running as a standalone script.
_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

from stt import (  # noqa: E402
    STTProviderUnavailable,
    STTTimeout,
    STTTranscriptionFailed,
    transcribe as stt_transcribe,
)


def main() -> int:
    ap = argparse.ArgumentParser(prog="transcribe.py")
    ap.add_argument("audio", help="path to audio file (WAV/MP3/M4A/FLAC/OGG/...)")
    ap.add_argument("--lang", default="auto",
                    help="language hint (e.g. de, en, auto)")
    ap.add_argument("--provider", default=None,
                    help="pin a specific provider (openai|local); "
                         "disables fallback")
    ap.add_argument("--timeout-s", type=float, default=None,
                    help="per-provider timeout (seconds)")
    args = ap.parse_args()

    audio_path = Path(args.audio)
    if not audio_path.is_file():
        print(f"[transcribe] file not found: {audio_path}", file=sys.stderr)
        return 1

    lang = args.lang if args.lang and args.lang != "auto" else None
    try:
        result = stt_transcribe(
            audio_path,
            lang=lang,
            timeout_s=args.timeout_s,
            provider=args.provider,
        )
    except STTProviderUnavailable as exc:
        print(f"[transcribe] no provider available: {exc}", file=sys.stderr)
        return 1
    except STTTimeout as exc:
        print(f"[transcribe] timeout: {exc}", file=sys.stderr)
        return 3
    except STTTranscriptionFailed as exc:
        print(f"[transcribe] provider error: {exc}", file=sys.stderr)
        return 2

    print(result.text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
