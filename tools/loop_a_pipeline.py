#!/usr/bin/env python3
"""Loop A (3D PoC) — runs the production tasks on its own, one time slice at a time.

    python tools/loop_a_pipeline.py [--concept learning_concepts/audit_chain.yaml]
                                    [--budget-s 480] [--threads 8] [--no-board]

Started every 10 minutes by ``corvin-loop-a.timer``. Each run picks up where the
last one stopped and works for at most ``--budget-s`` seconds:

  #2 YAML + TTS  narration of every scene → ``audio/<scene>.mp3`` (OpenAI TTS as
                 planned; espeak-ng locally if OpenAI fails). Scene lengths are
                 then fitted to the narration → ``concept.resolved.yaml``.
  #3 Render      full-quality frames from the resolved concept, in chunks, never
                 re-rendering a valid frame (resumable across runs/reboots).
  #4 Compose     per-scene EXR → video, narration laid under each scene, all
                 scenes concatenated → ``<slug>.mp4``; verified with ffprobe.
  #5 Study,      need people (45 participants) and their data. The runner does not
  #6 Analysis    pretend: it marks them blocked with the reason and stops there.

Every step reports to the initiatives board (status, progress, note) through
``initiatives.update_task`` and writes one content-free audit record per stage
transition (``loop_a.stage``). Output lives next to the generator's:
``<CORVIN_HOME>/tenants/<tid>/video_library/<slug>/``.

Resource use: Blender runs at ``nice 19`` with ``--threads`` cores, so the
console stays responsive; one run at a time (lock file).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import blender_3d_scene_generator as gen  # noqa: E402

TASKS = {"tts": "2", "render": "3", "compose": "4", "study": "5", "analysis": "6"}
CHUNK_FRAMES = 12
NARRATION_TAIL_S = 0.8  # silence after the narration before the scene ends


# ── infrastructure ───────────────────────────────────────────────────────────

class Board:
    """Reports to the initiatives board; a no-op when there is no board."""

    def __init__(self, tenant: str, initiative: str, enabled: bool = True):
        self.tenant, self.initiative, self.enabled = tenant, initiative, enabled
        self._mod = None
        if enabled:
            try:
                sys.path.insert(0, str(REPO / "core" / "console"))
                from corvin_console import initiatives  # noqa: PLC0415
                self._mod = initiatives
            except Exception as exc:  # noqa: BLE001
                print(f"board unavailable: {exc}", file=sys.stderr)

    def task(self, tid: str) -> dict | None:
        if not self._mod:
            return None
        try:
            b = self._mod.board(self.tenant)
        except Exception:  # noqa: BLE001
            return None
        ini = next((i for i in b["initiatives"] if i["id"] == self.initiative), None)
        return next((t for t in (ini or {}).get("tasks", []) if t["id"] == tid), None)

    def update(self, tid: str, **kw) -> None:
        if not self._mod:
            return
        try:
            self._mod.update_task(self.tenant, self.initiative, tid, **kw)
        except Exception as exc:  # noqa: BLE001 — never lose work over a board write
            print(f"board update failed for {tid}: {exc}", file=sys.stderr)


def audit(tenant: str, stage: str, **counts) -> None:
    """One content-free chained record per stage transition."""
    try:
        from core.paths import tenant_audit_chain  # noqa: PLC0415
        from forge import security_events  # type: ignore  # noqa: PLC0415

        security_events.write_event(
            tenant_audit_chain(tenant), "loop_a.stage", severity="INFO",
            details={"tenant_id": tenant, "stage": stage,
                     **{k: v for k, v in counts.items() if isinstance(v, (int, float, bool))}})
    except Exception as exc:  # noqa: BLE001
        print(f"audit write failed: {type(exc).__name__}", file=sys.stderr)


def ffprobe_duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def ffprobe_streams(path: Path) -> set[str]:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return {x.strip() for x in out.stdout.splitlines() if x.strip()}


# ── TTS ──────────────────────────────────────────────────────────────────────

def _tts_openai(text: str, dest: Path) -> bool:
    try:
        sys.path.insert(0, str(REPO))
        from core.voice.tts_providers import OpenAITTSProvider  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return False
    prov = OpenAITTSProvider()
    if not prov.is_available():
        return False
    res = asyncio.run(prov.synthesize(text, voice="en-US-AvaMultilingualNeural"))
    if not res or not res.audio_bytes:
        return False
    dest.write_bytes(res.audio_bytes)
    return ffprobe_duration(dest) > 0


def _tts_espeak(text: str, dest: Path) -> bool:
    espeak = shutil.which("espeak-ng")
    if not espeak:
        return False
    wav = dest.with_suffix(".wav")
    r1 = subprocess.run([espeak, "-v", "en-us", "-s", "155", "-w", str(wav), text], capture_output=True)
    if r1.returncode != 0 or not wav.is_file():
        return False
    r2 = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(wav), "-codec:a", "libmp3lame",
                         "-q:a", "3", str(dest)], capture_output=True)
    wav.unlink(missing_ok=True)
    return r2.returncode == 0 and ffprobe_duration(dest) > 0


TTS_PROVIDERS: list[tuple[str, Callable[[str, Path], bool]]] = [("openai", _tts_openai), ("espeak-ng", _tts_espeak)]


def stage_tts(concept: dict, out: Path, board: Board, providers=None) -> bool:
    """Narration audio for every scene; resolved concept with fitted lengths."""
    providers = providers or TTS_PROVIDERS
    audio = out / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    manifest_p = audio / "manifest.json"
    manifest = json.loads(manifest_p.read_text()) if manifest_p.is_file() else {}
    scenes = concept["scenes"]
    board.update(TASKS["tts"], status="running")
    for i, sc in enumerate(scenes):
        dest = audio / f"{sc['name']}.mp3"
        if not sc.get("narration"):
            manifest[sc["name"]] = {"provider": None, "duration_s": 0.0}
            continue
        if dest.is_file() and ffprobe_duration(dest) > 0 and sc["name"] in manifest:
            continue
        used = None
        for name, fn in providers:
            try:
                if fn(sc["narration"], dest):
                    used = name
                    break
            except Exception as exc:  # noqa: BLE001
                print(f"tts {name} failed: {exc}", file=sys.stderr)
        if not used:
            board.update(TASKS["tts"], note=f"TTS failed for scene '{sc['name']}' with every provider")
            return False
        manifest[sc["name"]] = {"provider": used, "duration_s": round(ffprobe_duration(dest), 3)}
        manifest_p.write_text(json.dumps(manifest, indent=2))
        board.update(TASKS["tts"], progress=int((i + 1) / len(scenes) * 90))
    manifest_p.write_text(json.dumps(manifest, indent=2))
    # Fit every scene to its narration: never cut a sentence off.
    resolved = json.loads(json.dumps(concept))
    for sc in resolved["scenes"]:
        spoken = manifest.get(sc["name"], {}).get("duration_s", 0.0)
        if spoken > 0:  # a silent scene keeps its planned length
            sc["duration_s"] = round(max(sc["duration_s"], spoken + NARRATION_TAIL_S), 2)
    raw = _as_yaml_concept(resolved)
    import yaml  # noqa: PLC0415
    (out / "concept.resolved.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    providers_used = sorted({m["provider"] for m in manifest.values() if m["provider"]})
    board.update(TASKS["tts"], status="done", progress=100,
                 note=f"Narration for {len(scenes)} scenes via {', '.join(providers_used)}; "
                      f"scene lengths fitted to the narration ({sum(s['duration_s'] for s in resolved['scenes']):.1f} s total)")
    audit(board.tenant, "tts_done", scenes=len(scenes))
    return True


def _as_yaml_concept(c: dict) -> dict:
    return {"name": c["name"], "fps": c["fps"], "render_settings": c["render_settings"],
            "scenes": [{k: s[k] for k in ("name", "template", "template_params", "duration_s",
                                          "camera_angle", "narration")} for s in c["scenes"]]}


# ── Render ───────────────────────────────────────────────────────────────────

def _valid_frame(p: Path) -> bool:
    try:
        with p.open("rb") as fh:
            return fh.read(4) == gen.EXR_MAGIC and p.stat().st_size > 1024
    except OSError:
        return False


def _ranges(nums: list[int]) -> list[tuple[int, int]]:
    """[1,2,3,7,8] → [(1,3),(7,8)]"""
    out: list[tuple[int, int]] = []
    for n in nums:
        if out and n == out[-1][1] + 1:
            out[-1] = (out[-1][0], n)
        else:
            out.append((n, n))
    return out


def render_plan(concept: dict, out: Path) -> list[tuple[str, int, int]]:
    """Missing frame ranges per scene, as (scene, first, last)."""
    todo = []
    for sc in concept["scenes"]:
        total = max(1, int(round(sc["duration_s"] * concept["fps"])))
        missing = [f for f in range(1, total + 1)
                   if not _valid_frame(out / "frames" / sc["name"] / f"frame_{f:04d}.exr")]
        todo += [(sc["name"], a, b) for a, b in _ranges(missing)]
    return todo


def frame_totals(concept: dict, out: Path) -> tuple[int, int]:
    total = done = 0
    for sc in concept["scenes"]:
        n = max(1, int(round(sc["duration_s"] * concept["fps"])))
        total += n
        done += sum(_valid_frame(out / "frames" / sc["name"] / f"frame_{f:04d}.exr") for f in range(1, n + 1))
    return done, total


def stage_render(resolved_path: Path, out: Path, board: Board, *, deadline: float, threads: int,
                 resolution: int = 100, samples: int = 0, nice: int = 19) -> bool:
    concept = gen.load_concept(resolved_path)
    done, total = frame_totals(concept, out)
    board.update(TASKS["render"], status="running",
                 progress=int(done / total * 100),
                 note=f"{done}/{total} frames rendered (CPU, {threads} threads, nice {nice})")
    for scene, first, last in render_plan(concept, out):
        f = first
        while f <= last:
            if time.time() >= deadline:
                return False
            chunk_end = min(last, f + CHUNK_FRAMES - 1)
            report = gen.build(resolved_path, out=out, render=True, frames=f"{f}-{chunk_end}",
                               resolution=resolution, samples=samples, scene=scene,
                               threads=threads, nice=nice)
            if not report.get("ok"):
                problems = [p for s in report.get("scenes", []) for p in
                            (s.get("pre_render_problems") or []) + (s.get("post_render_problems") or [])]
                board.update(TASKS["render"], note=f"render failed: {report.get('error') or problems[:2]}")
                audit(board.tenant, "render_failed", attempts=int(report.get("attempts", 0)))
                return False
            done, total = frame_totals(concept, out)
            board.update(TASKS["render"], progress=min(99, int(done / total * 100)),
                         note=f"{done}/{total} frames rendered (CPU, {threads} threads, nice {nice})")
            f = chunk_end + 1
    done, total = frame_totals(concept, out)
    if done < total:
        return False
    board.update(TASKS["render"], status="done", progress=100,
                 note=f"All {total} frames rendered and validated (EXR)")
    audit(board.tenant, "render_done", frames=total)
    return True


# ── Compose ──────────────────────────────────────────────────────────────────

def stage_compose(resolved_path: Path, out: Path, board: Board) -> bool:
    concept = gen.load_concept(resolved_path)
    board.update(TASKS["compose"], status="running", progress=5)
    seg_dir = out / "segments"
    seg_dir.mkdir(exist_ok=True)
    segments = []
    for i, sc in enumerate(concept["scenes"]):
        n = max(1, int(round(sc["duration_s"] * concept["fps"])))
        dur = n / concept["fps"]
        seg = seg_dir / f"{i:02d}_{sc['name']}.mp4"
        audio = out / "audio" / f"{sc['name']}.mp3"
        cmd = ["ffmpeg", "-loglevel", "error", "-y",
               # EXR frames are scene-linear: convert to sRGB for H.264.
               "-apply_trc", "iec61966_2_1", "-framerate", str(concept["fps"]),
               "-i", str(out / "frames" / sc["name"] / "frame_%04d.exr")]
        if audio.is_file():
            cmd += ["-i", str(audio)]
        else:
            cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
        cmd += ["-filter_complex", f"[0:v]format=yuv420p[v];[1:a]apad,atrim=0:{dur:.3f},"
                                   f"aresample=44100[a]",
                "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}",
                "-c:v", "libx264", "-crf", "23", "-preset", "medium", "-c:a", "aac", "-b:a", "160k",
                str(seg)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not seg.is_file():
            board.update(TASKS["compose"], note=f"compose failed on scene '{sc['name']}': {r.stderr[-200:]}")
            return False
        segments.append(seg)
        board.update(TASKS["compose"], progress=int((i + 1) / len(concept["scenes"]) * 80))
    listing = seg_dir / "concat.txt"
    listing.write_text("".join(f"file '{s.name}'\n" for s in segments))
    final = out / f"{concept['slug']}.mp4"
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                        "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(final)],
                       capture_output=True, text=True)
    expected = sum(max(1, int(round(s["duration_s"] * concept["fps"]))) for s in concept["scenes"]) / concept["fps"]
    got = ffprobe_duration(final) if final.is_file() else 0.0
    streams = ffprobe_streams(final) if final.is_file() else set()
    if r.returncode != 0 or abs(got - expected) > 0.5 or streams != {"video", "audio"}:
        board.update(TASKS["compose"], note=f"final video check failed: duration {got:.1f}s vs {expected:.1f}s, "
                                            f"streams {sorted(streams)}")
        return False
    board.update(TASKS["compose"], status="done", progress=100,
                 note=f"{final.name}: {got:.1f} s, H.264 + AAC narration, {final.stat().st_size // 1024} KiB")
    audit(board.tenant, "compose_done", duration_s=round(got, 1))
    return True


# ── Human tasks ──────────────────────────────────────────────────────────────

def stage_human(board: Board) -> None:
    for key, why in (("study", "Needs 45 human participants (recruitment, consent, sessions) — "
                               "cannot run autonomously. The video is ready for it."),
                     ("analysis", "Waits for the learning-study data (task #5).")):
        t = board.task(TASKS[key])
        if t and t["status"] not in ("done", "blocked"):
            board.update(TASKS[key], status="blocked", note=why)
            audit(board.tenant, f"{key}_blocked_on_humans")


# ── orchestration ────────────────────────────────────────────────────────────

def run(concept_path: Path, *, out: Path, board: Board, budget_s: int, threads: int,
        resolution: int = 100, samples: int = 0) -> str:
    deadline = time.time() + budget_s
    concept = gen.load_concept(concept_path)
    resolved = out / "concept.resolved.yaml"
    tts = board.task(TASKS["tts"])
    if not resolved.is_file() or (tts and tts["status"] != "done" and board._mod):
        if not stage_tts(concept, out, board):
            return "tts_failed"
    if not stage_render(resolved, out, board, deadline=deadline, threads=threads,
                        resolution=resolution, samples=samples):
        return "rendering"  # budget used up or failed; the next run resumes
    final = out / f"{concept['slug']}.mp4"
    comp = board.task(TASKS["compose"])
    if not final.is_file() or (comp and comp["status"] != "done"):
        if not stage_compose(resolved, out, board):
            return "compose_failed"
    stage_human(board)
    return "waiting_for_humans"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--concept", type=Path, default=REPO / "learning_concepts" / "audit_chain.yaml")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--tenant", default=os.environ.get("CORVIN_TENANT_ID", "_default"))
    ap.add_argument("--initiative", default="loop-a")
    ap.add_argument("--budget-s", type=int, default=480)
    ap.add_argument("--threads", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--resolution", type=int, default=100, help="render resolution percentage")
    ap.add_argument("--samples", type=int, default=0, help="override the concept's samples")
    ap.add_argument("--no-board", action="store_true")
    a = ap.parse_args(argv)
    concept = gen.load_concept(a.concept)
    out = a.out or gen.default_out_dir(concept["slug"])
    out.mkdir(parents=True, exist_ok=True)
    lock = out / ".pipeline.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError:
        try:
            os.kill(int(lock.read_text() or 0), 0)
            print(json.dumps({"state": "already_running"}))
            return 0
        except (ValueError, ProcessLookupError, PermissionError):
            lock.write_text(str(os.getpid()))  # stale lock from a killed run
    try:
        board = Board(a.tenant, a.initiative, enabled=not a.no_board)
        state = run(a.concept, out=out, board=board, budget_s=a.budget_s, threads=a.threads,
                    resolution=a.resolution, samples=a.samples)
        done, total = frame_totals(gen.load_concept(out / "concept.resolved.yaml"), out) \
            if (out / "concept.resolved.yaml").is_file() else (0, 0)
        print(json.dumps({"state": state, "frames": f"{done}/{total}", "out": str(out)}))
        return 0 if state in ("rendering", "waiting_for_humans") else 1
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
