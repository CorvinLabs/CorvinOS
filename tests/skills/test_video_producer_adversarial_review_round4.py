"""Adversarial review round 4 -- against the video_producer consolidation
session (2026-09-26): real ffmpeg wiring, real edge-tts synthesis, honest
YouTube local-only mode, Director Mode removal, console panel wiring.

Rounds 1-3 (test_video_producer_adversarial_review_round{1,2,3_final}.py)
targeted `core/skills/video_producer/` (path 5, the generic topic->video
generator) -- out of scope here; that implementation is being deprecated in
favour of `core/skills/os_skills/video_producer/orchestrator.py` (path 1),
which is what this session's real fixes landed in.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from core.skills.workers.video_assembler.filter_graph import FilterGraph
from core.skills.os_skills.video_producer.types import Scene, Storyboard


class TestFilterGraphInjection:
    """Vector: scene.narration is untrusted (may come from an
    LLM-generated storyboard) and used to be interpolated into an ffmpeg
    filter_complex string with only quote-escaping -- ffmpeg's filtergraph
    description language treats `:`, `,`, `[`, `]`, `;` as syntax too."""

    def test_narration_with_filtergraph_special_chars_never_reaches_the_filter_string(self):
        malicious = "Hello, world: this][is;a\\test'of\"escaping"

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            slides_dir = project_dir / "slides"
            audio_dir = project_dir / "audio"
            slides_dir.mkdir()
            audio_dir.mkdir()

            scene = Scene(id="s01", kind="card", narration=malicious, captions=True)
            storyboard = Storyboard(metadata={}, scenes=[scene])
            fg = FilterGraph(storyboard, slides_dir, audio_dir)

            filter_str = fg._get_caption_filter(malicious[:50], "s01")

            assert malicious not in filter_str, "narration leaked into the filtergraph string"
            assert "textfile=" in filter_str
            caption_file = slides_dir.parent / "captions" / "s01.txt"
            assert caption_file.read_text() == malicious[:50]

    def test_unsafe_scene_id_is_rejected(self):
        """scene.id becomes a bare filtergraph LABEL ([scaled_<id>]) with no
        quoting at all -- a `]`/`;`/`:` in it corrupts the graph outright."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            slides_dir = project_dir / "slides"
            audio_dir = project_dir / "audio"
            slides_dir.mkdir()
            audio_dir.mkdir()

            scene = Scene(id="s01];evil_filter=[", kind="card", narration="x", captions=False)
            storyboard = Storyboard(metadata={}, scenes=[scene])
            fg = FilterGraph(storyboard, slides_dir, audio_dir)

            with pytest.raises(ValueError, match="unsafe scene id"):
                import asyncio
                asyncio.run(fg.build())


class TestYouTubeLocalOnlyMode:
    """Vector: does the "no credentials" default path ever report a fake
    success, or does it honestly refuse?"""

    @pytest.mark.asyncio
    async def test_no_credentials_never_fabricates_a_video_id(self):
        from core.skills.workers.youtube_uploader.youtube_api import YouTubeAPI

        api = YouTubeAPI()  # no token, no CORVIN_YOUTUBE_TOKEN_PATH
        result = await api.upload_video(
            video_path="/nonexistent.mp4", title="t", description="d", tags=[],
        )
        assert result["status"] == "not_configured"
        assert result["video_id"] is None
        assert result["video_id"] != "dQw4w9WgXcQ"  # the old hardcoded fake id

    @pytest.mark.asyncio
    async def test_enqueue_refuses_without_credentials(self):
        from core.skills.workers.youtube_uploader import YouTubeUploader

        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "output.mp4"
            video_path.write_bytes(b"fake mp4 bytes")
            uploader = YouTubeUploader(tmpdir)  # no oauth_token
            result = await uploader.enqueue_upload(video_path, {"title": "t"})
            assert result["status"] == "error"


class TestDirectorModeRemoval:
    """Vector: ADR-0696 claimed IMPLEMENTED status for code with zero
    production call sites. Confirm it's actually gone, not just undocumented."""

    def test_both_director_mode_trees_are_gone(self):
        repo_root = Path(__file__).resolve().parents[2]
        assert not (repo_root / "core/skills/os_skills/video_producer/src/director").exists()
        assert not (repo_root / "core/skills/director_mode").exists()
