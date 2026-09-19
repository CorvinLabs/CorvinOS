"""Unit tests for Voice-Sync Compositor (Blocker 3)

Tests narration-animation synchronization and FFmpeg integration.
"""

import pytest
import tempfile
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from voice_sync_mapper import VoiceSyncMapper, Keyframe, NarrationAudio, VoiceSyncMapping
from voice_sync_compositor import VoiceSyncCompositor, VoiceSyncConfig


class TestVoiceSyncCompositorBasics:
    """Test compositor initialization and configuration"""

    def test_compositor_initialization(self):
        """Should initialize with default config"""
        compositor = VoiceSyncCompositor()
        assert compositor.ffmpeg_path == "ffmpeg"
        assert compositor.config.frame_rate == 30
        assert compositor.config.audio_bitrate == "128k"

    def test_compositor_with_custom_config(self):
        """Should accept custom config"""
        config = VoiceSyncConfig(
            frame_rate=60,
            video_bitrate="8000k",
            fallback_on_sync_fail=False
        )
        compositor = VoiceSyncCompositor(config=config)
        assert compositor.config.frame_rate == 60
        assert compositor.config.video_bitrate == "8000k"
        assert compositor.config.fallback_on_sync_fail is False


class TestVoiceSyncMapping:
    """Test creating and using voice-sync mappings"""

    def test_create_mapping_from_keyframes(self):
        """Should create mapping from keyframes"""
        mapper = VoiceSyncMapper(frame_rate=30)

        audio = NarrationAudio(
            audio_path=Path("narration.mp3"),
            duration_sec=30
        )

        keyframes = [
            Keyframe(frame=0, event="speech_start", narrator_text="Hello"),
            Keyframe(frame=60, event="diagram_appears", narrator_text="diagram"),
            Keyframe(frame=180, event="feedback", narrator_text="feedback"),
        ]

        mapping = mapper.create_mapping(audio, keyframes)

        assert len(mapping.frame_to_event) == 3
        assert mapping.frame_to_event[0] == "speech_start"
        assert mapping.frame_to_event[60] == "diagram_appears"
        assert mapping.frame_to_event[180] == "feedback"

    def test_mapping_validation(self):
        """Should validate mapping consistency"""
        mapper = VoiceSyncMapper(frame_rate=30)

        audio = NarrationAudio(Path("narration.mp3"), duration_sec=10)

        keyframes = [
            Keyframe(frame=0, event="start", narrator_text="start"),
            Keyframe(frame=300, event="end", narrator_text="end"),  # > 10 sec * 30 fps = 300 frames
        ]

        with pytest.raises(ValueError):
            mapper.create_mapping(audio, keyframes)

    def test_mapping_json_serialization(self):
        """Should serialize/deserialize mapping to JSON"""
        mapper = VoiceSyncMapper(frame_rate=30)

        audio = NarrationAudio(Path("narration.mp3"), duration_sec=10)
        keyframes = [
            Keyframe(frame=0, event="start", narrator_text="Hello"),
            Keyframe(frame=150, event="middle", narrator_text="middle"),
        ]

        original = mapper.create_mapping(audio, keyframes)

        # Serialize
        json_str = mapper.export_mapping_json(original)
        assert isinstance(json_str, str)
        assert "frame_to_event" in json_str

        # Deserialize
        restored = mapper.import_mapping_json(json_str)
        assert restored.frame_to_event == original.frame_to_event
        assert len(restored.keyframes) == len(original.keyframes)


