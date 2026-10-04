"""Scene transitions for Video Producer (CONCEPT-0093 Phase 2).

Replaces the old "concat filter, hard cut between scenes" assembly with a
crossfade chain via FFmpeg's xfade filter. Each scene is first rendered as
its own short clip (one static frame held for its audio duration), then the
clips are chained pairwise with xfade, each transition overlapping the
previous clip's tail with the next clip's head.

xfade requires the offset (in seconds, within the cumulative timeline) at
which each transition starts — easy to get wrong by hand, so the chain
builder computes it from each clip's own duration rather than letting a
caller pass hardcoded offsets.
"""

import os
import subprocess
from typing import List, Tuple

XFADE_TRANSITIONS = {"fade", "dissolve", "slideup", "slideleft", "slideright", "circleopen"}


def render_scene_clip(frame_path: str, duration: float, output_path: str,
                       width: int = 1920, height: int = 1080, fps: int = 30) -> str:
    """Turn one static frame into a short silent video clip of the given
    duration — the unit xfade operates on (it needs real video streams to
    cross-fade between, not standalone images)."""
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-t", f"{duration}", "-i", frame_path,
        "-r", str(fps),
        "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-vf", f"scale={width}:{height}",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"Scene clip render failed for {frame_path}: {result.stderr}")
    return output_path


def build_crossfade_chain(
    clips: List[Tuple[str, float]],
    output_path: str,
    transition: str = "fade",
    transition_duration: float = 0.5,
) -> str:
    """Chain N scene clips with xfade crossfades instead of a hard concat cut.

    Args:
        clips: list of (clip_path, clip_duration_seconds) — duration must be
            the clip's REAL measured duration (ffprobe), not the target,
            since offsets compound across the chain.
        transition: one of XFADE_TRANSITIONS.
        transition_duration: overlap length in seconds. Each transition
            trims transition_duration off the combined runtime (two clips
            overlap by that much instead of playing back to back), so the
            final video is shorter than sum(durations) by
            (n-1) * transition_duration.

    Returns:
        output_path (silent video — caller muxes audio separately, same as
        the old concat-filter path did).
    """
    if transition not in XFADE_TRANSITIONS:
        raise ValueError(f"Unknown transition {transition!r}; use one of {XFADE_TRANSITIONS}")
    if len(clips) < 2:
        raise ValueError("Need at least 2 clips to build a crossfade chain")

    cmd = ["ffmpeg", "-y"]
    for clip_path, _ in clips:
        cmd.extend(["-i", clip_path])

    # Chain xfade pairwise: [0][1] -> v01, [v01][2] -> v012, ...
    # offset for transition k is (sum of durations of clips 0..k) minus the
    # cumulative overlap already consumed by earlier transitions.
    filter_parts = []
    running_offset = clips[0][1]
    prev_label = "0"
    for i in range(1, len(clips)):
        next_label = f"v{i}"
        offset = running_offset - transition_duration
        in_a = f"[{prev_label}]" if prev_label == "0" else f"[{prev_label}]"
        filter_parts.append(
            f"{in_a}[{i}]xfade=transition={transition}:duration={transition_duration}:offset={offset}[{next_label}]"
        )
        running_offset += clips[i][1] - transition_duration
        prev_label = next_label

    filter_complex = ";".join(filter_parts)
    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", f"[{prev_label}]",
        "-c:v", "libx264", "-preset", "medium", "-b:v", "2000k",
        output_path,
    ])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(f"Crossfade chain assembly failed: {result.stderr}")
    return output_path


def get_clip_duration(path: str) -> float:
    """Real measured duration via ffprobe — never guessed (frame-perfect
    offsets in build_crossfade_chain depend on this being accurate)."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode == 0 and result.stdout.strip():
        return float(result.stdout.strip())
    raise RuntimeError(f"ffprobe failed to measure duration of {path}: {result.stderr}")
