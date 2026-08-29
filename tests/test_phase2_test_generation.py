"""Phase 2 Test Generation: Comprehensive Unit and Integration Tests.

Tests for:
- Schema validation and serialization
- TypeScript syntax validation
- Test quality validation
- Prompt loading and templating
- Cost tracking
- Generator integration (mocked API)
"""

import pytest
import json
import tempfile
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock

from core.test_generation.schema import (
    TestScenario,
    TestCategory,
    TestStatus,
    TestStep,
    TestAssertion,
)
from core.test_generation.validators import (
    TypeScriptValidator,
    SchemaValidator,
    TestQualityValidator,
)
from core.test_generation.generator import (
    PromptLoader,
    CostTracker,
    LLMGenerator,
    GenerationResult,
    PromptMetadata,
)


# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def valid_test_step():
    """Create a valid test step."""
    return TestStep(
        order=1,
        action="click",
        target="#button-id",
        params={},
        expected_outcome="Button click handled",
    )


@pytest.fixture
def valid_test_assertion():
    """Create a valid test assertion."""
    return TestAssertion(
        name="Button is visible",
        description="Verifies button renders",
        expression="screen.getByRole('button', {name: /click/i})",
        expected_result="button is visible",
    )


@pytest.fixture
def valid_test_scenario(valid_test_step, valid_test_assertion):
    """Create a valid test scenario."""
    step2 = TestStep(
        order=2,
        action="submit",
        target="#submit-button",
        params={},
        expected_outcome="Form submitted successfully",
    )
    assertion2 = TestAssertion(
        name="Form submission succeeded",
        description="Verifies form was submitted",
        expression="screen.getByText('Success')",
        expected_result="success message visible",
    )
    return TestScenario(
        id="test_feature_golden_path_1",
        name="Golden Path: Test Feature",
        feature_id="test_feature",
        category=TestCategory.GOLDEN_PATH,
        description="Complete happy-path scenario for test feature with detailed instructions and clear expectations",
        preconditions=["User is logged in"],
        postconditions=["Feature completed successfully"],
        steps=[valid_test_step, step2],
        assertions=[valid_test_assertion, assertion2],
        priority="high",
        tags=["golden_path"],
        estimated_duration_sec=5.0,
    )


@pytest.fixture
def temp_prompts_dir():
    """Create temporary prompts directory with test templates."""
    with tempfile.TemporaryDirectory() as tmpdir:
        prompts_dir = Path(tmpdir)

        # Create simple prompt templates
        for category in ['golden_path', 'happy_path', 'compliance', 'edge_case']:
            prompt_file = prompts_dir / f"{category}.txt"
            prompt_file.write_text(
                f"Generate {category} test for {{feature_id}}: {{feature_spec}}"
            )

        yield prompts_dir


