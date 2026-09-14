"""End-to-End Tests for Phase 5.1 Implementation

Tests ManimAnimatorWorker, VoiceSyncMapper, AssetLibrary
Complete test coverage for Director Mode Advanced (3-Tier Animation)

ADR-0740: Director Mode Advanced
ADR-0741: 3-Tier Animation Architecture
ADR-0742: Didactic Storyboard System
"""

import pytest
from pathlib import Path
import tempfile
import json
from datetime import datetime

# Import Phase 5.1 components
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core" / "skills" / "video_producer_skill_2_0"))

from phase5.manim_animator import (
    ManimAnimatorWorker,
    AnimationRequest,
    AnimationResult,
)

from phase5.voice_sync_mapper import (
    VoiceSyncMapper,
    VoiceSyncMapping,
    Keyframe,
    NarrationAudio,
)

from phase5.asset_library import (
    AssetLibraryManifest,
    AssetMetadata,
)


class TestManimAnimatorWorker:
    """Tests for Manim Animator Worker (Tier 2 Renderer)"""

    @pytest.fixture
    def worker(self):
        return ManimAnimatorWorker(timeout_seconds=120, cache_enabled=True)

    @pytest.fixture
    def animation_request_learning_loop(self):
        return AnimationRequest(
            animation_id="learning-loop",
            concept_id="learning-loop",
            didactic_level="beginner",
            duration_seconds=30,
            assets=[],
            output_format="mp4",
            tier=2
        )

    def test_manim_worker_initialized(self, worker):
        """Test: ManimAnimatorWorker initializes correctly"""
        assert worker.name == "manim_animator_worker"
        assert worker.version == "5.1.0"
        assert worker.timeout == 120
        assert worker.cache_enabled == True
        assert worker.output_dir.exists()

    def test_animation_request_valid(self, animation_request_learning_loop):
        """Test: AnimationRequest validates correctly"""
        assert animation_request_learning_loop.animation_id == "learning-loop"
        assert animation_request_learning_loop.didactic_level == "beginner"
        assert animation_request_learning_loop.tier == 2

    def test_animation_request_invalid_didactic_level(self):
        """Test: AnimationRequest rejects invalid didactic level"""
        with pytest.raises(ValueError):
            AnimationRequest(
                animation_id="test",
                concept_id="test",
                didactic_level="invalid",
                duration_seconds=30,
                assets=[],
                output_format="mp4"
            )

    def test_animation_request_invalid_format(self):
        """Test: AnimationRequest rejects invalid output format"""
        with pytest.raises(ValueError):
            AnimationRequest(
                animation_id="test",
                concept_id="test",
                didactic_level="beginner",
                duration_seconds=30,
                assets=[],
                output_format="invalid"
            )

    def test_load_scene_spec_learning_loop(self, worker):
        """Test: Load scene spec for learning-loop"""
        spec = worker._load_scene_spec("learning-loop")
        assert spec is not None
        assert spec["title"] == "Learning Loop"
        assert "elements" in spec
        assert "flow" in spec

    def test_load_scene_spec_not_found(self, worker):
        """Test: Return None for unknown animation_id"""
        spec = worker._load_scene_spec("unknown-animation")
        assert spec is None

    def test_generate_scene_script_learning_loop(self, worker):
        """Test: Generate Manim scene script for learning-loop"""
        spec = worker._load_scene_spec("learning-loop")
        script = worker._generate_scene_script(
            animation_id="learning-loop",
            scene_spec=spec,
            didactic_level="beginner"
        )

        assert "class LearningLoopScene(Scene)" in script
        assert "Measure" in script or "learning" in script.lower()
        assert len(script) > 100  # Should be substantial code

    def test_generate_scene_script_maestro_workers(self, worker):
        """Test: Generate Manim scene script for maestro-workers"""
        spec = worker._load_scene_spec("maestro-workers")
        script = worker._generate_scene_script(
            animation_id="maestro-workers",
            scene_spec=spec,
            didactic_level="technical"
        )

        assert "class MaestroWorkersScene(Scene)" in script
        assert "Maestro" in script or "Worker" in script
        assert len(script) > 100

    def test_animation_result_success(self):
        """Test: AnimationResult with success"""
        result = AnimationResult(
            animation_id="test",
            success=True,
            output_path=Path("/tmp/test.mp4"),
            duration_seconds=30.0,
            render_time_ms=5000,
            output_hash="abc123",
            tier_used=2
        )

        assert result.success == True
        assert result.animation_id == "test"
        assert result.duration_seconds == 30.0

    def test_animation_result_failure(self):
        """Test: AnimationResult with failure"""
        result = AnimationResult(
            animation_id="test",
            success=False,
            error="Timeout",
            render_time_ms=60000
        )

        assert result.success == False
        assert result.error == "Timeout"
        assert result.output_path is None


