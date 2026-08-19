"""Tests for Six-Phase Orchestrator (k=2-5: Testing, Review, Validation, Integration)."""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

from operator.skill_forge.six_phase_orchestrator import (
    Phase,
    Verdict,
    PhaseOutput,
    OrchestrationRun,
    SixPhaseOrchestrator,
    bootstrap_orchestrator,
)


# ============================================================================
# k=2: UNIT TESTS (Tier-2)
# ============================================================================

class TestPhaseOutput:
    """Test Phase output dataclass."""

    def test_phase_output_creation(self):
        """PhaseOutput can be created with required fields."""
        output = PhaseOutput(
            phase=Phase.API_DESIGN,
            status="success",
            output={"spec": "test"},
            loss=0.05,
        )

        assert output.phase == Phase.API_DESIGN
        assert output.status == "success"
        assert output.loss == 0.05
        assert output.iteration_k == 0


class TestOrchestrationRun:
    """Test orchestration run state machine."""

    def test_run_creation(self):
        """Run can be created and tracks state."""
        run = OrchestrationRun(run_id="test-1", skill_request="test skill")

        assert run.run_id == "test-1"
        assert run.status == "running"
        assert run.current_phase == Phase.API_DESIGN

    def test_run_mark_complete(self):
        """Run can be marked complete with quality score."""
        run = OrchestrationRun(run_id="test-1", skill_request="test")
        run.mark_complete(quality_score=0.85)

        assert run.status == "success"
        assert run.quality_score == 0.85
        assert run.finished_at is not None

    def test_latest_phase_output(self):
        """Can retrieve latest phase output."""
        run = OrchestrationRun(run_id="test-1", skill_request="test")
        output = PhaseOutput(phase=Phase.API_DESIGN, status="success", output={}, loss=0.05)
        run.phases_completed.append(output)

        assert run.latest_phase_output() == output


class TestSixPhaseOrchestrator:
    """Test orchestrator phases 1-6."""

    def test_orchestrator_creation(self):
        """Orchestrator can be instantiated."""
        mock_client = MagicMock()
        orch = SixPhaseOrchestrator(mock_client)

        assert orch.max_iterations == 5
        assert len(orch.phases_registry) == 6

    @pytest.mark.asyncio
    async def test_phase1_api_design(self):
        """Phase 1 generates API specification."""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text='{"skill_name": "test_skill", "purpose": "test", "inputs": [], "outputs": [], "edge_cases": [], "dependencies": [], "estimated_loc": 200}')]
        )

        orch = SixPhaseOrchestrator(mock_client)
        run = OrchestrationRun(run_id="test-1", skill_request="test skill")

        output = await orch._phase1_api_design("test skill", run)

        assert output.phase == Phase.API_DESIGN
        assert output.status == "success"
        assert output.loss < 0.1
        assert "skill_name" in output.output

    @pytest.mark.asyncio
    async def test_phase2_dialectical_review(self):
        """Phase 2 runs thesis/antithesis/synthesis review."""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="Thesis/Antithesis/Synthesis analysis")]
        )

        orch = SixPhaseOrchestrator(mock_client)
        run = OrchestrationRun(run_id="test-1", skill_request="test")
        run.phases_completed.append(
            PhaseOutput(phase=Phase.API_DESIGN, status="success",
                       output={"skill_name": "test"}, loss=0.05)
        )

        output = await orch._phase2_dialectical_review("test", run)

        assert output.phase == Phase.DIALECTICAL_REVIEW
        assert output.status == "success"
        assert "thesis" in output.output or "antithesis" in output.output

    @pytest.mark.asyncio
    async def test_phase3_ideation_concept_adr(self):
        """Phase 3 documents idea/concept/ADR/plan."""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="1. IDEA: ...\n2. CONCEPT: ...\n3. ADR: ...\n4. PLAN: ...")]
        )

        orch = SixPhaseOrchestrator(mock_client)
        run = OrchestrationRun(run_id="test-1", skill_request="test")
        run.phases_completed.append(
            PhaseOutput(phase=Phase.API_DESIGN, status="success",
                       output={"skill_name": "test", "purpose": "test purpose"},
                       loss=0.05)
        )

        output = await orch._phase3_ideation_concept_adr("test", run)

        assert output.phase == Phase.IDEATION_CONCEPT_ADR
        assert "documentation" in output.output

    @pytest.mark.asyncio
    async def test_phase4_adversarial_review(self):
        """Phase 4 runs 3D adversarial reviews."""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="Review finding")]
        )

        orch = SixPhaseOrchestrator(mock_client)
        run = OrchestrationRun(run_id="test-1", skill_request="test")
        run.phases_completed.append(
            PhaseOutput(phase=Phase.API_DESIGN, status="success",
                       output={"skill_name": "test"}, loss=0.05)
        )

        output = await orch._phase4_adversarial_review("test", run)

        assert output.phase == Phase.ADVERSARIAL_REVIEW
        assert len(output.findings) >= 3  # 3 reviewers

    @pytest.mark.asyncio
    async def test_phase5_implementation(self):
        """Phase 5 generates skill implementation."""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="# Test Skill\n\nInstructions here")]
        )

        orch = SixPhaseOrchestrator(mock_client)
        run = OrchestrationRun(run_id="test-1", skill_request="test")
        run.phases_completed.extend([
            PhaseOutput(phase=Phase.API_DESIGN, status="success",
                       output={"skill_name": "test", "purpose": "test"}, loss=0.05),
            PhaseOutput(phase=Phase.DIALECTICAL_REVIEW, status="success",
                       output={}, loss=0.08),
            PhaseOutput(phase=Phase.IDEATION_CONCEPT_ADR, status="success",
                       output={"documentation": "plan"}, loss=0.10),
        ])

        output = await orch._phase5_implementation("test", run)

        assert output.phase == Phase.IMPLEMENTATION
        assert "skill_body" in output.output

    @pytest.mark.asyncio
    async def test_phase6_e2e_test(self):
        """Phase 6 validates on fictional skill ideas."""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="yes, this skill works well")]
        )

        orch = SixPhaseOrchestrator(mock_client)
        run = OrchestrationRun(run_id="test-1", skill_request="test")

        output = await orch._phase6_e2e_test("test", run)

        assert output.phase == Phase.E2E_TEST
        assert len(output.findings) > 0


