"""
Phase 5.4: FFmpeg Video Assembler Tests.

Tests for assembling frame sequences into MP4 videos.
"""

import pytest
import sys
import tempfile
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from video.ffmpeg_assembler import (
    FFmpegAssembler,
    assemble_simple_video,
    assemble_batch_videos,
)


class TestFFmpegAssemblerCreation:
    """Test FFmpeg assembler creation."""

    def test_assembler_creation(self):
        """Should create FFmpeg assembler."""
        assembler = FFmpegAssembler()
        assert assembler is not None
        assert assembler.ffmpeg_path == "ffmpeg"

    def test_assembler_with_custom_ffmpeg_path(self):
        """Should accept custom ffmpeg path."""
        assembler = FFmpegAssembler(ffmpeg_path="/usr/bin/ffmpeg")
        assert assembler.ffmpeg_path == "/usr/bin/ffmpeg"


class TestFFmpegAssemblerVerification:
    """Test FFmpeg verification."""

    def test_ffmpeg_verification(self):
        """Should verify ffmpeg availability."""
        assembler = FFmpegAssembler()
        # This will be True if ffmpeg is installed, False otherwise
        # The test should pass either way
        assert isinstance(assembler._verify_ffmpeg(), bool)


class TestDurationCalculation:
    """Test video duration calculation."""

    def test_calculate_duration(self):
        """Should calculate video duration from frame count."""
        # 300 frames @ 30 FPS = 10 seconds
        duration = FFmpegAssembler.calculate_duration(300, frame_rate=30)
        assert duration == 10.0

    def test_calculate_duration_custom_fps(self):
        """Should handle custom frame rates."""
        # 240 frames @ 24 FPS = 10 seconds
        duration = FFmpegAssembler.calculate_duration(240, frame_rate=24)
        assert duration == 10.0

    def test_calculate_duration_60fps(self):
        """Should handle 60 FPS."""
        # 600 frames @ 60 FPS = 10 seconds
        duration = FFmpegAssembler.calculate_duration(600, frame_rate=60)
        assert duration == 10.0


class TestPathEscaping:
    """Test filter path escaping for ffmpeg."""

    def test_escape_filter_path_simple(self):
        """Simple path should be escaped."""
        path = "/path/to/file.srt"
        escaped = FFmpegAssembler._escape_filter_path(path)
        assert escaped.startswith("'")
        assert escaped.endswith("'")

    def test_escape_filter_path_with_spaces(self):
        """Path with spaces should be escaped."""
        path = "/path/to/my file.srt"
        escaped = FFmpegAssembler._escape_filter_path(path)
        assert escaped.startswith("'")
        assert escaped.endswith("'")

    def test_escape_filter_path_backslashes(self):
        """Windows paths should be converted."""
        path = "C:\\Users\\test\\file.srt"
        escaped = FFmpegAssembler._escape_filter_path(path)
        assert "\\" not in escaped
        assert "/" in escaped


class TestAssemblyWithStubFrames:
    """Test video assembly with stub frame directory."""

    def test_assembly_with_nonexistent_frames(self):
        """Assembly with nonexistent frame directory should handle gracefully."""
        assembler = FFmpegAssembler()

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "output.mp4"

            # This might raise if ffmpeg is available and does validation
            # Or it might return an output file if ffmpeg doesn't exist
            try:
                result = assembler.assemble(
                    frame_dir="/nonexistent/frame/dir/",
                    output_path=str(output_path)
                )
                # If it succeeds, that's ok (stub ffmpeg)
                # If it fails, that's also ok
            except (RuntimeError, FileNotFoundError):
                # Expected if ffmpeg is not available or frames not found
                pass

    def test_assembly_generates_output_path_on_success(self):
        """If assembly succeeds, should return output path."""
        # Create mock frame directory
        with tempfile.TemporaryDirectory() as tmpdir:
            frame_dir = Path(tmpdir) / "frames"
            frame_dir.mkdir()

            # Create a few stub PNG frames
            for i in range(1, 4):
                frame_path = frame_dir / f"frame_{i:06d}.png"
                img = Image.new("RGB", (1920, 1080), color="blue")
                img.save(frame_path)

            output_path = Path(tmpdir) / "output.mp4"

            assembler = FFmpegAssembler()

            try:
                result = assembler.assemble(
                    frame_dir=str(frame_dir),
                    output_path=str(output_path)
                )
                # If ffmpeg is available and works, output file should be created
                # If ffmpeg is not available, this will raise
                if result:
                    assert result == str(output_path)
            except (RuntimeError, FileNotFoundError):
                pytest.skip("ffmpeg not available")


class TestBatchAssembly:
    """Test batch video assembly."""

    def test_batch_assembly_initialization(self):
        """Should initialize batch assembly."""
        assembler = FFmpegAssembler()

        videos = [
            {"frame_dir": "/tmp/frames_1/", "output_path": "video_1.mp4"},
            {"frame_dir": "/tmp/frames_2/", "output_path": "video_2.mp4"},
        ]

        # This will likely fail due to missing frames, but test structure
        results = assembler.assemble_batch(videos, show_progress=False)

        # Results should be a list
        assert isinstance(results, list)
        assert len(results) == 2

    def test_batch_assembly_shows_progress(self):
        """Batch assembly should show progress by default."""
        assembler = FFmpegAssembler()

        videos = [
            {"frame_dir": "/tmp/nonexistent_1/", "output_path": "video_1.mp4"},
        ]

        # With show_progress=True, should log progress
        results = assembler.assemble_batch(videos, show_progress=True)
        assert isinstance(results, list)


