"""Generate the real narration audio for the CorvinOS demo video.

Real edge-tts synthesis (Microsoft Neural TTS, no API key) -- same engine +
calling convention as core/skills/workers/voice_synthesizer/synthesizer.py.
Writes narration.mp3 + narration_duration.json (real ffprobe-measured
duration in seconds, fps=25 assumed) alongside this script, so
build_scene.py can calibrate the Blender scene's frame_end to the ACTUAL
audio length instead of guessing.

Run: python3 generate_narration.py
"""
import asyncio
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT_TEXT = (HERE / "narration_script.txt").read_text().strip()
VOICE = "en-US-AriaNeural"
FPS = 25


async def synthesize() -> Path:
    if os.environ.get("CORVIN_TTS_LOCAL_ONLY") == "1":
        raise RuntimeError("edge-tts disabled under CORVIN_TTS_LOCAL_ONLY=1")

    import edge_tts

    out_path = HERE / "narration.mp3"
    communicate = edge_tts.Communicate(SCRIPT_TEXT, VOICE)
    await asyncio.wait_for(communicate.save(str(out_path)), timeout=30)

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError("edge-tts produced no audio")
    return out_path


def measure_duration(mp3_path: Path) -> float:
    import ffmpeg

    probe = ffmpeg.probe(str(mp3_path))
    return float(probe["format"]["duration"])


def main() -> int:
    mp3_path = asyncio.run(synthesize())
    duration_s = measure_duration(mp3_path)
    frame_end = round(duration_s * FPS)

    config = {
        "duration_s": duration_s,
        "fps": FPS,
        "frame_end": frame_end,
        "voice": VOICE,
        "audio_file": mp3_path.name,
    }
    (HERE / "narration_duration.json").write_text(json.dumps(config, indent=2))

    print(f"Narration: {mp3_path} ({duration_s:.2f}s, frame_end={frame_end} @ {FPS}fps)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