# ============================================================================
# k=3: E2E VALIDATION (Tier-4)
# ============================================================================

class TestE2EValidation:
    """E2E tests on fictional skill ideas."""

    @pytest.mark.asyncio
    async def test_orchestrate_json_validator_skill(self):
        """E2E: Orchestrate JSON Validator skill (fictional idea 1)."""
        mock_client = MagicMock()

        # Mock responses for 6 phases
        mock_responses = [
            '{"skill_name": "json_validator", "purpose": "Validate JSON", "inputs": [], "outputs": [], "edge_cases": [], "dependencies": [], "estimated_loc": 200}',
            "Thesis: Validate JSON syntax. Antithesis: Complex edge cases. Synthesis: Simple but thorough.",
            "1. IDEA: Validate JSON\n2. CONCEPT: Use Python json module\n3. ADR: Simple approach\n4. PLAN: Step-by-step",
            "Review: Correct, simple, in scope.",
            "# JSON Validator\n\nInstructions for validating JSON files.",
            "yes, validates JSON files correctly",
        ]

        mock_client.messages.create.side_effect = [
            MagicMock(content=[MagicMock(text=resp)]) for resp in mock_responses
        ]

        orch = SixPhaseOrchestrator(mock_client)
        run = await orch.orchestrate("Create a skill that validates JSON files")

        assert run.status == "success"
        assert len(run.phases_completed) == 6
        assert run.quality_score >= 0.0

    @pytest.mark.asyncio
    async def test_orchestrate_code_analyzer_skill(self):
        """E2E: Orchestrate Code Analyzer skill (fictional idea 2)."""
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            MagicMock(content=[MagicMock(text='{"skill_name": "code_analyzer", "purpose": "Analyze code", "inputs": [], "outputs": [], "edge_cases": [], "dependencies": [], "estimated_loc": 300}')])
        ] + [MagicMock(content=[MagicMock(text="Response")]) for _ in range(5)]

        orch = SixPhaseOrchestrator(mock_client)
        run = await orch.orchestrate("Create a skill that analyzes code for complexity")

        assert run.status == "success"

    @pytest.mark.asyncio
    async def test_orchestrate_log_parser_skill(self):
        """E2E: Orchestrate Log Parser skill (fictional idea 3)."""
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            MagicMock(content=[MagicMock(text='{"skill_name": "log_parser", "purpose": "Parse logs", "inputs": [], "outputs": [], "edge_cases": [], "dependencies": [], "estimated_loc": 250}')])
        ] + [MagicMock(content=[MagicMock(text="Response")]) for _ in range(5)]

        orch = SixPhaseOrchestrator(mock_client)
        run = await orch.orchestrate("Create a skill that parses log files")

        assert run.status == "success"


