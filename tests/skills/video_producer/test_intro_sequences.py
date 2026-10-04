"""Tests for branded intro sequences (ADR-2213)."""
import os
import pytest

from core.skills.video_producer.workers.intro_sequences import (
    DEFAULT_DURATION_S,
    LOGO_MARK_SVG_PATH,
    VARIANTS,
    IntroRenderError,
    pick_variant,
    prepend_intro_with_fade,
    render_intro,
)

requires_ffmpeg = pytest.mark.skipif(
    os.system("which ffmpeg > /dev/null 2>&1") != 0, reason="needs ffmpeg"
)
def _playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


requires_playwright = pytest.mark.skipif(not _playwright_available(), reason="needs playwright")


def test_logo_mark_asset_exists():
    """The intro system must point at a real, existing brand asset — never
    an invented placeholder."""
    assert LOGO_MARK_SVG_PATH.exists(), f"logo mark missing at {LOGO_MARK_SVG_PATH}"
    content = LOGO_MARK_SVG_PATH.read_text(encoding="utf-8")
    assert "<svg" in content


def test_at_least_five_distinct_variants():
    assert len(VARIANTS) >= 5
    ids = [v.id for v in VARIANTS]
    assert len(ids) == len(set(ids)), "variant ids must be unique"


def test_pick_variant_is_deterministic():
    """Same job id -> same variant, every time (reproducibility)."""
    a = pick_variant("job_abc123")
    b = pick_variant("job_abc123")
    assert a.id == b.id


def test_pick_variant_spreads_across_different_seeds():
    """Different job ids should not all collapse onto the same variant."""
    seeds = [f"job_{i}" for i in range(20)]
    chosen = {pick_variant(s).id for s in seeds}
    assert len(chosen) > 1, "pick_variant must distribute across more than one variant"


@requires_playwright
@requires_ffmpeg
def test_render_intro_produces_valid_mp4(tmp_path):
    variant = VARIANTS[0]
    path = render_intro(variant, str(tmp_path), duration_s=2.0, width=640, height=360)
    assert os.path.exists(path)
    assert os.path.getsize(path) > 1000  # not an empty/corrupt file

    import subprocess
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    assert "video" in result.stdout


@requires_playwright
@requires_ffmpeg
def test_prepend_intro_with_fade_produces_combined_video(tmp_path):
    """E2E: render a real intro, crossfade it with a real short main video,
    verify duration is roughly intro + main - fade_overlap."""
    import subprocess

    variant = VARIANTS[1]
    intro_path = render_intro(variant, str(tmp_path), duration_s=2.0, width=640, height=360)

    # Build a tiny real main video (2s solid color + silent audio) to fade into
    main_path = str(tmp_path / "main.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=640x360:d=2",
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", main_path],
        capture_output=True, text=True, timeout=30, check=True,
    )

    output_path = str(tmp_path / "combined.mp4")
    result = prepend_intro_with_fade(intro_path, main_path, output_path, fade_duration_s=0.5)
    assert os.path.exists(result)

    duration_check = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", output_path],
        capture_output=True, text=True,
    )
    combined_duration = float(duration_check.stdout.strip())
    # intro(~2s) + main(2s) - fade(0.5s) ~= 3.5s, allow slack for Playwright's
    # own timing variance (measured: wait_for_timeout(2000ms) can yield a
    # slightly shorter captured clip than requested)
    assert 2.5 < combined_duration < 4.5


def test_render_intro_raises_cleanly_on_missing_asset(tmp_path, monkeypatch):
    """Fail-closed: a missing logo asset must raise IntroRenderError, not
    silently render a blank or crash with an unrelated exception."""
    import core.skills.video_producer.workers.intro_sequences as mod
    monkeypatch.setattr(mod, "LOGO_MARK_SVG_PATH", tmp_path / "does_not_exist.svg")
    with pytest.raises(IntroRenderError, match="not found"):
        mod._read_logo_svg()