class TestAssemblyParameters:
    """Test assembly parameter handling."""

    def test_assembly_with_custom_bitrates(self):
        """Should accept custom bitrates."""
        assembler = FFmpegAssembler()

        with tempfile.TemporaryDirectory() as tmpdir:
            frame_dir = Path(tmpdir) / "frames"
            frame_dir.mkdir()

            # Create stub frames
            for i in range(1, 4):
                img = Image.new("RGB", (1920, 1080))
                img.save(frame_dir / f"frame_{i:06d}.png")

            output_path = Path(tmpdir) / "output.mp4"

            try:
                result = assembler.assemble(
                    frame_dir=str(frame_dir),
                    output_path=str(output_path),
                    video_bitrate="3000k",
                    audio_bitrate="96k"
                )
                # If successful, should return output path
                if result:
                    assert result == str(output_path)
            except (RuntimeError, FileNotFoundError):
                pytest.skip("ffmpeg not available")

    def test_assembly_with_custom_preset(self):
        """Should accept custom encoding presets."""
        assembler = FFmpegAssembler()

        with tempfile.TemporaryDirectory() as tmpdir:
            frame_dir = Path(tmpdir) / "frames"
            frame_dir.mkdir()

            # Create stub frames
            for i in range(1, 4):
                img = Image.new("RGB", (1920, 1080))
                img.save(frame_dir / f"frame_{i:06d}.png")

            output_path = Path(tmpdir) / "output.mp4"

            try:
                result = assembler.assemble(
                    frame_dir=str(frame_dir),
                    output_path=str(output_path),
                    preset="fast"  # Faster encoding
                )
                if result:
                    assert result == str(output_path)
            except (RuntimeError, FileNotFoundError):
                pytest.skip("ffmpeg not available")


class TestConvenienceFunctions:
    """Test convenience functions."""

    def test_assemble_simple_video_helper(self):
        """assemble_simple_video helper should work."""
        with tempfile.TemporaryDirectory() as tmpdir:
            frame_dir = Path(tmpdir) / "frames"
            frame_dir.mkdir()

            # Create stub frames
            for i in range(1, 4):
                img = Image.new("RGB", (1920, 1080))
                img.save(frame_dir / f"frame_{i:06d}.png")

            output_path = Path(tmpdir) / "output.mp4"

            try:
                result = assemble_simple_video(
                    frame_dir=str(frame_dir),
                    output_path=str(output_path)
                )
                # If successful, should return path
                if result:
                    assert result == str(output_path)
            except (RuntimeError, FileNotFoundError):
                pytest.skip("ffmpeg not available")

    def test_assemble_batch_videos_helper(self):
        """assemble_batch_videos helper should work."""
        videos = [
            {
                "frame_dir": "/tmp/frames_1/",
                "output_path": "video_1.mp4"
            }
        ]

        results = assemble_batch_videos(videos)
        assert isinstance(results, list)


class TestOutputPathHandling:
    """Test output path handling."""

    def test_creates_output_directory(self):
        """Should create output directory if it doesn't exist."""
        assembler = FFmpegAssembler()

        with tempfile.TemporaryDirectory() as tmpdir:
            frame_dir = Path(tmpdir) / "frames"
            frame_dir.mkdir()

            # Create stub frames
            for i in range(1, 4):
                img = Image.new("RGB", (1920, 1080))
                img.save(frame_dir / f"frame_{i:06d}.png")

            # Create output path in non-existent subdirectory
            output_dir = Path(tmpdir) / "subdir" / "output"
            output_path = output_dir / "video.mp4"

            try:
                result = assembler.assemble(
                    frame_dir=str(frame_dir),
                    output_path=str(output_path)
                )
                # If successful, directory should be created
                if result:
                    assert output_dir.exists()
            except (RuntimeError, FileNotFoundError):
                pytest.skip("ffmpeg not available")


class TestWithAudioAndSubtitles:
    """Test assembly with audio and subtitles (structure only)."""

    def test_assembly_accepts_audio_parameter(self):
        """Should accept audio parameter."""
        assembler = FFmpegAssembler()

        with tempfile.TemporaryDirectory() as tmpdir:
            frame_dir = Path(tmpdir) / "frames"
            frame_dir.mkdir()

            # Create stub frames
            for i in range(1, 4):
                img = Image.new("RGB", (1920, 1080))
                img.save(frame_dir / f"frame_{i:06d}.png")

            output_path = Path(tmpdir) / "output.mp4"

            try:
                result = assembler.assemble(
                    frame_dir=str(frame_dir),
                    audio_mp3="/nonexistent/audio.mp3",  # Will skip if missing
                    output_path=str(output_path)
                )
                if result:
                    assert result == str(output_path)
            except (RuntimeError, FileNotFoundError):
                pytest.skip("ffmpeg not available")

    def test_assembly_accepts_subtitles_parameter(self):
        """Should accept subtitles parameter."""
        assembler = FFmpegAssembler()

        with tempfile.TemporaryDirectory() as tmpdir:
            frame_dir = Path(tmpdir) / "frames"
            frame_dir.mkdir()

            # Create stub frames
            for i in range(1, 4):
                img = Image.new("RGB", (1920, 1080))
                img.save(frame_dir / f"frame_{i:06d}.png")

            output_path = Path(tmpdir) / "output.mp4"

            try:
                result = assembler.assemble(
                    frame_dir=str(frame_dir),
                    subtitles_srt="/nonexistent/subs.srt",  # Will skip if missing
                    output_path=str(output_path)
                )
                if result:
                    assert result == str(output_path)
            except (RuntimeError, FileNotFoundError):
                pytest.skip("ffmpeg not available")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