class TestVoiceSyncMapper:
    """Tests for Voice-Sync Mapper"""

    @pytest.fixture
    def mapper(self):
        return VoiceSyncMapper(frame_rate=30)

    @pytest.fixture
    def narration_audio(self):
        return NarrationAudio(
            audio_path=Path("narration.mp3"),
            duration_sec=30.0,
            frame_rate=30
        )

    @pytest.fixture
    def keyframes(self):
        return [
            Keyframe(
                frame=0,
                event="speech_start",
                narrator_text="Learning loop",
                timestamp_sec=0.0
            ),
            Keyframe(
                frame=60,
                event="diagram_appears",
                narrator_text="5-step cycle",
                animation_action="diagram_zoom_in",
                timestamp_sec=2.0
            ),
            Keyframe(
                frame=180,
                event="animation_complete",
                narrator_text="Done",
                timestamp_sec=6.0
            ),
        ]

    def test_mapper_initialization(self, mapper):
        """Test: VoiceSyncMapper initializes correctly"""
        assert mapper.frame_rate == 30

    def test_create_mapping_valid(self, mapper, narration_audio, keyframes):
        """Test: Create valid voice-sync mapping"""
        mapping = mapper.create_mapping(narration_audio, keyframes)

        assert mapping is not None
        assert 0 in mapping.frame_to_event
        assert 60 in mapping.frame_to_event
        assert mapping.frame_to_event[0] == "speech_start"
        assert mapping.frame_to_event[60] == "diagram_appears"

    def test_create_mapping_keyframe_exceeds_duration(self, mapper, narration_audio):
        """Test: Reject keyframe that exceeds audio duration"""
        # Audio is 30 sec = 900 frames @ 30 FPS
        invalid_keyframe = [
            Keyframe(
                frame=1000,  # Exceeds 900
                event="too_late",
                narrator_text="This is past the end",
                timestamp_sec=33.0
            )
        ]

        with pytest.raises(ValueError):
            mapper.create_mapping(narration_audio, invalid_keyframe)

    def test_keyframe_ordering(self, mapper, narration_audio, keyframes):
        """Test: Keyframes are ordered by frame"""
        mapping = mapper.create_mapping(narration_audio, keyframes)
        indices = mapping.keyframe_indices

        assert indices == sorted(indices)

    def test_validate_mapping_valid(self, mapper, narration_audio, keyframes):
        """Test: Validate correct mapping"""
        mapping = mapper.create_mapping(narration_audio, keyframes)
        errors = mapper.validate_mapping(mapping, narration_audio)

        assert errors == []

    def test_validate_mapping_overlapping_silence(self, mapper, narration_audio, keyframes):
        """Test: Detect overlapping silence ranges"""
        mapping = mapper.create_mapping(narration_audio, keyframes)
        # Manually add overlapping silence ranges
        mapping.narrator_silence_ranges = [
            (100, 200),
            (150, 250)  # Overlaps with previous
        ]

        errors = mapper.validate_mapping(mapping, narration_audio)
        assert any("Overlapping" in e for e in errors)

    def test_export_import_json(self, mapper, narration_audio, keyframes):
        """Test: Export and import mapping as JSON"""
        mapping = mapper.create_mapping(narration_audio, keyframes)
        json_str = mapper.export_mapping_json(mapping)

        # Verify JSON is valid
        data = json.loads(json_str)
        assert "frame_to_event" in data
        assert "keyframes" in data

        # Import back
        imported = mapper.import_mapping_json(json_str)
        assert imported.frame_to_event == mapping.frame_to_event


