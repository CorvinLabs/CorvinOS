"""Measured quality of a produced video — from the artifacts, never invented.

Until 2026-09-20 ``GET /v1/console/video/jobs/{id}/quality-metrics`` returned a
hard-coded record (three scenes, "h264 7200k", 0.95/0.87/0.75) for every job
id, including ids that did not exist. This module reads what the Video
Producer actually wrote for a job — ``output.mp4``,
``scenes/scene_NNN.{mp4,mp3,png}``, ``metadata.json`` and the storyboard — and
measures it with ``ffprobe``:

* container/stream facts (duration, size, bitrate, codec, resolution, fps,
  pixel format, audio codec/sample rate/channels);
* subtitles (there must be none: no subtitle stream, no caption file);
* per scene: planned duration (storyboard) vs rendered duration (clip),
  drift, slide + voice presence;
* a checklist with a NAMED denominator: ``score.share`` is passed checks over
  checks that could run; a check whose input is missing is ``skip``, never a
  pass.

``ffprobe`` results are cached per (path, size, mtime). No ffprobe on the host
→ the stream facts are absent and the checks that need them are ``skip``.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_probe_cache: Dict[Tuple[str, int, int], Optional[dict]] = {}
_probe_lock = threading.Lock()


def ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


def ffprobe(path: Path) -> Optional[dict]:
    """``ffprobe -show_format -show_streams`` as a dict, cached; ``None`` when
    ffprobe is absent, the file is missing or unreadable."""
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_size, int(st.st_mtime))
    with _probe_lock:
        if key in _probe_cache:
            return _probe_cache[key]
    exe = shutil.which("ffprobe")
    result: Optional[dict] = None
    if exe:
        try:
            out = subprocess.run(
                [exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                capture_output=True, text=True, timeout=20, check=False,
            )
            if out.returncode == 0 and out.stdout.strip():
                result = json.loads(out.stdout)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            logger.debug("ffprobe failed for %s: %s", path.name, exc)
    with _probe_lock:
        _probe_cache[key] = result
    return result


def _fps(rate: str) -> Optional[float]:
    try:
        num, den = rate.split("/")
        return round(int(num) / int(den), 3) if int(den) else None
    except (ValueError, AttributeError):
        return None


def _f(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def stream_facts(probe: Optional[dict]) -> Tuple[Optional[dict], Optional[dict], Optional[dict]]:
    """``(container, video, audio)`` from a probe; each ``None`` when absent."""
    if not probe:
        return None, None, None
    fmt = probe.get("format") or {}
    container = {
        "format": str(fmt.get("format_name") or "").split(",")[0] or None,
        "duration_s": _f(fmt.get("duration")),
        "size_bytes": int(_f(fmt.get("size")) or 0),
        "bitrate_kbps": round((_f(fmt.get("bit_rate")) or 0) / 1000, 1),
    }
    video = audio = None
    for s in probe.get("streams") or []:
        if s.get("codec_type") == "video" and video is None:
            video = {
                "codec": s.get("codec_name"), "width": s.get("width"), "height": s.get("height"),
                "fps": _fps(str(s.get("r_frame_rate") or "")), "pixel_format": s.get("pix_fmt"),
                "bitrate_kbps": round((_f(s.get("bit_rate")) or 0) / 1000, 1) or None,
            }
        elif s.get("codec_type") == "audio" and audio is None:
            audio = {
                "codec": s.get("codec_name"), "sample_rate_hz": int(_f(s.get("sample_rate")) or 0) or None,
                "channels": s.get("channels"), "bitrate_kbps": round((_f(s.get("bit_rate")) or 0) / 1000, 1) or None,
            }
    return container, video, audio


def subtitle_facts(probe: Optional[dict], video_path: Optional[Path]) -> Optional[dict]:
    """Subtitles must not exist: no subtitle stream in the container, no caption
    file next to it. ``None`` when the container could not be probed."""
    if probe is None:
        return None
    streams = [s for s in (probe.get("streams") or []) if s.get("codec_type") == "subtitle"]
    files = sorted(p.name for ext in ("srt", "vtt", "ass") for p in (video_path.parent.glob(f"*.{ext}") if video_path else []))
    return {"streams": len(streams), "files": files}


def _storyboard(job: dict) -> List[dict]:
    sb = job.get("storyboard")
    if isinstance(sb, str):
        try:
            sb = json.loads(sb)
        except ValueError:
            sb = None
    scenes = (sb or {}).get("scenes") if isinstance(sb, dict) else None
    return [s for s in (scenes or []) if isinstance(s, dict)]


def scene_facts(scenes_dir: Optional[Path], storyboard: List[dict]) -> List[dict]:
    """One row per scene: the storyboard's plan next to what was rendered."""
    clips: Dict[int, Path] = {}
    if scenes_dir and scenes_dir.is_dir():
        for p in scenes_dir.glob("scene_*.mp4"):
            try:
                clips[int(p.stem.split("_")[1])] = p
            except (IndexError, ValueError):
                continue
    n = max(len(storyboard), max(clips) if clips else 0)
    rows: List[dict] = []
    for i in range(1, n + 1):
        plan = storyboard[i - 1] if i - 1 < len(storyboard) else {}
        clip = clips.get(i)
        c_probe = ffprobe(clip) if clip else None
        container, _v, _a = stream_facts(c_probe)
        actual = container["duration_s"] if container else None
        planned = (_f(plan.get("duration_ms")) or 0) / 1000 if plan else None
        planned = planned if planned else None
        drift = round((actual - planned) / planned * 100, 1) if (actual is not None and planned) else None
        voice = scenes_dir / f"scene_{i:03d}.mp3" if scenes_dir else None
        slide = scenes_dir / f"scene_{i:03d}.png" if scenes_dir else None
        v_probe = ffprobe(voice) if voice and voice.is_file() else None
        v_container, _vv, _va = stream_facts(v_probe)
        rows.append({
            "index": i,
            "id": str(plan.get("id") or f"s{i}"),
            "kind": plan.get("kind"),
            "planned_s": round(planned, 2) if planned else None,
            "actual_s": round(actual, 2) if actual is not None else None,
            "drift_pct": drift,
            "rendered": clip is not None,
            "size_bytes": clip.stat().st_size if clip else None,
            "has_slide": bool(slide and slide.is_file()),
            "has_voice": bool(voice and voice.is_file()),
            "voice_s": round(v_container["duration_s"], 2) if v_container and v_container.get("duration_s") else None,
            "narration_words": len(str(plan.get("narration_text") or "").split()) if plan else None,
        })
    return rows