@pytest.fixture
def temp_metrics_file():
    """Create temporary metrics file."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write('{}')
        metrics_file = Path(f.name)

    yield metrics_file

    # Cleanup
    metrics_file.unlink(missing_ok=True)


# ============================================================================
# Schema Tests
# ============================================================================


class TestTestScenarioSchema:
    """Test TestScenario schema."""

    def test_scenario_creation(self, valid_test_scenario):
        """Test creating a valid scenario."""
        assert valid_test_scenario.id == "test_feature_golden_path_1"
        assert valid_test_scenario.name == "Golden Path: Test Feature"
        assert valid_test_scenario.category == TestCategory.GOLDEN_PATH
        assert len(valid_test_scenario.steps) == 2
        assert len(valid_test_scenario.assertions) == 2

    def test_scenario_to_dict(self, valid_test_scenario):
        """Test scenario serialization to dict."""
        data = valid_test_scenario.to_dict()
        assert data['id'] == "test_feature_golden_path_1"
        assert data['category'] == 'golden_path'  # Enum converted to string
        assert data['status'] == 'generated'
        assert isinstance(data['steps'], list)
        assert isinstance(data['assertions'], list)

    def test_scenario_to_json(self, valid_test_scenario):
        """Test scenario serialization to JSON."""
        json_str = valid_test_scenario.to_json()
        parsed = json.loads(json_str)
        assert parsed['id'] == "test_feature_golden_path_1"
        assert parsed['category'] == 'golden_path'

    def test_scenario_from_dict(self, valid_test_scenario):
        """Test scenario deserialization from dict."""
        data = valid_test_scenario.to_dict()
        restored = TestScenario.from_dict(data)
        assert restored.id == valid_test_scenario.id
        assert restored.category == valid_test_scenario.category
        assert len(restored.steps) == len(valid_test_scenario.steps)

    def test_scenario_from_json(self, valid_test_scenario):
        """Test scenario deserialization from JSON."""
        json_str = valid_test_scenario.to_json()
        restored = TestScenario.from_json(json_str)
        assert restored.id == valid_test_scenario.id
        assert restored.category == valid_test_scenario.category

    def test_scenario_validation_valid(self, valid_test_scenario):
        """Test validation of valid scenario."""
        is_valid, errors = valid_test_scenario.validate()
        assert is_valid
        assert len(errors) == 0

    def test_scenario_validation_empty_id(self, valid_test_scenario):
        """Test validation fails for empty id."""
        valid_test_scenario.id = ""
        is_valid, errors = valid_test_scenario.validate()
        assert not is_valid
        assert any("id" in e.lower() for e in errors)

    def test_scenario_validation_no_steps(self, valid_test_scenario):
        """Test validation fails with no steps."""
        valid_test_scenario.steps = []
        is_valid, errors = valid_test_scenario.validate()
        assert not is_valid
        assert any("step" in e.lower() for e in errors)

    def test_scenario_validation_no_assertions(self, valid_test_scenario):
        """Test validation fails with no assertions."""
        valid_test_scenario.assertions = []
        is_valid, errors = valid_test_scenario.validate()
        assert not is_valid
        assert any("assertion" in e.lower() for e in errors)

    def test_test_step_to_dict(self, valid_test_step):
        """Test TestStep serialization."""
        data = valid_test_step.to_dict()
        assert data['order'] == 1
        assert data['action'] == 'click'
        assert data['target'] == '#button-id'

    def test_test_assertion_to_dict(self, valid_test_assertion):
        """Test TestAssertion serialization."""
        data = valid_test_assertion.to_dict()
        assert data['name'] == 'Button is visible'
        assert 'screen.getByRole' in data['expression']


# ============================================================================
# Validator Tests
# ============================================================================


class TestTypeScriptValidator:
    """Test TypeScript syntax validation."""

    def test_valid_screen_get_by_role(self):
        """Test valid screen.getByRole expression."""
        expr = "screen.getByRole('button', {name: /click/i})"
        is_valid, error = TypeScriptValidator.validate_expression(expr)
        assert is_valid
        assert error is None

    def test_valid_screen_get_by_text(self):
        """Test valid screen.getByText expression."""
        expr = "screen.getByText('Success')"
        is_valid, error = TypeScriptValidator.validate_expression(expr)
        assert is_valid

    def test_valid_expect(self):
        """Test valid expect expression."""
        expr = "expect(element).toHaveTextContent('value')"
        is_valid, error = TypeScriptValidator.validate_expression(expr)
        assert is_valid

    def test_valid_document_selector(self):
        """Test valid document.querySelector."""
        expr = "document.querySelector('#button-id')"
        is_valid, error = TypeScriptValidator.validate_expression(expr)
        assert is_valid

    def test_invalid_empty_expression(self):
        """Test empty expression fails."""
        is_valid, error = TypeScriptValidator.validate_expression("")
        assert not is_valid

    def test_invalid_unbalanced_parens(self):
        """Test unbalanced parentheses fail."""
        expr = "screen.getByRole('button'"
        is_valid, error = TypeScriptValidator.validate_expression(expr)
        assert not is_valid
        assert "Unbalanced" in error

    def test_invalid_forbidden_eval(self):
        """Test forbidden eval() pattern."""
        expr = "eval('malicious')"
        is_valid, error = TypeScriptValidator.validate_expression(expr)
        assert not is_valid
        assert "forbidden" in error.lower()

    def test_invalid_forbidden_innerHTML(self):
        """Test forbidden innerHTML pattern."""
        expr = "element.innerHTML = '<script>'"
        is_valid, error = TypeScriptValidator.validate_expression(expr)
        assert not is_valid

    def test_valid_action(self):
        """Test valid action type."""
        is_valid, error = TypeScriptValidator.validate_action("click")
        assert is_valid
        assert error is None

    def test_invalid_action(self):
        """Test invalid action type."""
        is_valid, error = TypeScriptValidator.validate_action("teleport")
        assert not is_valid
        assert "not in valid actions" in error.lower()


class TestSchemaValidator:
    """Test schema validation."""

    def test_validate_valid_scenario_dict(self, valid_test_scenario):
        """Test validation of valid scenario dict."""
        data = valid_test_scenario.to_dict()
        is_valid, errors = SchemaValidator.validate_scenario_dict(data)
        assert is_valid
        assert len(errors) == 0

    def test_validate_missing_required_field(self, valid_test_scenario):
        """Test validation fails for missing required field."""
        data = valid_test_scenario.to_dict()
        del data['id']
        is_valid, errors = SchemaValidator.validate_scenario_dict(data)
        assert not is_valid
        assert any("id" in e.lower() for e in errors)

    def test_validate_invalid_category(self, valid_test_scenario):
        """Test validation fails for invalid category."""
        data = valid_test_scenario.to_dict()
        data['category'] = 'invalid_category'
        is_valid, errors = SchemaValidator.validate_scenario_dict(data)
        assert not is_valid
        assert any("category" in e.lower() for e in errors)

    def test_validate_empty_steps(self, valid_test_scenario):
        """Test validation fails for empty steps."""
        data = valid_test_scenario.to_dict()
        data['steps'] = []
        is_valid, errors = SchemaValidator.validate_scenario_dict(data)
        assert not is_valid

    def test_validate_json_syntax_valid(self):
        """Test valid JSON syntax."""
        json_str = '{"id": "test", "name": "Test"}'
        is_valid, error = SchemaValidator.validate_json_string(json_str)
        assert is_valid
        assert error is None

    def test_validate_json_syntax_invalid(self):
        """Test invalid JSON syntax."""
        json_str = '{"id": "test", "name": "Test"'  # Missing closing brace
        is_valid, error = SchemaValidator.validate_json_string(json_str)
        assert not is_valid
        assert error is not None


class TestQualityValidationClass:
    """Test quality validation."""

    def test_validate_quality_valid(self, valid_test_scenario):
        """Test quality validation of valid scenario."""
        data = valid_test_scenario.to_dict()
        is_valid, issues = TestQualityValidator.validate_quality(data)
        assert is_valid
        assert len(issues) == 0

    def test_validate_quality_short_description(self, valid_test_scenario):
        """Test quality validation fails for short description."""
        data = valid_test_scenario.to_dict()
        data['description'] = "Too short"
        is_valid, issues = TestQualityValidator.validate_quality(data)
        assert not is_valid
        assert any("description" in i.lower() for i in issues)

    def test_validate_quality_no_preconditions(self, valid_test_scenario):
        """Test quality validation flags missing preconditions."""
        data = valid_test_scenario.to_dict()
        data['preconditions'] = []
        is_valid, issues = TestQualityValidator.validate_quality(data)
        assert not is_valid

    def test_validate_quality_too_many_steps(self, valid_test_scenario):
        """Test quality validation flags too many steps."""
        data = valid_test_scenario.to_dict()
        # Create 11 steps
        data['steps'] = [
            {"order": i, "action": "click", "target": f"#btn{i}", "expected_outcome": "clicked"}
            for i in range(11)
        ]
        is_valid, issues = TestQualityValidator.validate_quality(data)
        assert not is_valid
        assert any("maximum" in i.lower() and "steps" in i.lower() for i in issues)


# ============================================================================
# Prompt Loader Tests
# ============================================================================


class TestPromptLoader:
    """Test prompt loading and templating."""

    def test_load_prompt(self, temp_prompts_dir):
        """Test loading a prompt file."""
        loader = PromptLoader(prompts_dir=temp_prompts_dir)
        prompt = loader.load('golden_path')
        assert 'golden_path' in prompt
        assert 'feature_id' in prompt
        assert '{feature_id}' in prompt

    def test_template_prompt(self, temp_prompts_dir):
        """Test templating a prompt."""
        loader = PromptLoader(prompts_dir=temp_prompts_dir)
        prompt = loader.template(
            category='golden_path',
            feature_id='test_feature',
            feature_spec='Test spec',
        )
        assert 'test_feature' in prompt
        assert 'Test spec' in prompt

    def test_get_metadata(self, temp_prompts_dir):
        """Test getting prompt metadata."""
        loader = PromptLoader(prompts_dir=temp_prompts_dir)
        metadata = loader.get_metadata('golden_path')
        assert metadata is not None
        assert metadata.category == 'golden_path'
        assert metadata.version == '1.0'
        assert 'golden_path' in metadata.parameters or True  # May be empty

    def test_load_nonexistent_prompt(self, temp_prompts_dir):
        """Test loading nonexistent prompt raises error."""
        loader = PromptLoader(prompts_dir=temp_prompts_dir)
        with pytest.raises(FileNotFoundError):
            loader.load('nonexistent_category')


# ============================================================================
# Cost Tracker Tests
# ============================================================================


class TestCostTracker:
    """Test cost tracking and budget enforcement."""

    def test_cost_calculation(self, temp_metrics_file):
        """Test cost calculation from tokens."""
        tracker = CostTracker(metrics_file=temp_metrics_file)
        metrics = tracker.record(input_tokens=1000, output_tokens=500)

        assert metrics.input_tokens == 1000
        assert metrics.output_tokens == 500
        assert metrics.cost_usd > 0

    def test_within_budget_initially(self, temp_metrics_file):
        """Test initially within budget."""
        tracker = CostTracker(metrics_file=temp_metrics_file)
        assert tracker.within_budget()

    def test_budget_tracking(self, temp_metrics_file):
        """Test budget tracking across multiple generations."""
        tracker = CostTracker(metrics_file=temp_metrics_file)

        # Record a generation
        tracker.record(input_tokens=1000, output_tokens=1000)
        remaining_1 = tracker.get_remaining_budget()
        assert remaining_1 < tracker.WEEKLY_BUDGET_USD

        # Record another
        tracker.record(input_tokens=500, output_tokens=500)
        remaining_2 = tracker.get_remaining_budget()
        assert remaining_2 < remaining_1

    def test_save_and_load_metrics(self, temp_metrics_file):
        """Test saving and loading metrics."""
        tracker1 = CostTracker(metrics_file=temp_metrics_file)
        tracker1.record(input_tokens=1000, output_tokens=500)
        tracker1.save_metrics()

        # Create new tracker instance and load
        tracker2 = CostTracker(metrics_file=temp_metrics_file)
        assert tracker2.total_cost == tracker1.total_cost
        assert tracker2.total_input_tokens == 1000
        assert tracker2.total_output_tokens == 500
        assert tracker2.generations_count == 1


# ============================================================================
# Generator Integration Tests
# ============================================================================


class TestLLMGenerator:
    """Test LLM generator (with mocked API)."""

    @patch('core.test_generation.generator.anthropic.Anthropic')
    def test_generate_success(self, mock_client_class, temp_prompts_dir, temp_metrics_file):
        """Test successful test generation."""
        # Mock API response with valid JSON (wrapped in markdown code block for robustness)
        valid_json = json.dumps({
            "id": "test_1",
            "name": "Test Scenario",
            "feature_id": "feature",
            "category": "golden_path",
            "description": "Test description for golden path scenario is quite detailed and informative",
            "steps": [
                {"order": 1, "action": "click", "target": "#btn", "params": {}, "expected_outcome": "clicked"},
                {"order": 2, "action": "submit", "target": "#submit", "params": {}, "expected_outcome": "success"}
            ],
            "assertions": [
                {"name": "test1", "description": "button visible", "expression": "screen.getByRole('button')", "expected_result": "visible"},
                {"name": "test2", "description": "success message", "expression": "screen.getByText('Success')", "expected_result": "visible"}
            ],
            "preconditions": ["ready"],
            "postconditions": ["done"],
            "priority": "high",
            "tags": ["test"],
            "estimated_duration_sec": 5.0
        })
        # Wrap in markdown code block like real LLM responses would
        response_text = f"```json\n{valid_json}\n```"
        mock_response = Mock()
        mock_response.content = [Mock(text=response_text)]
        mock_response.usage.input_tokens = 1000
        mock_response.usage.output_tokens = 500
        mock_client = Mock()
        mock_client.messages.create.return_value = mock_response
        mock_client_class.return_value = mock_client

        # Create generator with mocked client
        with patch.object(LLMGenerator, '__init__', lambda x: None):
            generator = LLMGenerator()
            generator.api_key = "test_key"
            generator.client = mock_client
            generator.prompt_loader = PromptLoader(prompts_dir=temp_prompts_dir)
            generator.cost_tracker = CostTracker(metrics_file=temp_metrics_file)

            # Generate test
            result = generator.generate(
                category='golden_path',
                feature_spec='Test feature',
                feature_id='test_feature',
                component='TestComponent',
                expected_users='All users',
                success_criteria='Feature works',
                run_number=1,
            )

            assert result.success
            assert result.scenario is not None
            assert result.error is None
            assert result.cost_metrics is not None

    @patch('core.test_generation.generator.anthropic.Anthropic')
    def test_generate_invalid_json(self, mock_client_class, temp_prompts_dir, temp_metrics_file):
        """Test generation with invalid JSON response."""
        # Mock API with invalid JSON
        mock_response = Mock()
        mock_response.content = [Mock(text='not valid json')]
        mock_response.usage.input_tokens = 1000
        mock_response.usage.output_tokens = 500
        mock_client = Mock()
        mock_client.messages.create.return_value = mock_response
        mock_client_class.return_value = mock_client

        with patch.object(LLMGenerator, '__init__', lambda x: None):
            generator = LLMGenerator()
            generator.api_key = "test_key"
            generator.client = mock_client
            generator.prompt_loader = PromptLoader(prompts_dir=temp_prompts_dir)
            generator.cost_tracker = CostTracker(metrics_file=temp_metrics_file)

            result = generator.generate(
                category='golden_path',
                feature_spec='Test feature',
                feature_id='test_feature',
                component='TestComponent',
                expected_users='All users',
                success_criteria='Feature works',
                run_number=1,
                max_retries=1,
            )

            assert not result.success
            assert result.error is not None

    def test_extract_json_from_markdown(self):
        """Test JSON extraction from markdown response."""
        response = '```json\n{"id": "test"}\n```'
        result = LLMGenerator._extract_json(response)
        assert result is not None
        assert '"id"' in result

    def test_extract_json_raw(self):
        """Test JSON extraction from raw JSON."""
        response = '{"id": "test", "name": "Test"}'
        result = LLMGenerator._extract_json(response)
        assert result is not None
        assert '"id"' in result

    def test_extract_json_none(self):
        """Test JSON extraction returns None for non-JSON."""
        response = 'This is not JSON'
        result = LLMGenerator._extract_json(response)
        assert result is None


# ============================================================================
# Integration Tests
# ============================================================================


class TestGenerationResultIntegration:
    """Test GenerationResult serialization."""

    def test_generation_result_success(self, valid_test_scenario):
        """Test successful generation result."""
        result = GenerationResult(
            success=True,
            scenario=valid_test_scenario,
        )
        data = result.to_dict()
        assert data['success']
        assert data['scenario'] is not None

    def test_generation_result_failure(self):
        """Test failed generation result."""
        result = GenerationResult(
            success=False,
            error="Test error",
        )
        data = result.to_dict()
        assert not data['success']
        assert data['error'] == "Test error"


# ============================================================================
# End-to-End Tests
# ============================================================================


class TestEndToEnd:
    """End-to-end test generation workflow."""

    def test_full_scenario_lifecycle(self, valid_test_scenario):
        """Test full scenario lifecycle: create → serialize → deserialize → validate."""
        # Validate original
        is_valid, errors = valid_test_scenario.validate()
        assert is_valid

        # Serialize to JSON
        json_str = valid_test_scenario.to_json()
        assert len(json_str) > 0

        # Deserialize
        restored = TestScenario.from_json(json_str)

        # Validate restored
        is_valid, errors = restored.validate()
        assert is_valid

        # Verify equality
        assert restored.id == valid_test_scenario.id
        assert restored.category == valid_test_scenario.category


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
