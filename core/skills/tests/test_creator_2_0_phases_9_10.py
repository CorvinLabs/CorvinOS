"""Tests for Creator 2.0 phases 9–10 (Packaging, Delivery)."""

import pytest
from core.skills.os_skills.creator_2_0.phases.phase_9 import PackagingPhase, PackagingRequest
from core.skills.os_skills.creator_2_0.phases.phase_10 import DeliveryPhase, DeliveryRequest
from core.skills.os_skills.creator_2_0.events import LossEmitter


class TestPackagingPhase:
    """Test Phase 9: Packaging."""

    def test_packaging_creates_manifest(self):
        """Test that packaging creates skill manifest."""
        emitter = LossEmitter()
        phase = PackagingPhase()

        result = phase.execute(
            PackagingRequest(
                skill_id="test_skill",
                description="Test skill",
                intent="validation",
                file_layout={"__init__.py": "init", "skill.py": "main"},
            ),
            emitter,
        )

        assert result.manifest is not None
        assert "id" in result.manifest
        assert "version" in result.manifest
        assert "description" in result.manifest

    def test_packaging_generates_package_name(self):
        """Test that packaging generates package name."""
        emitter = LossEmitter()
        phase = PackagingPhase()

        result = phase.execute(
            PackagingRequest(
                skill_id="test_skill",
                description="Test",
                intent="validation",
                file_layout={},
            ),
            emitter,
        )

        assert result.package_name == "test-skill"

    def test_packaging_sets_version(self):
        """Test that packaging sets initial version."""
        emitter = LossEmitter()
        phase = PackagingPhase()

        result = phase.execute(
            PackagingRequest(
                skill_id="test",
                description="Test",
                intent="validation",
                file_layout={},
            ),
            emitter,
        )

        assert result.version == "0.1.0"

    def test_packaging_creates_zip_path(self):
        """Test that packaging creates ZIP path."""
        emitter = LossEmitter()
        phase = PackagingPhase()

        result = phase.execute(
            PackagingRequest(
                skill_id="test_skill",
                description="Test",
                intent="validation",
                file_layout={},
            ),
            emitter,
        )

        assert "test-skill" in result.zip_path
        assert ".zip" in result.zip_path

    def test_packaging_manifest_has_required_fields(self):
        """Test that manifest has all required fields."""
        emitter = LossEmitter()
        phase = PackagingPhase()

        result = phase.execute(
            PackagingRequest(
                skill_id="test",
                description="Test",
                intent="validation",
                file_layout={},
            ),
            emitter,
        )

        required_fields = ["id", "type", "version", "description", "entry_point"]
        assert all(f in result.manifest for f in required_fields)

    def test_packaging_manifest_type_is_skill(self):
        """Test that manifest type is 'skill'."""
        emitter = LossEmitter()
        phase = PackagingPhase()

        result = phase.execute(
            PackagingRequest(
                skill_id="test",
                description="Test",
                intent="validation",
                file_layout={},
            ),
            emitter,
        )

        assert result.manifest["type"] == "skill"

    def test_packaging_emits_event(self):
        """Test that packaging emits event."""
        emitter = LossEmitter()
        phase = PackagingPhase()

        phase.execute(
            PackagingRequest(
                skill_id="test",
                description="Test",
                intent="validation",
                file_layout={},
            ),
            emitter,
        )

        events = emitter.get_events()
        assert len(events) == 1
        assert events[0].phase_num == 9


class TestDeliveryPhase:
    """Test Phase 10: Delivery."""

    def test_delivery_finalizes_manifest(self):
        """Test that delivery finalizes manifest."""
        emitter = LossEmitter()
        phase = DeliveryPhase()

        initial_manifest = {"id": "test", "version": "0.1.0"}

        result = phase.execute(
            DeliveryRequest(
                skill_id="test",
                manifest=initial_manifest,
                version="0.1.0",
                package_name="test",
            ),
            emitter,
        )

        assert "generated_at" in result.final_manifest
        assert "status" in result.final_manifest
        assert result.final_manifest["status"] == "ready"

    def test_delivery_sets_status_ready(self):
        """Test that delivery sets status to ready."""
        emitter = LossEmitter()
        phase = DeliveryPhase()

        result = phase.execute(
            DeliveryRequest(
                skill_id="test",
                manifest={},
                version="0.1.0",
                package_name="test",
            ),
            emitter,
        )

        assert result.delivery_status == "ready"

    def test_delivery_lists_artifacts(self):
        """Test that delivery lists artifacts."""
        emitter = LossEmitter()
        phase = DeliveryPhase()

        result = phase.execute(
            DeliveryRequest(
                skill_id="test_skill",
                manifest={},
                version="0.1.0",
                package_name="test-skill",
            ),
            emitter,
        )

        assert len(result.artifacts) > 0
        assert any("skill" in a for a in result.artifacts)

    def test_delivery_includes_tests_in_artifacts(self):
        """Test that delivery includes test artifacts."""
        emitter = LossEmitter()
        phase = DeliveryPhase()

        result = phase.execute(
            DeliveryRequest(
                skill_id="test",
                manifest={},
                version="0.1.0",
                package_name="test",
            ),
            emitter,
        )

        assert any("test" in a for a in result.artifacts)

    def test_delivery_includes_manifest_in_artifacts(self):
        """Test that delivery includes manifest."""
        emitter = LossEmitter()
        phase = DeliveryPhase()

        result = phase.execute(
            DeliveryRequest(
                skill_id="test",
                manifest={},
                version="0.1.0",
                package_name="test",
            ),
            emitter,
        )

        assert any("manifest" in a.lower() for a in result.artifacts)

    def test_delivery_emits_event(self):
        """Test that delivery emits event."""
        emitter = LossEmitter()
        phase = DeliveryPhase()

        phase.execute(
            DeliveryRequest(
                skill_id="test",
                manifest={},
                version="0.1.0",
                package_name="test",
            ),
            emitter,
        )

        events = emitter.get_events()
        assert len(events) == 1
        assert events[0].phase_num == 10

    def test_delivery_preserves_original_manifest_fields(self):
        """Test that delivery preserves original manifest fields."""
        emitter = LossEmitter()
        phase = DeliveryPhase()

        original = {
            "id": "test",
            "version": "0.1.0",
            "description": "Test skill",
        }

        result = phase.execute(
            DeliveryRequest(
                skill_id="test",
                manifest=original,
                version="0.1.0",
                package_name="test",
            ),
            emitter,
        )

        assert result.final_manifest["id"] == "test"
        assert result.final_manifest["description"] == "Test skill"
