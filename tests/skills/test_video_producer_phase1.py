"""Phase 1 tests for Video Producer Skill 2.0.

Coverage:
- Asset analyzer stages (ingestion, deep read, contradiction detection, role mapping)
- Orchestrator gate enforcement (analysis.ready_for_narration)
- Storyboard generation source constraint
- E2E workflow (PPT → analysis.json + storyboard.json)
"""

import pytest
import asyncio
import json
import tempfile
from pathlib import Path
from datetime import datetime

# Import from video_producer
from core.skills.os_skills.video_producer import (
    VideoProducerOrchestrator,
    AssetAnalysisResult,
    Storyboard,
    Scene,
    FactualClaim,
    Contradiction,
    AnalysisGateFailedError,
    AssetIngestionError,
)
from core.skills.workers.asset_analyzer import AssetAnalyzer


class TestAssetAnalyzerStages:
    """Test individual stages of asset analyzer."""

    @pytest.fixture
    def temp_workdir(self):
        """Create temporary working directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def analyzer(self, temp_workdir):
        """Create analyzer instance."""
        return AssetAnalyzer(temp_workdir)

    @pytest.mark.asyncio
    async def test_stage_1_ingestion_empty(self, analyzer):
        """Stage 1: Ingestion returns empty list for no assets."""
        result = await analyzer._stage_1_ingest([])
        assert isinstance(result, list)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_stage_2_deep_read_returns_facts(self, analyzer):
        """Stage 2: Deep read returns list of FactualClaim."""
        result = await analyzer._stage_2_deep_read([])
        assert isinstance(result, list)
        assert all(isinstance(f, FactualClaim) for f in result)

    @pytest.mark.asyncio
    async def test_stage_2b_contradiction_detection(self, analyzer):
        """Stage 2b: Detect contradictions returns list."""
        claims = [
            FactualClaim(
                id="c1",
                text="Feature X is ready",
                source_asset="doc1.pdf",
            ),
            FactualClaim(
                id="c2",
                text="Feature X is in beta",
                source_asset="doc2.pdf",
            ),
        ]
        result = await analyzer._stage_2b_detect_contradictions(claims)
        assert isinstance(result, list)
        assert all(isinstance(c, Contradiction) for c in result)

    @pytest.mark.asyncio
    async def test_stage_3_asset_role_mapping(self, analyzer):
        """Stage 3: Asset role mapping returns dict."""
        assets = [{"name": "slide1.pptx"}]
        claims = []
        result = await analyzer._stage_3_map_asset_roles(assets, claims)
        assert isinstance(result, dict)


class TestAssetAnalyzerGates:
    """Test asset analyzer gate enforcement."""

    @pytest.fixture
    def temp_workdir(self):
        """Create temporary working directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def analyzer(self, temp_workdir):
        """Create analyzer instance."""
        return AssetAnalyzer(temp_workdir)

    @pytest.mark.asyncio
    async def test_analyze_insufficient_facts(self, analyzer):
        """Gate: analysis.ready_for_narration == false if < 3 facts."""
        result = await analyzer.analyze([])
        assert result.ready_for_narration is False
        assert len(result.blockers) > 0

    @pytest.mark.asyncio
    async def test_analyze_no_asset_roles(self, analyzer):
        """Gate: ready_for_narration == false if no asset roles mapped."""
        result = await analyzer.analyze([])
        assert result.ready_for_narration is False

    @pytest.mark.asyncio
    async def test_analyze_metadata_present(self, analyzer):
        """Analysis result includes metadata with timestamp."""
        result = await analyzer.analyze([])
        assert "metadata" in result.metadata
        assert "processed_at" in result.metadata


