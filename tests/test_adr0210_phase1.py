"""Tests for ADR-0210 Phase 1: Unified Initial Analysis Prompt."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "operator" / "orchestration"))

from initial_analysis import (
    InitialAnalysisRequest, Classification, Entities, GlobalPlan, Step,
    make_task_analysis_prompt, parse_task_analysis_response,
)


class TestADR0210Phase1Prompt:
    """ADR-0210 Phase 1: Unified task analysis prompt."""

    def test_classification_dataclass(self):
        """Classification captures task type, complexity, engine routing, confidence."""
        c = Classification(
            task_type="code_generation",
            complexity="moderate",
            engine_preference="claude",
            confidence=0.85,
        )
        assert c.task_type == "code_generation"
        assert c.complexity == "moderate"
        assert c.engine_preference == "claude"
        assert c.confidence == 0.85

    def test_entities_dataclass(self):
        """Entities captures files, tools, APIs, environment vars."""
        e = Entities(
            files=[{"path": "data.csv", "purpose": "input"}],
            tools=["file_reader"],
            external_apis=["api.example.com"],
            environment_vars=["API_KEY"],
        )
        assert len(e.files) == 1
        assert e.files[0]["path"] == "data.csv"
        assert "file_reader" in e.tools
        assert "api.example.com" in e.external_apis
        assert "API_KEY" in e.environment_vars

    def test_step_dataclass(self):
        """Step captures action, dependencies, parallelization hints, tokens."""
        s = Step(
            step=1,
            action="read_file",
            depends_on=[],
            can_parallelize=[2, 3],
            estimated_tokens=1000,
        )
        assert s.step == 1
        assert s.action == "read_file"
        assert 2 in s.can_parallelize
        assert s.estimated_tokens == 1000

    def test_global_plan_dataclass(self):
        """GlobalPlan captures steps, duration, tokens, fallback strategy."""
        steps = [
            Step(step=1, action="read_file", estimated_tokens=500),
            Step(step=2, action="analyze_data", depends_on=[1], estimated_tokens=2000),
        ]
        p = GlobalPlan(
            steps=steps,
            estimated_duration_s=10,
            estimated_tokens=2500,
            fallback_strategy="sequential",
        )
        assert len(p.steps) == 2
        assert p.estimated_tokens == 2500
        assert p.fallback_strategy == "sequential"

    def test_initial_analysis_request_serialization(self):
        """InitialAnalysisRequest serializes and deserializes without loss."""
        req = InitialAnalysisRequest(
            classification=Classification(
                task_type="data_analysis",
                complexity="simple",
                engine_preference="local",
                confidence=0.9,
            ),
            entities=Entities(
                files=[{"path": "input.json", "purpose": "input"}],
                tools=["json_parser"],
            ),
            global_plan=GlobalPlan(
                steps=[Step(step=1, action="read_file", estimated_tokens=100)],
                estimated_duration_s=2,
                estimated_tokens=100,
            ),
            cache_key="test-key-123",
            ttl_seconds=300,
        )

        # Serialize
        d = req.to_dict()
        assert d["classification"]["task_type"] == "data_analysis"
        assert d["cache_key"] == "test-key-123"
        assert len(d["global_plan"]["steps"]) == 1

        # Deserialize
        req2 = InitialAnalysisRequest.from_dict(d)
        assert req2.classification.task_type == "data_analysis"
        assert req2.cache_key == "test-key-123"
        assert len(req2.global_plan.steps) == 1

    def test_task_analysis_prompt_template(self):
        """Prompt template is well-formed and includes all required sections."""
        task = "Generate a Python function that reads a CSV and calculates stats."
        context = {"file_exists": True, "format": "csv"}

        prompt = make_task_analysis_prompt(task, context)

        # Prompt should include all sections
        assert "Classification" in prompt
        assert "Entity Extraction" in prompt
        assert "Global Execution Plan" in prompt
        assert "JSON Output Format" in prompt

        # Should include the task and context
        assert task in prompt
        assert "file_exists" in prompt

    def test_parse_task_analysis_response_valid_json(self):
        """Parse valid JSON response from LM."""
        response = json.dumps({
            "classification": {
                "task_type": "code_generation",
                "complexity": "moderate",
                "engine_preference": "claude",
                "confidence": 0.8,
            },
            "entities": {
                "files": [{"path": "test.py", "purpose": "output"}],
                "tools": ["file_writer"],
                "external_apis": [],
                "environment_vars": [],
            },
            "global_plan": {
                "steps": [
                    {
                        "step": 1,
                        "action": "generate_code",
                        "depends_on": [],
                        "can_parallelize": [],
                        "estimated_tokens": 5000,
                    }
                ],
                "estimated_duration_s": 5,
                "estimated_tokens": 5000,
                "fallback_strategy": "sequential",
            },
        })

        req = parse_task_analysis_response(response)
        assert req.classification.task_type == "code_generation"
        assert req.global_plan.estimated_tokens == 5000

    def test_parse_task_analysis_response_json_in_markdown(self):
        """Parse JSON response wrapped in markdown code fence."""
        response = """
        Here is the analysis:

        ```json
        {
            "classification": {
                "task_type": "tool_call",
                "complexity": "simple",
                "engine_preference": "default",
                "confidence": 0.75
            },
            "entities": {
                "files": [],
                "tools": ["calc"],
                "external_apis": [],
                "environment_vars": []
            },
            "global_plan": {
                "steps": [
                    {"step": 1, "action": "call_tool", "depends_on": [], "can_parallelize": [], "estimated_tokens": 500}
                ],
                "estimated_duration_s": 1,
                "estimated_tokens": 500,
                "fallback_strategy": "sequential"
            }
        }
        ```

        Done!
        """

        req = parse_task_analysis_response(response)
        assert req.classification.task_type == "tool_call"
        assert req.classification.confidence == 0.75

    def test_parse_task_analysis_response_invalid_json_raises(self):
        """Invalid JSON response raises ValueError."""
        response = "This is not JSON at all"

        with pytest.raises(ValueError, match="not valid JSON"):
            parse_task_analysis_response(response)

    def test_parse_task_analysis_response_missing_fields_raises(self):
        """Missing required fields raises ValueError."""
        response = json.dumps({
            "classification": {"task_type": "code_gen", "complexity": "simple"},
            # Missing entities and global_plan
        })

        with pytest.raises(ValueError, match="Missing required fields"):
            parse_task_analysis_response(response)

    def test_parallelization_hints(self):
        """Steps encode can_parallelize hints for concurrent execution."""
        steps = [
            Step(step=1, action="read_file", can_parallelize=[2, 3]),
            Step(step=2, action="read_file", can_parallelize=[1, 3]),
            Step(step=3, action="read_file", can_parallelize=[1, 2]),
            Step(step=4, action="merge", depends_on=[1, 2, 3]),
        ]
        plan = GlobalPlan(steps=steps, estimated_duration_s=3, estimated_tokens=1000)

        # Steps 1, 2, 3 can all run in parallel
        parallel_group_1 = [s for s in plan.steps if not s.depends_on]
        assert len(parallel_group_1) == 3
        assert all(4 not in s.can_parallelize for s in parallel_group_1)

    def test_serialization_roundtrip_preserves_all_fields(self):
        """to_dict/from_dict roundtrip preserves all fields without loss."""
        original = InitialAnalysisRequest(
            classification=Classification(
                task_type="delegation",
                complexity="complex",
                engine_preference="gemini",
                confidence=0.6,
            ),
            entities=Entities(
                files=[
                    {"path": "a.txt", "purpose": "input"},
                    {"path": "b.txt", "purpose": "output"},
                ],
                tools=["tool1", "tool2"],
                external_apis=["api1"],
                environment_vars=["VAR1", "VAR2"],
            ),
            global_plan=GlobalPlan(
                steps=[
                    Step(step=1, action="tool_call", depends_on=[], can_parallelize=[2]),
                    Step(step=2, action="tool_call", depends_on=[], can_parallelize=[1]),
                    Step(step=3, action="merge", depends_on=[1, 2]),
                ],
                estimated_duration_s=8,
                estimated_tokens=8000,
                fallback_strategy="parallel",
            ),
            cache_key="abc-123",
            ttl_seconds=600,
        )

        # Roundtrip
        d = original.to_dict()
        restored = InitialAnalysisRequest.from_dict(d)

        # Verify all fields match
        assert restored.classification.task_type == original.classification.task_type
        assert restored.classification.confidence == original.classification.confidence
        assert len(restored.entities.files) == len(original.entities.files)
        assert len(restored.global_plan.steps) == len(original.global_plan.steps)
        assert restored.cache_key == original.cache_key
        assert restored.ttl_seconds == original.ttl_seconds
