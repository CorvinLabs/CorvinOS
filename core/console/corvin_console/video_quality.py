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

The plugin's Production Report (``<store>/videos/<job>/report.json``, schema 1,
ADR-2247) is merged into the same record under ``report``: its named signals
(value, unit, threshold, verdict, basis "n of N", validity) are passed through
as the job measured them, never recomputed here. A job without a report says
so (``report.state``: ``missing`` / ``not_written`` / ``unreadable``) - nothing
is filled in for it.
"""
from __future__ import annotations

import json
import logging
import os
import re
import stat
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
            "voice_drift_pct": (round((actual - v_container["duration_s"]) / v_container["duration_s"] * 100, 1)
                                if actual is not None and v_container and v_container.get("duration_s") else None),
            "narration_words": len(str(plan.get("narration_text") or "").split()) if plan else None,
        })
    return rows


def _check(cid: str, label: str, status: str, detail: str) -> dict:
    return {"id": cid, "label": label, "status": status, "detail": detail}


def checks_for(container: Optional[dict], video: Optional[dict], audio: Optional[dict],
               subtitles: Optional[dict], scenes: List[dict], storyboard: List[dict],
               probed: bool, metadata: Optional[dict] = None) -> List[dict]:
    out: List[dict] = []
    metadata = metadata if isinstance(metadata, dict) else {}
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
        # No `timing`/`runtime` check (ADR-2247): both compared the storyboard model's length
        # ESTIMATE with the voice-driven real length, so they failed on every real video and were
        # ignored. That number is now `plan_estimate_error` in the production report - information,
        # never a verdict.
        voiced = sum(1 for s in scenes if s["has_voice"])
        provider = str(metadata.get("tts_provider_used") or "")
        if "mock" in provider:
            out.append(_check("voice", "Every scene has a voice track", "fail",
                              "silent placeholder voice (no narration provider was available)"))
        else:
            out.append(_check("voice", "Every scene has a voice track", "pass" if voiced == len(scenes) else "warn", f"{voiced} of {len(scenes)}"))
        kept = [c for c in (metadata.get("layout_collisions") or []) if isinstance(c, dict) and c.get("action") == "kept"]
        if "layout_collisions" in metadata:
            out.append(_check("layout", "No slide shipped with overlapping elements", "fail" if kept else "pass",
                              f"{len(kept)} slide(s) kept with overlaps: {', '.join(str(c.get('scene')) for c in kept[:5])}" if kept
                              else "no overlap left"))
    else:
        out.append(_check("scenes", "Every storyboard scene rendered", "skip", "no storyboard on the job"))
    return out


# ── the production report (plugin's report.json, schema 1) ──────────────────

REPORT_NAME = "report.json"
REPORT_SCHEMA = 1
MAX_REPORT_BYTES = 64 * 1024  # the plugin refuses to write a larger one (src/report.py)
SIGNAL_VERDICTS = ("pass", "warn", "fail", "skip", "info")


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return v


def _int_or_none(v: Any) -> Optional[int]:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def _short(v: Any, limit: int = 80) -> Optional[str]:
    return str(v)[:limit] if isinstance(v, (str, int, float)) and not isinstance(v, bool) and str(v) else None


def _threshold(v: Any) -> Any:
    """A number, a flat map of numbers, or None - nothing else reaches the panel."""
    if _num(v) is not None:
        return v
    if isinstance(v, dict):
        out = {str(k)[:40]: v[k] for k in v if _num(v[k]) is not None}
        return out or None
    return None


def _signal(rec: Any) -> Optional[dict]:
    """One signal record as the job wrote it, reduced to the fields the panel shows.
    An unrecognised verdict is shown as ``unknown``, never mapped to a pass."""
    if not isinstance(rec, dict) or not isinstance(rec.get("id"), str) or not rec["id"]:
        return None
    basis = rec.get("basis") if isinstance(rec.get("basis"), dict) else {}
    verdict = rec.get("verdict") if rec.get("verdict") in SIGNAL_VERDICTS else "unknown"
    value = rec.get("value")
    return {
        "id": rec["id"][:60],
        "kind": rec.get("kind") if rec.get("kind") in ("runtime", "posthoc") else None,
        "value": value if _num(value) is not None else None,
        "unit": _short(rec.get("unit")) or "",
        "threshold": _threshold(rec.get("threshold")),
        "verdict": verdict,
        "basis": {"n": _int_or_none(basis.get("n")), "of": _int_or_none(basis.get("of"))},
        "valid": rec.get("valid") is not False,
        "invalidated_by": _short(rec.get("invalidated_by"), 60),
        "reason": _short(rec.get("reason")),
        "detail": rec.get("detail") if isinstance(rec.get("detail"), dict) else None,
    }


def _read_report_file(path: Path) -> Tuple[Optional[dict], Optional[str]]:
    """``(data, None)`` or ``(None, why)``. Never follows a symlink, never reads more than the cap."""
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "unreadable"
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None, "unreadable"
        if st.st_size > MAX_REPORT_BYTES:
            return None, "too_large"
        with os.fdopen(fd, "rb") as f:
            fd = -1
            raw = f.read(MAX_REPORT_BYTES + 1)
    finally:
        if fd >= 0:
            os.close(fd)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, "not_json"
    return (data, None) if isinstance(data, dict) else (None, "not_json")


def read_report(job_dir: Optional[Path], job_id: str, metadata: Optional[dict] = None) -> dict:
    """The job's production report for the panel. ``state``:

    * ``present``     - read, schema 1, written for this job;
    * ``missing``     - no report file (jobs produced before reports existed have none);
    * ``not_written`` - the job ran with reports but its report was refused on write;
    * ``unreadable``  - a file exists but is not a schema-1 report of this job (``reason`` says why).
    """
    metadata = metadata if isinstance(metadata, dict) else {}
    if job_dir is None:
        return {"state": "missing", "signals": []}
    data, why = _read_report_file(Path(job_dir) / REPORT_NAME)
    if data is None:
        if why == "missing":
            state = "not_written" if "report" in metadata and metadata.get("report") is None else "missing"
            return {"state": state, "signals": []}
        return {"state": "unreadable", "reason": why, "signals": []}
    if data.get("schema") != REPORT_SCHEMA:
        return {"state": "unreadable", "reason": "unknown_schema", "signals": []}
    if data.get("job_id") != job_id:
        return {"state": "unreadable", "reason": "other_job", "signals": []}
    signals = [s for s in (_signal(r) for r in (data.get("signals") or [])) if s is not None]
    by_kind: Dict[str, int] = {}
    for d in data.get("degradations") or []:
        if isinstance(d, dict) and isinstance(d.get("kind"), str):
            by_kind[d["kind"][:60]] = by_kind.get(d["kind"][:60], 0) + 1
    ds = data.get("degraded_scenes") if isinstance(data.get("degraded_scenes"), dict) else {}
    stages = [{"name": _short(st.get("name"), 40), "seconds": _num(st.get("seconds"))}
              for st in (data.get("stages") or []) if isinstance(st, dict)]
    usage = data.get("llm_usage") if isinstance(data.get("llm_usage"), dict) else {}
    tools = data.get("tools") if isinstance(data.get("tools"), dict) else {}
    counts = {v: sum(1 for s in signals if s["verdict"] == v) for v in SIGNAL_VERDICTS + ("unknown",)}
    return {
        "state": "present",
        "schema": REPORT_SCHEMA,
        "plugin_version": _short(data.get("plugin_version"), 40),
        "status": _short(data.get("status"), 20),
        "failed_stage": _short(data.get("failed_stage"), 20),
        "error_class": _short(data.get("error_class"), 60),
        "language": _short(data.get("language"), 10),
        "style_id": _short(data.get("style_id"), 60),
        "storyboard_llm": _short(data.get("storyboard_llm"), 60),
        "created_at": _short(data.get("created_at"), 40),
        "finished_at": _short(data.get("finished_at"), 40),
        "total_seconds": _num(data.get("total_seconds")),
        "stages": stages,
        "degraded_scenes": {"n": _int_or_none(ds.get("n")), "of": _int_or_none(ds.get("of"))},
        "degradations_by_kind": by_kind,
        "llm_usage": {k: v for k, v in usage.items() if _int_or_none(v) is not None},
        "tools": {str(k)[:20]: _short(v, 40) for k, v in tools.items()},
        "signals": signals,
        "verdicts": counts,
    }


def measure(job: dict, video_output: Optional[dict], *, job_dir: Optional[Path] = None,
            with_report: bool = False) -> dict:
    """The measured quality record for one job. ``job`` is the stored job as a
    dict (``id``, ``status``, ``storyboard``, timestamps, ``video_output_path``),
    ``video_output`` the stored output record (``video_path``, ``metadata``) or ``None``.
    ``with_report``: also merge the job's production report from ``job_dir`` (the caller
    has already confined ``job_dir`` to the tenant's store)."""
    job_id = str(job.get("id"))
    video_path = Path(str((video_output or {}).get("video_path") or job.get("video_output_path") or "")).expanduser()
    video_ok = bool(str(video_path)) and video_path.is_file()
    scenes_dir = video_path.parent / "scenes" if video_ok else None
    probe = ffprobe(video_path) if video_ok else None
    container, video, audio = stream_facts(probe)
    storyboard = _storyboard(job)
    subtitles = subtitle_facts(probe, video_path if video_ok else None)
    scenes = scene_facts(scenes_dir, storyboard)
    checks = checks_for(container, video, audio, subtitles, scenes, storyboard, probed=probe is not None,
                        metadata=(video_output or {}).get("metadata"))
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
        **({"report": read_report(job_dir, job_id, (video_output or {}).get("metadata"))} if with_report else {}),
    }