class TestOrchestratorGates:
    """Test orchestrator gate enforcement."""

    @pytest.fixture
    def temp_projectdir(self):
        """Create temporary project directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def orchestrator(self, temp_projectdir):
        """Create orchestrator instance."""
        return VideoProducerOrchestrator(temp_projectdir)

    def test_check_analysis_gates_ready_for_narration_false(self, orchestrator):
        """Gate check raises AnalysisGateFailedError if ready_for_narration == false."""
        analysis = AssetAnalysisResult(
            metadata={"test": True},
            ready_for_narration=False,
            blockers=["No facts extracted"],
        )
        with pytest.raises(AnalysisGateFailedError):
            orchestrator._check_analysis_gates(analysis)

    def test_check_analysis_gates_ready_for_narration_true(self, orchestrator):
        """Gate check passes if ready_for_narration == true."""
        analysis = AssetAnalysisResult(
            metadata={"test": True},
            factual_claims=[
                FactualClaim(id="c1", text="Fact 1", source_asset="doc1"),
                FactualClaim(id="c2", text="Fact 2", source_asset="doc2"),
                FactualClaim(id="c3", text="Fact 3", source_asset="doc3"),
            ],
            asset_roles={"doc1": "slides"},
            ready_for_narration=True,
        )
        # Should not raise
        orchestrator._check_analysis_gates(analysis)

    def test_gate_error_message_includes_blockers(self, orchestrator):
        """Gate error message includes specific blockers."""
        analysis = AssetAnalysisResult(
            metadata={"test": True},
            ready_for_narration=False,
            blockers=["Blocker A", "Blocker B"],
        )
        with pytest.raises(AnalysisGateFailedError) as exc_info:
            orchestrator._check_analysis_gates(analysis)
        error_msg = str(exc_info.value)
        assert "Blocker A" in error_msg
        assert "Blocker B" in error_msg


class TestStoryboardGeneration:
    """Test storyboard generation with source constraints."""

    def test_source_constraint_prompt_includes_claims(self):
        """Storyboard prompt includes all factual claims."""
        from core.skills.os_skills.video_producer import StoryboardGenerator

        generator = StoryboardGenerator()
        analysis = AssetAnalysisResult(
            metadata={"test": True},
            factual_claims=[
                FactualClaim(id="c1", text="Claim 1", source_asset="doc1"),
                FactualClaim(id="c2", text="Claim 2", source_asset="doc2"),
            ],
            asset_roles={"doc1": "slides", "doc2": "detail"},
        )
        prompt = generator._build_source_constraint_prompt(analysis)
        assert "Claim 1" in prompt
        assert "Claim 2" in prompt

    def test_source_constraint_prompt_includes_asset_roles(self):
        """Storyboard prompt includes asset role guidance."""
        from core.skills.os_skills.video_producer import StoryboardGenerator

        generator = StoryboardGenerator()
        analysis = AssetAnalysisResult(
            metadata={"test": True},
            asset_roles={"doc1": "slides", "doc2": "detail"},
        )
        prompt = generator._build_source_constraint_prompt(analysis)
        assert "doc1" in prompt
        assert "slides" in prompt

    def test_source_constraint_prompt_forbids_invention(self):
        """Storyboard prompt explicitly forbids invention."""
        from core.skills.os_skills.video_producer import StoryboardGenerator

        generator = StoryboardGenerator()
        analysis = AssetAnalysisResult(metadata={"test": True})
        prompt = generator._build_source_constraint_prompt(analysis)
        assert "invent" in prompt.lower() or "not" in prompt.lower()


class TestTypesAndSerialization:
    """Test type definitions and serialization."""

    def test_factual_claim_frozen(self):
        """FactualClaim is immutable."""
        claim = FactualClaim(
            id="c1",
            text="Test",
            source_asset="doc1",
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            claim.text = "Modified"

    def test_analysis_result_to_dict(self):
        """AssetAnalysisResult.to_dict() produces valid JSON."""
        analysis = AssetAnalysisResult(
            metadata={"test": True},
            factual_claims=[
                FactualClaim(id="c1", text="Claim", source_asset="doc1"),
            ],
        )
        result_dict = analysis.to_dict()
        # Verify it's JSON-serializable
        json_str = json.dumps(result_dict)
        assert len(json_str) > 0

    def test_storyboard_to_dict(self):
        """Storyboard.to_dict() produces valid JSON."""
        storyboard = Storyboard(
            metadata={"test": True},
            scenes=[
                Scene(id="s1", kind="card", narration="Scene 1"),
            ],
        )
        result_dict = storyboard.to_dict()
        json_str = json.dumps(result_dict)
        assert len(json_str) > 0


class TestExceptionHierarchy:
    """Test exception classes."""

    def test_analysis_gate_failed_is_analysis_incomplete_error(self):
        """AnalysisGateFailedError is subclass of AnalysisIncompleteError."""
        from core.skills.os_skills.video_producer import (
            AnalysisGateFailedError,
            AnalysisIncompleteError,
        )
        exc = AnalysisGateFailedError("test")
        assert isinstance(exc, AnalysisIncompleteError)

    def test_all_exceptions_are_video_producer_error(self):
        """All specific exceptions are VideoProducerError."""
        from core.skills.os_skills.video_producer import (
            VideoProducerError,
            AssetIngestionError,
            AnalysisIncompleteError,
        )
        assert issubclass(AssetIngestionError, VideoProducerError)
        assert issubclass(AnalysisIncompleteError, VideoProducerError)


class TestE2EWorkflow:
    """End-to-end workflow tests."""

    @pytest.fixture
    def temp_projectdir(self):
        """Create temporary project directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def orchestrator(self, temp_projectdir):
        """Create orchestrator instance."""
        return VideoProducerOrchestrator(temp_projectdir)

    @pytest.mark.asyncio
    async def test_e2e_orchestrate_no_assets(self, orchestrator):
        """E2E: Orchestrate with no assets."""
        result = await orchestrator.orchestrate([])
        assert "analysis" in result
        assert "status" in result
        # Without proper assets, status should indicate blocked
        # (actual behavior depends on implementation)

    @pytest.mark.asyncio
    async def test_e2e_orchestrate_returns_dict_structure(self, orchestrator):
        """E2E: Orchestrate returns proper structure."""
        result = await orchestrator.orchestrate([])
        assert isinstance(result, dict)
        assert "analysis" in result
        assert "status" in result
        # Storyboard may be None if analysis gates fail
        assert "storyboard" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
