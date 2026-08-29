"""Phase 2 Test Generation: LLM-Powered Test Scenario Generation.

Complete test generation pipeline with:
- Schema validation (TestScenario)
- Multi-layer validation (schema, syntax, quality)
- 4 category-specific prompts (golden_path, happy_path, compliance, edge_case)
- Claude Opus API integration
- Cost tracking and budget enforcement
- Prompt versioning and tracking
- Pilot runner (10 features × 3 runs × 4 categories = 120 tests)

Usage:
    from core.test_generation.generator import LLMGenerator, PilotRunner

    # Single generation
    generator = LLMGenerator()
    result = generator.generate(
        category='golden_path',
        feature_spec='User login with email and password',
        feature_id='user_auth_login',
        component='AuthenticationModule',
        expected_users='All users',
        success_criteria='User successfully authenticated',
    )

    if result.success:
        print(f"Test generated: {result.scenario.name}")
    else:
        print(f"Generation failed: {result.error}")

    # Pilot run
    runner = PilotRunner()
    summary = runner.run()  # 120 tests total
    print(f"Pass rate: {summary['pass_rate_percent']:.1f}%")
"""

from .schema import (
    TestScenario,
    TestCategory,
    TestStatus,
    TestStep,
    TestAssertion,
)
from .validators import (
    TypeScriptValidator,
    SchemaValidator,
    TestQualityValidator,
    ESLintValidator,
)
from .generator import (
    LLMGenerator,
    PromptLoader,
    CostTracker,
    GenerationResult,
    PilotRunner,
)

__all__ = [
    # Schema
    'TestScenario',
    'TestCategory',
    'TestStatus',
    'TestStep',
    'TestAssertion',
    # Validators
    'TypeScriptValidator',
    'SchemaValidator',
    'TestQualityValidator',
    'ESLintValidator',
    # Generator
    'LLMGenerator',
    'PromptLoader',
    'CostTracker',
    'GenerationResult',
    'PilotRunner',
]
