#!/usr/bin/env python3
"""Generate and play short earcons (audible cues) for trivial-turn signaling.

Three earcons are generated on first use and cached on disk:
  done   880Hz, 200ms, fade-out      — "ok / accepted / done"
  error  440Hz→220Hz, 400ms, descend — "something went wrong"
  tool   660Hz, 100ms, click         — "tool ran"

We deliberately do NOT bundle any binary audio in the repo. WAVs are written
to ~/.cache/corvin-voice/earcons/ on first call. Pure-stdlib (math, struct,
wave) — no extra deps.

Usage:
    earcon.py {done|error|tool}
    earcon.py --classify <textfile>   # prints "trivial" or "speak"

The classify subcommand is the trivial-skip heuristic used by stop_hook.sh:
  - text < threshold chars (default 80) AND
  - text doesn't end with '?' AND
  - first non-empty line isn't a bullet/number list item
  → "trivial" (caller should play `done` earcon instead of full TTS)
  Otherwise → "speak" (run the normal TTS pipeline).
"""

from __future__ import annotations

import argparse
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import wave
from pathlib import Path

def _voice_dir() -> Path:
    """Inline corvinOS path resolver — kept self-contained so this script
    runs without importing the bridges/shared/paths.py module."""
    env = os.environ.get("CORVIN_HOME") or os.environ.get("CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env))) / "voice"
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
            return parent / ".corvin" / "voice"
    return Path.home() / ".corvin" / "voice"


def _earcon_cache_dir() -> Path:
    """Honour ``XDG_CACHE_HOME`` (legacy override) or root under voice_dir()."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "corvin-voice" / "earcons"
    return _voice_dir() / "earcons"


CACHE_DIR = _earcon_cache_dir()

SAMPLE_RATE = 44100
AMPLITUDE = 0.3  # peak amplitude (0..1); 0.3 ≈ -10 dBFS, perceptually distinct without being loud


def _write_wav(path: Path, samples: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        frames = b"".join(
            struct.pack("<h", max(-32767, min(32767, int(32767 * s))))
            for s in samples
        )
        w.writeframes(frames)


def _envelope(i: int, total: int, fade_out_frac: float = 0.25) -> float:
    """Linear fade-in (5ms) and fade-out (fade_out_frac of total) envelope."""
    fade_in = int(SAMPLE_RATE * 0.005)
    fade_out = int(total * fade_out_frac)
    if i < fade_in:
        return i / fade_in
    if i > total - fade_out:
        return max(0.0, (total - i) / fade_out)
    return 1.0


def _tone(freq: float, duration_s: float, fade_out_frac: float = 0.25) -> list[float]:
    n = int(SAMPLE_RATE * duration_s)
    return [
        AMPLITUDE * _envelope(i, n, fade_out_frac) * math.sin(2 * math.pi * freq * i / SAMPLE_RATE)
        for i in range(n)
    ]


def gen_done(path: Path) -> None:
    _write_wav(path, _tone(880.0, 0.2, fade_out_frac=0.3))


def gen_error(path: Path) -> None:
    # Two descending tones, no gap.
    _write_wav(path, _tone(440.0, 0.18, 0.2) + _tone(220.0, 0.22, 0.4))


def gen_tool(path: Path) -> None:
    _write_wav(path, _tone(660.0, 0.1, fade_out_frac=0.5))


GENERATORS = {"done": gen_done, "error": gen_error, "tool": gen_tool}


def ensure(name: str) -> Path:
    if name not in GENERATORS:
        raise ValueError(f"unknown earcon: {name}")
    path = CACHE_DIR / f"{name}.wav"
    if not path.exists() or path.stat().st_size == 0:
        GENERATORS[name](path)
    return path


def play(name: str) -> int:
    path = ensure(name)
    for player, args in [
        ("paplay", [str(path)]),
        ("aplay", ["-q", str(path)]),
        ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet", str(path)]),
        ("play", ["-q", str(path)]),
        ("mpv", ["--really-quiet", "--no-video", str(path)]),
    ]:
        if shutil.which(player):
            try:
                subprocess.run([player, *args], check=False)
                return 0
            except OSError as exc:
                print(f"[earcon] {player} failed: {exc}", file=sys.stderr)
                continue
    print("[earcon] no audio player available", file=sys.stderr)
    return 1


# Trivial-skip heuristic. Conservative: when in doubt, return "speak"
# so the user never misses real content.
LIST_LINE_RE = re.compile(r"^\s*(?:\*{0,2}\d+\.|[-*+]\s|\d+\.)")


def classify(text: str, threshold: int = 80) -> str:
    text = text.strip()
    if not text:
        return "trivial"
    if len(text) >= threshold:
        return "speak"
    if text.rstrip().endswith("?"):
        return "speak"
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    if LIST_LINE_RE.match(first):
        return "speak"
    return "trivial"


def main() -> int:
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd")

    play_p = sp.add_parser("play")
    play_p.add_argument("name", choices=list(GENERATORS.keys()))

    cls_p = sp.add_parser("classify")
    cls_p.add_argument("--threshold", type=int, default=80)
    cls_p.add_argument("file", nargs="?", help="path to text file; stdin if omitted")

    # Backward-compat: bare `earcon.py done` == `earcon.py play done`
    ap.add_argument("legacy", nargs="?", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.cmd == "play":
        return play(args.name)
    if args.cmd == "classify":
        text = Path(args.file).read_text() if args.file else sys.stdin.read()
        print(classify(text, args.threshold))
        return 0
    if args.legacy in GENERATORS:
        return play(args.legacy)

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
