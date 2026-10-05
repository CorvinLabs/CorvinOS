"""build_av_crossfade_chain keeps picture and voice aligned across joins.

Each synthetic scene is a solid colour with a beep 0.5 s in. After the chain,
every beep must start exactly where its scene's picture is on screen — the
property the old video-only crossfade + concatenated audio broke by
transition_duration per join.
"""
import os
import subprocess

import pytest
from PIL import Image

from core.skills.video_producer.workers.transitions import build_av_crossfade_chain, get_clip_duration

pytestmark = pytest.mark.skipif(os.system("which ffmpeg > /dev/null 2>&1") != 0, reason="needs ffmpeg")

COLORS = [("red", (255, 0, 0)), ("green", (0, 128, 0)), ("blue", (0, 0, 255))]
DURATIONS = [3.0, 4.0, 2.5]


def _clip(path, color, dur):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color={color}:s=320x180:r=25:d={dur}",
         "-f", "lavfi", "-i", f"sine=f=1000:d=0.3,adelay=500:all=1,apad=whole_dur={dur}",
         "-shortest", "-c:v", "libx264", "-c:a", "aac", str(path)],
        check=True,
    )


def test_beeps_land_on_their_own_scene(tmp_path):
    clips = []
    for i, ((name, _), dur) in enumerate(zip(COLORS, DURATIONS)):
        clips.append(str(tmp_path / f"c{i}.mp4"))
        _clip(clips[-1], name, dur)
    ds = [get_clip_duration(c) for c in clips]
    t = 0.6
    out = str(tmp_path / "out.mp4")
    total = build_av_crossfade_chain(clips, out, ["slideleft", "circleopen"], t, width=320, height=180)
    assert total == pytest.approx(sum(ds) + 2 * t, abs=0.08)

    r = subprocess.run(["ffmpeg", "-i", out, "-af", "silencedetect=n=-30dB:d=0.1", "-f", "null", "-"],
                       capture_output=True, text=True)
    onsets = [float(l.split("silence_end: ")[1].split()[0]) for l in r.stderr.splitlines() if "silence_end" in l]
    starts = [sum(ds[:i]) + i * t for i in range(3)]
    assert len(onsets) >= 3
    for onset, start in zip(onsets, starts):
        assert onset == pytest.approx(start + 0.5, abs=0.06)

    for i, start in enumerate(starts):
        frame = tmp_path / f"f{i}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start + 0.52}", "-i", out,
                        "-frames:v", "1", str(frame)], check=True)
        px = Image.open(frame).convert("RGB").getpixel((160, 90))
        want = COLORS[i][1]
        assert all(abs(a - b) < 40 for a, b in zip(px, want)), (i, px, want)


def test_rejects_unknown_transition(tmp_path):
    with pytest.raises(ValueError):
        build_av_crossfade_chain(["a", "b"], str(tmp_path / "o.mp4"), ["not-a-transition"])
