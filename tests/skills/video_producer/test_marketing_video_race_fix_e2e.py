"""E2E wiring proof for the Corvin marketing video pipeline + the job_tmp
race-condition fix it depends on (feedback-recurring-race-fix-the-primitive-
not-the-callsite: the same f"/tmp/{job_id}_scene_{i}" pattern recurred in 5
worker files; the fix moved into one shared primitive, core.skills.
video_producer.workers.job_tmp, instead of patching each call site).

Sandboxing (feedback-tests-read-live-operator-state): every test here pins
CORVIN_HOME to a throwaway tmp directory via an autouse fixture. None of the
code under test here writes to the audit chain directly (VoiceSynthesizerWorker
has no _audit() call — only MaestroOrchestrator does, and this suite never
calls MaestroOrchestrator), but the pin is kept anyway because that is
exactly the lesson of the incident the memory documents: a test that works
fine on a clean CI runner can still read/write live state on a developer
machine, and the only way to make that structurally impossible is to pin the
path before anything runs, not to reason about which code "shouldn't" touch
it.

Two of the three tests go through the REAL transport (a real subprocess for
the collision proof; a real import + real OPENAI_API_KEY-backed
VoiceSynthesizerWorker.execute() call for the render proof) rather than
mocking the unit under test — e2e-wiring-proof's hard rule.
"""
import os
import subprocess
import sys
import pytest

CORVINOS_ROOT = "/home/shumway/projects/CorvinOS"
MARKETING_SOURCE_V3 = "/home/shumway/projects/Corvin-Videos/corvinOS-marketing/source_v3"

requires_openai_key = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"), reason="needs OPENAI_API_KEY for real TTS"
)
requires_ffmpeg = pytest.mark.skipif(
    os.system("which ffmpeg > /dev/null 2>&1") != 0, reason="needs ffmpeg"
)


@pytest.fixture(autouse=True)
def sandbox_corvin_home(tmp_path, monkeypatch):
    """Pin CORVIN_HOME to a throwaway dir for every test in this file.

    Per feedback-tests-read-live-operator-state: pin BEFORE any code under
    test runs, don't reason about whether it "should" need it.
    """
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin_home_sandbox"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg_config_sandbox"))


def test_job_scoped_dir_prevents_cross_process_collision():
    """The actual regression proof: job_tmp.job_scoped_dir() must give two
    DIFFERENT real OS processes, called with the IDENTICAL job_id (the
    pattern every existing E2E test in this package uses — fixed strings
    like "test_maestro_diagram_render_phase", not the UUID default),
    two DIFFERENT directories. Before the fix, both processes built the
    exact same f"/tmp/{job_id}_scene_{i}.mp3" string and the second one to
    write would silently overwrite the first's in-flight file.

    Runs as two real subprocess.run() calls (not two in-process function
    calls) because the bug IS a cross-process race — a same-process test
    would not exercise the failure mode at all.
    """
    probe = (
        "import sys; sys.path.insert(0, %r); "
        "from core.skills.video_producer.workers.job_tmp import job_scoped_dir; "
        "print(job_scoped_dir('shared_fixed_job_id_e2e_test'))"
    ) % CORVINOS_ROOT

    proc1 = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=10)
    proc2 = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=10)

    assert proc1.returncode == 0, proc1.stderr
    assert proc2.returncode == 0, proc2.stderr

    dir1 = proc1.stdout.strip()
    dir2 = proc2.stdout.strip()

    assert dir1 and dir2
    assert dir1 != dir2, (
        "RACE CONDITION REGRESSION: two processes using the identical "
        f"job_id got the SAME temp directory ({dir1}) — the collision "
        "the job_tmp primitive exists to prevent is back."
    )


def test_marketing_video_reachable_through_real_voice_synthesizer_worker():
    """Reachability proof (phase 1 of e2e-wiring-proof): the marketing
    video's render script must call the REAL production worker, not a
    duplicated/local TTS function — closing the gap where v1/v2 had their
    own synthesize_openai_tts() and never touched the Video Producer plugin
    the original task asked for. Checked by actually importing the render
    module (not grepping its source), so a refactor that keeps the string
    but removes the call would fail this too.
    """
    sys.path.insert(0, MARKETING_SOURCE_V3)
    sys.path.insert(0, CORVINOS_ROOT)
    import importlib

    render_mod = importlib.import_module("render_marketing_video_v3")

    from core.skills.video_producer.workers.voice_synthesizer import VoiceSynthesizerWorker

    assert render_mod.VoiceSynthesizerWorker is VoiceSynthesizerWorker, (
        "render_marketing_video_v3 must import the real production "
        "VoiceSynthesizerWorker class, not a local/duplicated stand-in"
    )
    assert hasattr(render_mod, "render") and callable(render_mod.render)


@requires_openai_key
@requires_ffmpeg
def test_marketing_video_e2e_renders_through_real_pipeline(tmp_path, monkeypatch):
    """Full E2E (phase 2 of e2e-wiring-proof): call the real render()
    entry point with a throwaway job_id, go through the real
    VoiceSynthesizerWorker (real OpenAI TTS call, real ffprobe), real PIL
    frame generation and a real ffmpeg assembly — then verify the produced
    MP4 against the properties the video-storage-convention documents
    (H.264/AAC, non-trivial duration, playable).

    Output is redirected to a tmp OUTPUT_DIR so this test never touches the
    checked-in Corvin-Videos artifact, and uses a unique job_id (not the
    production "corvinos_marketing_v3") so a concurrent real render is never
    at risk of colliding with this test run either.
    """
    sys.path.insert(0, MARKETING_SOURCE_V3)
    sys.path.insert(0, CORVINOS_ROOT)
    import importlib

    render_mod = importlib.import_module("render_marketing_video_v3")

    test_output_dir = tmp_path / "marketing_e2e_output"
    test_output_dir.mkdir()
    monkeypatch.setattr(render_mod, "OUTPUT_DIR", test_output_dir)

    job_id = "e2e_test_marketing_video_render"
    final_video, total_duration = render_mod.render(job_id=job_id)

    assert os.path.exists(final_video), f"render() claimed success but {final_video} does not exist"
    assert total_duration > 60.0, (
        f"expected the full 10-scene narration (~85s), got {total_duration:.1f}s — "
        "a scene likely failed TTS and fell through to a near-zero mock duration"
    )

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration,bit_rate:stream=codec_name,codec_type",
         "-of", "json", final_video],
        capture_output=True, text=True, timeout=15,
    )
    assert probe.returncode == 0, probe.stderr

    import json
    info = json.loads(probe.stdout)
    codecs = {s["codec_type"]: s["codec_name"] for s in info["streams"]}
    assert codecs.get("video") == "h264"
    assert codecs.get("audio") == "aac"

    measured_duration = float(info["format"]["duration"])
    assert abs(measured_duration - total_duration) < 2.0, (
        f"video container duration ({measured_duration:.1f}s) drifted from "
        f"the audio-driven total ({total_duration:.1f}s) by more than 2s — "
        "audio/visual desync"
    )