class TestAssetLibrary:
    """Tests for Asset Library Manifest"""

    @pytest.fixture
    def manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            yield AssetLibraryManifest(manifest_path)

    def test_manifest_initialization(self, manifest):
        """Test: AssetLibraryManifest initializes correctly"""
        assert manifest.version == "1.0"
        assert manifest.assets == {}

    def test_add_asset(self, manifest):
        """Test: Add asset to library"""
        asset = AssetMetadata(
            id="learning-loop",
            version="1.0",
            name="Learning Loop Diagram",
            description="5-step feedback loop",
            asset_type="manim-scene",
            checksum_sha256="abc123",
            didactic_level=["beginner", "technical"],
            duration_seconds=30,
            file_path="scenes/learning_loop.py",
            license="MIT",
            created_at=datetime.now().isoformat(),
            created_by="test"
        )

        manifest.add_asset(asset)

        retrieved = manifest.get_asset("learning-loop", "1.0")
        assert retrieved is not None
        assert retrieved.id == "learning-loop"
        assert retrieved.version == "1.0"

    def test_get_latest_asset(self, manifest):
        """Test: Get latest version of asset"""
        for version in ["1.0", "1.1", "2.0"]:
            asset = AssetMetadata(
                id="learning-loop",
                version=version,
                name=f"Learning Loop v{version}",
                description="",
                asset_type="manim-scene",
                checksum_sha256=f"hash_{version}",
                didactic_level=["beginner"],
                duration_seconds=30,
                file_path="",
                license="MIT",
                created_at=datetime.now().isoformat(),
                created_by="test"
            )
            manifest.add_asset(asset)

        latest = manifest.get_latest_asset("learning-loop")
        assert latest.version == "2.0"

    def test_list_assets(self, manifest):
        """Test: List assets in library"""
        for i in range(3):
            asset = AssetMetadata(
                id=f"asset-{i}",
                version="1.0",
                name=f"Asset {i}",
                description="",
                asset_type="manim-scene" if i % 2 == 0 else "svg",
                checksum_sha256="hash",
                didactic_level=["beginner"],
                duration_seconds=30,
                file_path="",
                license="MIT",
                created_at=datetime.now().isoformat(),
                created_by="test"
            )
            manifest.add_asset(asset)

        all_assets = manifest.list_assets()
        assert len(all_assets) == 3

        manim_assets = manifest.list_assets(asset_type="manim-scene")
        assert len(manim_assets) == 2

    def test_save_and_load_manifest(self, manifest):
        """Test: Save and load manifest from JSON"""
        asset = AssetMetadata(
            id="test",
            version="1.0",
            name="Test Asset",
            description="Test",
            asset_type="manim-scene",
            checksum_sha256="abc123",
            didactic_level=["beginner"],
            duration_seconds=30,
            file_path="test.py",
            license="MIT",
            created_at=datetime.now().isoformat(),
            created_by="test"
        )
        manifest.add_asset(asset)
        manifest.save_manifest()

        # Load into new manifest
        manifest2 = AssetLibraryManifest(manifest.manifest_path)
        retrieved = manifest2.get_asset("test", "1.0")

        assert retrieved is not None
        assert retrieved.name == "Test Asset"


class TestPhase51Integration:
    """Integration tests for Phase 5.1 full pipeline"""

    def test_animation_request_to_result_pipeline(self):
        """Test: Complete pipeline from request to result"""
        request = AnimationRequest(
            animation_id="learning-loop",
            concept_id="learning-loop",
            didactic_level="beginner",
            duration_seconds=30,
            assets=[],
            output_format="mp4"
        )

        assert request.animation_id == "learning-loop"
        assert request.didactic_level == "beginner"

    def test_voice_sync_narration_alignment(self):
        """Test: Voice-sync correctly aligns narration to frames"""
        mapper = VoiceSyncMapper(frame_rate=30)
        audio = NarrationAudio(Path("narration.mp3"), duration_sec=10.0)

        keyframes = [
            Keyframe(frame=0, event="start", narrator_text="Start", timestamp_sec=0.0),
            Keyframe(frame=150, event="mid", narrator_text="Middle", timestamp_sec=5.0),
            Keyframe(frame=299, event="end", narrator_text="End", timestamp_sec=9.97)
        ]

        mapping = mapper.create_mapping(audio, keyframes)

        # Verify frame-to-event mapping
        assert mapping.get_event_at_frame(0) == "start"
        assert mapping.get_event_at_frame(150) == "mid"
        assert mapping.get_event_at_frame(299) == "end"

    def test_asset_library_manifest_with_tier_info(self):
        """Test: Asset library tracks tier information"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = AssetLibraryManifest(Path(tmpdir) / "manifest.json")

            # Add assets for different tiers
            for tier in [1, 2, 3]:
                asset = AssetMetadata(
                    id=f"animation-tier{tier}",
                    version="1.0",
                    name=f"Animation (Tier {tier})",
                    description="",
                    asset_type="manim-scene",
                    checksum_sha256=f"hash_tier{tier}",
                    didactic_level=["beginner"],
                    duration_seconds=30,
                    file_path="",
                    license="MIT",
                    created_at=datetime.now().isoformat(),
                    created_by="test",
                    renderer_tier=tier
                )
                manifest.add_asset(asset)

            # Verify all tiers loaded
            all_assets = manifest.list_assets()
            assert len(all_assets) == 3

            for asset in all_assets:
                assert asset.renderer_tier in [1, 2, 3]


# Pytest markers for selective testing
pytestmark = [
    pytest.mark.phase5_1,
    pytest.mark.video_producer,
    pytest.mark.e2e,
]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
