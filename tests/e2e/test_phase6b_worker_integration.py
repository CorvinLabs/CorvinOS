"""Phase 6b generic video-producer workers — fail-closed contract.

Until 2026-09-27 these workers lived in ``core/skills/video_producer/workers.py``,
shadowed by the ``workers/`` package (so this file failed at collection), and
each one reported ``status="completed"`` for work it never did: a TTS file that
was never written, a screenshot never captured, an encode ffmpeg never ran and
a ``youtube.com/watch?v=corvin_<hash>`` URL for an upload that never happened.
They have no backend, so every execution must FAIL with a ``not_implemented``
error and health must be ``not_measured`` — never ``healthy``.
Ref: ADR-0206 Phase 6 Milestone 2; adversarial review 2026-09-27.
"""
import pytest

from core.skills.video_producer.orchestrator import StoryboardFrame, VideoOrchestrator
from core.skills.video_producer.workers import (
    NOT_IMPLEMENTED,
    WORKERS,
    FFmpegWorker,
    ScreenshotWorker,
    TTSWorker,
    WorkerResult,
    YouTubeWorker,
    get_worker,
)

VALID_INPUT = {
    "tts": {"text": "Hello, world", "output_format": "mp3"},
    "screenshot": {"url": "https://example.com", "format": "png"},
    "ffmpeg": {"input_paths": ["audio.mp3", "video.mp4"], "output_path": "out.mp4"},
    "youtube": {"video_path": "final.mp4", "title": "My Video"},
}


@pytest.mark.parametrize("worker_type", sorted(WORKERS))
def test_valid_input_fails_closed_without_fabricated_output(worker_type):
    worker = get_worker(worker_type)
    result = worker.execute({**VALID_INPUT[worker_type], "frame_id": "f-1"})
    assert result.status == "failed"
    assert result.error.startswith(NOT_IMPLEMENTED)
    assert result.output is None  # no invented path / URL / video id
    assert result.frame_id == "f-1"
    assert worker.execution_count == 1
    assert worker.last_result is result


@pytest.mark.parametrize("worker_type", sorted(WORKERS))
def test_health_is_not_measured_never_healthy(worker_type):
    worker = get_worker(worker_type)
    assert worker.health_check()["status"] == "not_measured"
    worker.execute(VALID_INPUT[worker_type])
    assert worker.health_check()["status"] == "not_measured"


def test_youtube_never_returns_a_watch_url():
    result = YouTubeWorker().execute({"video_path": "final.mp4", "title": "T"})
    assert "youtube.com" not in repr(result)


@pytest.mark.parametrize(
    "worker,bad_input,error",
    [
        (TTSWorker(), {}, "Missing text input"),
        (FFmpegWorker(), {}, "Missing input paths"),
        (YouTubeWorker(), {}, "Missing video path"),
    ],
)
def test_input_validation_still_reports_the_specific_error(worker, bad_input, error):
    result = worker.execute(bad_input)
    assert result.status == "failed"
    assert result.error == error


def test_registry_and_lookup():
    assert set(WORKERS) == {"tts", "screenshot", "ffmpeg", "youtube"}
    assert isinstance(get_worker("screenshot"), ScreenshotWorker)
    with pytest.raises(ValueError, match="Unknown worker type"):
        get_worker("invalid")


def test_worker_result_defaults():
    r = WorkerResult(worker_id="tts", frame_id="f", status="failed", error="x")
    assert r.output is None and r.created_at


def test_orchestrator_dispatches_to_workers_and_fails_closed():
    orch = VideoOrchestrator("integration-test")
    for i, wt in enumerate(sorted(WORKERS)):
        orch.add_frame(StoryboardFrame(
            frame_id=f"frame-{wt}", timestamp=float(i), description=wt,
            worker_type=wt, worker_input=VALID_INPUT[wt],
            created_at="2026-09-26T00:00:00",
        ))
    results = [orch.execute_frame(c) for c in orch.build_execution_plan()]
    assert [r["status"] for r in results] == ["failed"] * len(WORKERS)
    assert all(r["errors"][0].startswith(NOT_IMPLEMENTED) for r in results)
    events = [e["event"] for e in orch.execution_trace]
    assert "frame_execution_completed" not in events
    assert events.count("frame_execution_failed") == len(WORKERS)
