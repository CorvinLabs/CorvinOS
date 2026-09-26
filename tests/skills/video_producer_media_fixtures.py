"""Real (not fake-byte) minimal media generators for video_producer worker tests.

Several tests fed ffmpeg literal garbage (``b"PNG..." + b"\\x00" * 1000``) as
"slide"/"audio" fixtures. That only ever worked because `video_assembler.py`'s
`_run_ffmpeg` falls back to a stub MP4 when `ffmpeg` is absent from PATH --
once a real `ffmpeg` is installed (as it now is, system-wide), the real
encoder runs against undecodable bytes and fails with `status: "partial"`.
These helpers generate genuinely decodable minimal media via `ffmpeg` itself,
so the tests exercise real encoding instead of silently depending on ffmpeg's
absence.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def write_real_png(path: Path, width: int = 64, height: int = 64, color: str = "blue") -> None:
    """A tiny, genuinely decodable PNG."""
    subprocess.run(
        [
            "ffmpeg", "-f", "lavfi", "-i", f"color=c={color}:s={width}x{height}",
            "-frames:v", "1", "-y", str(path),
        ],
        check=True, capture_output=True, timeout=30,
    )


def write_real_audio(path: Path, duration: float = 1.0, freq: int = 440) -> None:
    """A tiny, genuinely decodable audio file (container inferred from ``path``'s suffix)."""
    subprocess.run(
        [
            "ffmpeg", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={duration}",
            "-y", str(path),
        ],
        check=True, capture_output=True, timeout=30,
    )
