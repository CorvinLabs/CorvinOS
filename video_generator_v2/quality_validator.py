#!/usr/bin/env python3
"""
Quality Validator
Fail-closed content verification for generated video/audio assets.

Root cause this replaces: the previous "_verify_video_content" checks only
asserted that an ffmpeg subprocess call did not crash — a histogram filter
run succeeds on a pure black frame exactly as well as on real content, and
the except-branch returned True on top of that. It was structurally
incapable of ever returning False. This module replaces that with checks
that measure actual pixel brightness/variance, video bitrate and audio
loudness, and raise on failure instead of logging a warning and continuing.
"""

import json
import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class QualityValidationError(RuntimeError):
    """Raised when a generated asset fails a real content check."""


# Bitrate is a WEAK, secondary signal — kept only as a coarse sanity gate
# (catches a zero-byte or grossly truncated encode), not the source of
# truth for "has content". Two direct measurements proved a fixed bitrate
# threshold cannot be both: a flat `color=c=...` field measured ~17-22k
# bps at a 12s-per-slide hold, comfortably clearing a naive 5000 threshold;
# raising the threshold to 30000 then rejected a slide with 20s of REAL
# rendered text (already confirmed non-flat by validate_image_has_content
# on its own PNG) at 19058 bps — VFR/long-hold slide content legitimately
# compresses to very little because H.264 has almost nothing new to encode
# frame-to-frame on a static image. Bitrate cannot distinguish "static but
# real" from "empty", so it stays low and advisory.
# validate_video_has_visible_content() (frame-extraction + pixel variance)
# is the check that actually decides — proven by both mutation tests to
# reject the flat-field bug and accept real static-slide content alike.
MIN_VIDEO_BITRATE_BPS = 3000
MIN_BRIGHTNESS_RANGE = 30  # max-min pixel luma; a flat color frame is ~0-1
MIN_AUDIO_MEAN_VOLUME_DB = -40.0


def _run(cmd: list) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def validate_image_has_content(image_path: str, min_range: int = MIN_BRIGHTNESS_RANGE) -> None:
    """Fail-closed: raise if a PNG/frame is a flat, empty color field.

    A slide/diagram frame that is just a solid background color (the bug
    this module exists to catch) has almost zero luma variance. Real text,
    icons or diagram lines create a wide brightness range.
    """
    try:
        from PIL import Image
    except ImportError as e:
        raise QualityValidationError(
            f"Pillow not available to validate {image_path}: {e}"
        ) from e

    if not Path(image_path).exists():
        raise QualityValidationError(f"Image does not exist: {image_path}")

    with Image.open(image_path) as img:
        extrema = img.convert("L").getextrema()

    brightness_range = extrema[1] - extrema[0]
    if brightness_range < min_range:
        raise QualityValidationError(
            f"{image_path} looks empty/flat (brightness range={brightness_range}, "
            f"need >={min_range}) — this is the black/flat-slide bug, not real content"
        )
    logger.info(f"✓ Content check passed for {image_path} (brightness range={brightness_range})")


def _probe_video_bitrate_bps(video_path: str) -> int:
    result = _run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=bit_rate", "-of", "csv=p=0", video_path
    ])
    raw = (result.stdout or "").strip()
    try:
        return int(raw)
    except ValueError:
        return 0


def validate_video_bitrate(video_path: str, min_bitrate_bps: int = MIN_VIDEO_BITRATE_BPS) -> None:
    """Fail-closed: raise if a video's bitrate is too low to carry real content.

    A pure `color=c=...` fill (no text, no shapes) compresses to a near-zero
    bitrate because there is nothing for H.264 to encode frame-to-frame.
    """
    if not Path(video_path).exists():
        raise QualityValidationError(f"Video does not exist: {video_path}")

    bitrate = _probe_video_bitrate_bps(video_path)
    if bitrate < min_bitrate_bps:
        raise QualityValidationError(
            f"{video_path} bitrate is {bitrate} bps (need >={min_bitrate_bps}) — "
            f"this is the signature of an empty/flat-color video, not real content"
        )
    logger.info(f"✓ Bitrate check passed for {video_path} ({bitrate} bps)")


def validate_video_has_visible_content(
    video_path: str, min_range: int = MIN_BRIGHTNESS_RANGE, sample_count: int = 3
) -> None:
    """Fail-closed: extract real frames from the video and check they are
    not a flat color field.

    This is the check that actually catches the bug bitrate alone misses:
    it decodes real pixels via ffmpeg and measures luma variance with PIL,
    the same way validate_image_has_content() does for a standalone PNG.
    Samples multiple points across the clip so a flat INTRO with real
    content only in the middle can't slip through on frame 0.
    """
    try:
        from PIL import Image
    except ImportError as e:
        raise QualityValidationError(
            f"Pillow not available to validate {video_path}: {e}"
        ) from e

    duration = _probe_duration_seconds(video_path)
    if duration <= 0:
        raise QualityValidationError(f"Could not determine duration for {video_path}")

    checked = 0
    for i in range(sample_count):
        timestamp = duration * (i + 1) / (sample_count + 1)
        frame_path = f"/tmp/_qv_frame_{Path(video_path).stem}_{i}.png"
        result = _run([
            "ffmpeg", "-ss", str(timestamp), "-i", video_path,
            "-vframes", "1", "-y", frame_path
        ])
        if result.returncode != 0 or not Path(frame_path).exists():
            continue

        with Image.open(frame_path) as img:
            extrema = img.convert("L").getextrema()
        brightness_range = extrema[1] - extrema[0]
        Path(frame_path).unlink(missing_ok=True)

        if brightness_range >= min_range:
            logger.info(
                f"✓ Visible content confirmed for {video_path} at t={timestamp:.1f}s "
                f"(brightness range={brightness_range})"
            )
            return
        checked += 1

    raise QualityValidationError(
        f"{video_path}: all {checked or sample_count} sampled frames are flat/empty "
        f"(brightness range < {min_range}) — this is the black/flat-video bug, not real content"
    )