def _check(cid: str, label: str, status: str, detail: str) -> dict:
    return {"id": cid, "label": label, "status": status, "detail": detail}


def checks_for(container: Optional[dict], video: Optional[dict], audio: Optional[dict],
               subtitles: Optional[dict], scenes: List[dict], storyboard: List[dict],
               probed: bool) -> List[dict]:
    out: List[dict] = []
    if not probed:
        out.append(_check("playable", "Container readable", "skip", "ffprobe is not available on this host" if not ffprobe_available() else "no output file"))
    else:
        out.append(_check("playable", "Container readable", "pass" if container and container.get("duration_s") else "fail",
                          f"{container.get('format')} · {container.get('duration_s')} s" if container else "ffprobe could not read the file"))
    if video:
        w, h = int(video.get("width") or 0), int(video.get("height") or 0)
        out.append(_check("resolution", "Resolution at least 720p",
                          "pass" if h >= 720 else "warn" if h >= 480 else "fail", f"{w}×{h}"))
        fps = video.get("fps") or 0
        out.append(_check("fps", "Frame rate at least 24 fps", "pass" if fps >= 24 else "fail", f"{fps} fps"))
        out.append(_check("pixfmt", "Pixel format widely playable", "pass" if video.get("pixel_format") == "yuv420p" else "warn",
                          str(video.get("pixel_format"))))
    else:
        out.append(_check("resolution", "Resolution at least 720p", "skip", "no video stream measured"))
    if probed:
        out.append(_check("audio", "Audio track present", "pass" if audio else "fail",
                          f"{audio.get('codec')} · {audio.get('sample_rate_hz')} Hz · {audio.get('channels')} ch" if audio else "none"))
        if audio:
            sr = audio.get("sample_rate_hz") or 0
            out.append(_check("audio_rate", "Audio sample rate at least 22.05 kHz", "pass" if sr >= 22050 else "warn", f"{sr} Hz"))
    if subtitles is None:
        out.append(_check("subtitles", "No subtitles", "skip", "container not probed"))
    else:
        bad = subtitles["streams"] or subtitles["files"]
        out.append(_check("subtitles", "No subtitles", "fail" if bad else "pass",
                          f"{subtitles['streams']} subtitle stream(s), caption file(s): {', '.join(subtitles['files']) or 'none'}" if bad
                          else "no subtitle stream, no caption file"))
    rendered = sum(1 for s in scenes if s["rendered"])
    if storyboard:
        out.append(_check("scenes", "Every storyboard scene rendered",
                          "pass" if rendered == len(storyboard) else "fail", f"{rendered} of {len(storyboard)}"))
        drifts = [abs(s["drift_pct"]) for s in scenes if s.get("drift_pct") is not None]
        if drifts:
            worst = max(drifts)
            out.append(_check("timing", "Scene timing within 10 % of the storyboard",
                              "pass" if worst <= 10 else "warn" if worst <= 25 else "fail", f"largest drift {worst} %"))
        planned = sum(s["planned_s"] or 0 for s in scenes)
        if planned and container and container.get("duration_s"):
            diff = abs(container["duration_s"] - planned) / planned * 100
            out.append(_check("runtime", "Runtime within 10 % of the plan",
                              "pass" if diff <= 10 else "warn" if diff <= 25 else "fail",
                              f"planned {round(planned, 1)} s · rendered {round(container['duration_s'], 1)} s"))
        voiced = sum(1 for s in scenes if s["has_voice"])
        out.append(_check("voice", "Every scene has a voice track", "pass" if voiced == len(scenes) else "warn", f"{voiced} of {len(scenes)}"))
    else:
        out.append(_check("scenes", "Every storyboard scene rendered", "skip", "no storyboard on the job"))
    return out


