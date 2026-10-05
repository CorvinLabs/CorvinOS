"""render_blender_intro's contract: callers branch on the result, never catch.

A render past its time budget used to raise TimeoutExpired straight out of
the worker and crashed the knowledge-graph render that relied on the
fallback. It must come back as available=True, success=False.
"""
import subprocess

from core.skills.video_producer.workers import blender_renderer as br


def test_timeout_is_a_result_not_an_exception(monkeypatch, tmp_path):
    monkeypatch.setattr(br, "blender_available", lambda: True)

    def _slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(br.subprocess, "run", _slow)
    r = br.render_blender_intro("t", str(tmp_path / "x.mp4"), num_frames=10, timeout_s=1)
    assert r.available is True and r.success is False
    assert "exceeded" in r.error


def test_resolution_percentage_is_bounded(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setattr(br, "blender_available", lambda: True)
    with pytest.raises(ValueError):
        br.render_blender_intro("t", str(tmp_path / "x.mp4"), resolution_percentage=0)