def probe_duration_seconds(video_path: str) -> float:
    """Public: total container duration in seconds (0.0 if unreadable)."""
    result = _run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "csv=p=0", video_path
    ])
    try:
        return float((result.stdout or "0").strip())
    except ValueError:
        return 0.0


# Backward-compatible internal alias (kept private-name usage inside this module).
_probe_duration_seconds = probe_duration_seconds


def validate_video_streams(video_path: str, require_audio: bool = False) -> None:
    """Fail-closed: raise if the video is missing a video stream (or audio, if required)."""
    if not Path(video_path).exists():
        raise QualityValidationError(f"Video does not exist: {video_path}")

    result = _run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", video_path])
    if result.returncode != 0:
        raise QualityValidationError(f"ffprobe failed on {video_path}: {result.stderr[:300]}")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise QualityValidationError(f"ffprobe returned invalid JSON for {video_path}: {e}") from e

    streams = data.get("streams", [])
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    if not has_video:
        raise QualityValidationError(f"{video_path} has no video stream")
    if require_audio and not has_audio:
        raise QualityValidationError(f"{video_path} has no audio stream (required)")

    logger.info(f"✓ Stream check passed for {video_path} (video={has_video}, audio={has_audio})")


def validate_audio_audible(video_or_audio_path: str, min_mean_db: float = MIN_AUDIO_MEAN_VOLUME_DB) -> None:
    """Fail-closed: raise if the audio track is silent or missing.

    Root cause this catches: a phase that continues past a failed TTS
    call and silently mixes in a missing/empty audio file, producing a
    video that "has an audio stream" per ffprobe but is inaudible.
    """
    result = _run([
        "ffmpeg", "-i", video_or_audio_path, "-af", "volumedetect", "-f", "null", "-"
    ])
    mean_db: Optional[float] = None
    for line in result.stderr.splitlines():
        if "mean_volume" in line:
            try:
                mean_db = float(line.split(":")[1].replace("dB", "").strip())
            except (IndexError, ValueError):
                pass

    if mean_db is None:
        raise QualityValidationError(
            f"Could not measure audio volume for {video_or_audio_path} — "
            f"no audio stream or volumedetect failed"
        )
    if mean_db < min_mean_db:
        raise QualityValidationError(
            f"{video_or_audio_path} audio is too quiet/silent (mean={mean_db} dB, "
            f"need >={min_mean_db} dB)"
        )
    logger.info(f"✓ Audio check passed for {video_or_audio_path} (mean={mean_db} dB)")


def validate_audio_video_duration_match(video_path: str, max_drift_seconds: float = 3.0) -> None:
    """Fail-closed: raise if the video and audio streams have materially
    different durations.

    Root cause this catches: `ffmpeg ... -c:v copy -c:a aac -shortest`
    does not reliably truncate a stream-copied video track to the audio
    track's length — measured directly on this pipeline's own output: a
    108.0s master video muxed against a 76.7s narration track produced a
    final file with video=97.9s / audio=78.9s, i.e. the last ~19 seconds
    play silently. `validate_audio_audible()` alone does not catch this,
    because ffmpeg's volumedetect measures the audio STREAM's own mean
    volume, which says nothing about how long the video stream runs past
    where the audio stream ends.
    """
    result = _run([
        "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration",
        "-of", "csv=p=0", video_path
    ])
    video_dur: Optional[float] = None
    audio_dur: Optional[float] = None
    for line in result.stdout.strip().splitlines():
        parts = line.split(",")
        if len(parts) != 2:
            continue
        codec_type, dur_str = parts
        try:
            dur = float(dur_str)
        except ValueError:
            continue
        if codec_type == "video":
            video_dur = dur
        elif codec_type == "audio":
            audio_dur = dur

    if video_dur is None or audio_dur is None:
        raise QualityValidationError(
            f"{video_path}: could not read both stream durations "
            f"(video={video_dur}, audio={audio_dur})"
        )

    drift = abs(video_dur - audio_dur)
    if drift > max_drift_seconds:
        raise QualityValidationError(
            f"{video_path}: video ({video_dur:.1f}s) and audio ({audio_dur:.1f}s) "
            f"durations differ by {drift:.1f}s (max allowed {max_drift_seconds}s) — "
            f"part of the video plays silently or the audio outlasts the picture"
        )
    logger.info(
        f"✓ Duration match passed for {video_path} "
        f"(video={video_dur:.1f}s, audio={audio_dur:.1f}s, drift={drift:.1f}s)"
    )


def validate_video_asset(video_path: str, require_audio: bool = False) -> None:
    """Full fail-closed check for a video asset: streams + bitrate (+audio if required).

    This is the single entry point every phase (PowerPoint, SVG, composition)
    must call on its own output before handing it to the next phase or
    declaring itself done. Raises QualityValidationError on any failure —
    callers must NOT catch-and-continue; a phase that produces empty content
    must stop the pipeline, not silently pass it downstream.

    Combines three independent signals because any one alone was proven
    (by mutation testing this exact module) to miss the flat-color bug:
    bitrate is a cheap first gate, but a flat field can still clear a
    naive threshold — the frame-extraction pixel-variance check is what
    actually catches it.
    """
    validate_video_streams(video_path, require_audio=require_audio)
    validate_video_bitrate(video_path)
    validate_video_has_visible_content(video_path)
    if require_audio:
        validate_audio_audible(video_path)
        validate_audio_video_duration_match(video_path)