def measure(job: dict, video_output: Optional[dict]) -> dict:
    """The measured quality record for one job. ``job`` is the stored job as a
    dict (``id``, ``status``, ``storyboard``, timestamps, ``video_output_path``),
    ``video_output`` the stored output record (``video_path``, ``metadata``) or ``None``."""
    job_id = str(job.get("id"))
    video_path = Path(str((video_output or {}).get("video_path") or job.get("video_output_path") or "")).expanduser()
    video_ok = bool(str(video_path)) and video_path.is_file()
    scenes_dir = video_path.parent / "scenes" if video_ok else None
    probe = ffprobe(video_path) if video_ok else None
    container, video, audio = stream_facts(probe)
    storyboard = _storyboard(job)
    subtitles = subtitle_facts(probe, video_path if video_ok else None)
    scenes = scene_facts(scenes_dir, storyboard)
    checks = checks_for(container, video, audio, subtitles, scenes, storyboard, probed=probe is not None)
    ran = [c for c in checks if c["status"] != "skip"]
    passed = sum(1 for c in ran if c["status"] == "pass")
    started, completed = job.get("started_at"), job.get("completed_at")
    seconds = None
    try:
        if started and completed:
            seconds = round((datetime.fromisoformat(str(completed)) - datetime.fromisoformat(str(started))).total_seconds(), 1)
    except (ValueError, TypeError):  # unparseable, or a tz-aware/naive mix
        seconds = None
    return {
        "job_id": job_id,
        "status": job.get("status"),
        "measured_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ffprobe_available": ffprobe_available(),
        "source": {
            "video": str(video_path) if video_ok else None,
            "scenes_dir": str(scenes_dir) if scenes_dir and scenes_dir.is_dir() else None,
            "storyboard_scenes": len(storyboard),
            "metadata": (video_output or {}).get("metadata"),
        },
        "container": container,
        "video": video,
        "audio": audio,
        "subtitles": subtitles,
        "scenes": scenes,
        "summary": {
            "scenes_planned": len(storyboard),
            "scenes_rendered": sum(1 for s in scenes if s["rendered"]),
            "planned_s": round(sum(s["planned_s"] or 0 for s in scenes), 2),
            "rendered_s": round(container["duration_s"], 2) if container and container.get("duration_s") else None,
            "size_bytes": container["size_bytes"] if container else None,
        },
        "production": {"started_at": started, "completed_at": completed, "seconds": seconds},
        "checks": checks,
        "score": {"passed": passed, "warned": sum(1 for c in ran if c["status"] == "warn"),
                  "failed": sum(1 for c in ran if c["status"] == "fail"), "total": len(ran),
                  "skipped": len(checks) - len(ran),
                  "share": round(passed / len(ran), 3) if ran else None},
    }