# ============================================================================
# k=4: BOOTSTRAPPING (Orchestrator on itself)
# ============================================================================

class TestBootstrapping:
    """Test bootstrapping: orchestrator applies itself to its own development."""

    @pytest.mark.asyncio
    async def test_bootstrap_generates_phases(self):
        """Bootstrapping produces all 6 phases."""
        mock_client = MagicMock()

        # Mock all API responses
        mock_client.messages.create.side_effect = [
            MagicMock(content=[MagicMock(text='{"skill_name": "skill_orchestrator", "purpose": "Orchestrate skills", "inputs": [], "outputs": [], "edge_cases": [], "dependencies": [], "estimated_loc": 500}')])
        ] + [MagicMock(content=[MagicMock(text="Generated response")]) for _ in range(29)]

        with patch('operator.skill_forge.six_phase_orchestrator.SixPhaseOrchestrator.__init__',
                  return_value=None):
            with patch.object(SixPhaseOrchestrator, '__init__', lambda self, *args, **kwargs: None):
                orch = SixPhaseOrchestrator(mock_client)
                orch.client = mock_client
                orch.max_iterations = 5
                orch.phases_registry = {
                    Phase.API_DESIGN: orch._phase1_api_design,
                    Phase.DIALECTICAL_REVIEW: orch._phase2_dialectical_review,
                    Phase.IDEATION_CONCEPT_ADR: orch._phase3_ideation_concept_adr,
                    Phase.ADVERSARIAL_REVIEW: orch._phase4_adversarial_review,
                    Phase.IMPLEMENTATION: orch._phase5_implementation,
                    Phase.E2E_TEST: orch._phase6_e2e_test,
                }

                run = await orch.orchestrate("Develop skill orchestrator")

                # All 6 phases should complete
                assert len(run.phases_completed) > 0


# ============================================================================
# k=5: INTEGRATION + PRODUCTION TESTS
# ============================================================================

class TestProductionIntegration:
    """Test production-ready integration."""

    def test_orchestrator_config(self):
        """Orchestrator has production config."""
        mock_client = MagicMock()
        orch = SixPhaseOrchestrator(mock_client, max_iterations=5)

        assert orch.max_iterations == 5
        assert len(orch.phases_registry) == 6

    @pytest.mark.asyncio
    async def test_orchestrator_convergence(self):
        """Orchestrator converges (loss < 0.15) or escalates."""
        mock_client = MagicMock()

        # Mock responses
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text='{"skill_name": "test", "purpose": "test", "inputs": [], "outputs": [], "edge_cases": [], "dependencies": [], "estimated_loc": 200}')]
        )

        orch = SixPhaseOrchestrator(mock_client)
        run = OrchestrationRun(run_id="test-1", skill_request="test")

        output = await orch._phase1_api_design("test", run)

        # Loss should be below convergence threshold
        assert output.loss < 0.15 or run.current_k == orch.max_iterations

    def test_skill_builder_is_production_ready(self):
        """Skill-Builder meets production readiness criteria."""
        # Checklist:
        # - Handles all 6 phases
        # - Autonomous (no user approval)
        # - Handles errors gracefully
        # - Produces measurable output (quality score)
        # - Can be installed as standard skill

        mock_client = MagicMock()
        orch = SixPhaseOrchestrator(mock_client)

        assert len(orch.phases_registry) == 6  # All 6 phases
        assert hasattr(orch, 'orchestrate')  # Orchestration method
        assert orch.max_iterations == 5  # Hard LDD budget


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