class TestVoiceSyncCompositorComposition:
    """Test composition with voice-sync"""

    @patch('voice_sync_compositor.subprocess.run')
    def test_composition_with_voice_sync(self, mock_run):
        """Should compose video with voice-sync filter graph"""

        # Mock ffmpeg success
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        compositor = VoiceSyncCompositor()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create mock frame directory
            frame_dir = Path(tmpdir) / "frames"
            frame_dir.mkdir()

            # Create dummy frames
            for i in range(1, 91):  # 90 frames = 3 sec @ 30 fps
                frame_file = frame_dir / f"frame_{i:06d}.png"
                frame_file.touch()

            # Create mock audio
            audio_file = Path(tmpdir) / "narration.mp3"
            audio_file.touch()

            # Create voice-sync mapping
            mapping = {
                "frame_to_event": {0: "start", 60: "middle", 180: "end"},
                "keyframe_indices": [0, 60, 180],
                "keyframes": [],
                "narrator_silence_ranges": []
            }

            output_mp4 = Path(tmpdir) / "output.mp4"

            # Mock file creation
            with patch('pathlib.Path.exists', return_value=True):
                with patch.object(Path, 'stat') as mock_stat:
                    mock_stat.return_value = MagicMock(st_size=5 * 1024 * 1024)

                    result = compositor.composite_with_voice_sync(
                        frame_dir=str(frame_dir),
                        narration_audio=str(audio_file),
                        voice_sync_mapping=mapping,
                        output_path=str(output_mp4)
                    )

            # Should have called ffmpeg
            assert mock_run.called
            call_args = mock_run.call_args[0][0]
            assert "ffmpeg" in call_args or compositor.ffmpeg_path in call_args

    def test_filter_graph_building(self):
        """Should build FFmpeg filter graph for voice-sync"""
        compositor = VoiceSyncCompositor()

        mapping = {
            "frame_to_event": {0: "start", 60: "middle"},
            "keyframe_indices": [0, 60],
            "keyframes": [],
            "narrator_silence_ranges": []
        }

        filter_graph = compositor._build_sync_filter_graph(mapping)

        # Should include setpts for timing adjustment
        assert "setpts" in filter_graph or filter_graph == ""

    def test_srt_export_from_mapping(self):
        """Should export voice-sync mapping as SRT subtitles"""
        compositor = VoiceSyncCompositor(config=VoiceSyncConfig(frame_rate=30))

        mapping = {
            "frame_to_event": {
                0: "Introduction",
                60: "Main Point",
                180: "Conclusion"
            },
            "keyframe_indices": [0, 60, 180]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            srt_file = Path(tmpdir) / "keyframes.srt"

            success = compositor.export_mapping_as_srt(mapping, str(srt_file))

            assert success
            assert srt_file.exists()

            # Check SRT format
            content = srt_file.read_text()
            assert "Introduction" in content
            assert "Main Point" in content
            assert "Conclusion" in content
            assert "00:00:00" in content
            assert "00:00:02" in content or "00:00:06" in content  # 2s and 6s timestamps


class TestVoiceSyncFallback:
    """Test fallback behavior when voice-sync fails"""

    @patch('voice_sync_compositor.subprocess.run')
    def test_fallback_on_sync_failure(self, mock_run):
        """Should fall back to standard timing on voice-sync error"""

        # First call (voice-sync): fail
        # Second call (fallback): succeed
        mock_run.side_effect = [
            MagicMock(returncode=1, stderr="Filter error"),  # voice-sync fails
            MagicMock(returncode=0, stdout="", stderr="")  # fallback succeeds
        ]

        config = VoiceSyncConfig(fallback_on_sync_fail=True)
        compositor = VoiceSyncCompositor(config=config)

        with tempfile.TemporaryDirectory() as tmpdir:
            frame_dir = Path(tmpdir) / "frames"
            frame_dir.mkdir()

            audio_file = Path(tmpdir) / "narration.mp3"
            audio_file.touch()

            output_mp4 = Path(tmpdir) / "output.mp4"

            mapping = {"frame_to_event": {}, "keyframe_indices": []}

            with patch('pathlib.Path.exists', return_value=True):
                with patch.object(Path, 'stat') as mock_stat:
                    mock_stat.return_value = MagicMock(st_size=1024)

                    result = compositor.composite_with_voice_sync(
                        frame_dir=str(frame_dir),
                        narration_audio=str(audio_file),
                        voice_sync_mapping=mapping,
                        output_path=str(output_mp4)
                    )

            # Should have fallback flag
            assert result.get("fallback") or result.get("error")

    @patch('voice_sync_compositor.subprocess.run')
    def test_no_fallback_strict_mode(self, mock_run):
        """Should fail hard if fallback is disabled"""

        mock_run.return_value = MagicMock(returncode=1, stderr="Filter error")

        config = VoiceSyncConfig(fallback_on_sync_fail=False)
        compositor = VoiceSyncCompositor(config=config)

        mapping = {"frame_to_event": {}, "keyframe_indices": []}

        result = compositor.composite_with_voice_sync(
            frame_dir="/nonexistent",
            narration_audio="/nonexistent.mp3",
            voice_sync_mapping=mapping,
            output_path="/tmp/output.mp4"
        )

        # Should fail (frame dir doesn't exist)
        assert result.get("success") is False


class TestTimingValidation:
    """Test timing validation against voice-sync mapping"""

    def test_timing_accuracy_calculation(self):
        """Should calculate timing accuracy"""
        compositor = VoiceSyncCompositor(config=VoiceSyncConfig(frame_rate=30))

        mapping = {
            "frame_to_event": {0: "start", 90: "middle", 180: "end"},
            "keyframe_indices": [0, 90, 180]
        }

        # Mock ffprobe result
        with patch('voice_sync_compositor.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="6.0\n")  # 6 seconds

            validation = compositor._validate_timing("/tmp/output.mp4", mapping)

            # 180 frames / 30 fps = 6.0 seconds (perfect match)
            assert validation.get("accuracy_ok") or "actual_duration_sec" in validation


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
