"""DataHub Unified Complete Tests (Phase 1+2)."""

import pytest
from pathlib import Path
import sys
import json
import tempfile

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core" / "skills" / "os_skills"))

from datahub_unified.models import DataSourceType, CreationType, DataIngestion, CreationRequest
from datahub_unified.datahub import DataHubSkill
from datahub_unified.creator import UnifiedCreator


class TestPhase1DataHub:
    """Test Phase 1: DataHub ingestion."""

    def test_ingest_json_data(self):
        """Ingest JSON data."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump([{"id": 1, "name": "test"}], f)
            temp_path = f.name

        try:
            hub = DataHubSkill()
            request = DataIngestion(
                source_type=DataSourceType.JSON,
                source_path=temp_path,
                sample_rows=100
            )

            result = hub.ingest(request)
            assert result
            assert hub.ingested_data is not None
            assert len(hub.ingested_data) > 0
        finally:
            Path(temp_path).unlink()

    def test_infer_schema(self):
        """Infer schema from data."""
        hub = DataHubSkill()
        hub.ingested_data = [
            {"id": 1, "name": "alice", "active": True},
            {"id": 2, "name": "bob", "active": False}
        ]

        schema = hub._infer_schema(hub.ingested_data)

        assert schema["id"] == "number"
        assert schema["name"] == "string"
        assert schema["active"] == "boolean"

    def test_analyze_data(self):
        """Analyze ingested data."""
        hub = DataHubSkill()
        hub.ingested_data = [{"x": i} for i in range(10)]
        hub.schema = {"x": "number"}
        hub.sample_rows = 10

        analysis = hub.analyze()

        assert analysis["row_count"] == 10
        assert analysis["schema"] is not None
        assert 0 <= analysis["completeness"] <= 1


class TestPhase2Creator:
    """Test Phase 2: Unified Creator."""

    def test_create_skill_from_data(self):
        """Create a Skill from data."""
        creator = UnifiedCreator()

        request = CreationRequest(
            data=DataIngestion(
                source_type=DataSourceType.JSON,
                source_path="test.json",
                sample_rows=100
            ),
            creation_type=CreationType.SKILL,
            name="test_skill",
            description="Test skill from data."
        )

        result = creator.create(request)

        assert result.artifact_name == "test_skill"
        assert result.artifact_type == CreationType.SKILL
        assert len(result.artifact_body) > 0
        assert "Pattern" in result.artifact_body

    def test_create_tool_from_data(self):
        """Create a Tool from data."""
        creator = UnifiedCreator()

        request = CreationRequest(
            data=DataIngestion(
                source_type=DataSourceType.CSV,
                source_path="data.csv",
                sample_rows=50
            ),
            creation_type=CreationType.TOOL,
            name="csv_processor",
            description="Process CSV data."
        )

        result = creator.create(request)

        assert result.artifact_type == CreationType.TOOL
        assert "def" in result.artifact_body or "Input" in result.artifact_body

    def test_create_dataset_from_data(self):
        """Create a Dataset from data."""
        creator = UnifiedCreator()

        request = CreationRequest(
            data=DataIngestion(
                source_type=DataSourceType.JSON,
                source_path="data.json",
                sample_rows=1000
            ),
            creation_type=CreationType.DATASET,
            name="large_dataset",
            description="Large dataset."
        )

        result = creator.create(request)

        assert result.artifact_type == CreationType.DATASET
        assert "Schema" in result.artifact_body or "dataset" in result.artifact_body.lower()

    def test_create_pipeline_from_data(self):
        """Create a Pipeline from data."""
        creator = UnifiedCreator()

        request = CreationRequest(
            data=DataIngestion(
                source_type=DataSourceType.API,
                source_path="https://api.example.com/data",
                sample_rows=500
            ),
            creation_type=CreationType.PIPELINE,
            name="data_pipeline",
            description="Pipeline for API data."
        )

        result = creator.create(request)

        assert result.artifact_type == CreationType.PIPELINE
        assert "Stage" in result.artifact_body or "Steps" in result.artifact_body

    def test_complexity_analysis(self):
        """Analyze data complexity."""
        creator = UnifiedCreator()

        request_small = CreationRequest(
            data=DataIngestion(source_type=DataSourceType.JSON, source_path="s.json", sample_rows=50),
            creation_type=CreationType.SKILL,
            name="small",
            description="Small."
        )

        request_large = CreationRequest(
            data=DataIngestion(source_type=DataSourceType.JSON, source_path="l.json", sample_rows=2000),
            creation_type=CreationType.SKILL,
            name="large",
            description="Large."
        )

        complexity_small = creator._analyze_complexity(request_small)
        complexity_large = creator._analyze_complexity(request_large)

        assert complexity_small < complexity_large
        assert 0 <= complexity_small <= 1
        assert 0 <= complexity_large <= 1


class TestE2EPipeline:
    """End-to-end: DataHub + Creator."""

    def test_full_pipeline(self):
        """Complete pipeline: ingest → analyze → create."""

        # Phase 1: Setup data
        data = [
            {"user_id": 1, "email": "alice@example.com", "created": "2026-01-01"},
            {"user_id": 2, "email": "bob@example.com", "created": "2026-01-02"}
        ]

        # Phase 1: Ingest
        hub = DataHubSkill()
        hub.ingested_data = data
        hub.schema = hub._infer_schema(data)
        hub.sample_rows = len(data)

        # Verify ingestion
        analysis = hub.analyze()
        assert analysis["row_count"] == 2

        # Phase 2: Create request
        request = hub.generate_creation_request(
            name="user_skill",
            creation_type=CreationType.SKILL,
            description="Skills about user data."
        )
        assert request is not None

        # Phase 2: Create artifact
        creator = UnifiedCreator()
        result = creator.create(request)

        # Verify result
        assert result.success
        assert result.artifact_name == "user_skill"
        assert len(result.artifact_body) > 100
        assert result.test_count > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
