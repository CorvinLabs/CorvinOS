#!/usr/bin/env python3
"""Phase 2 Test Generation: LLM-powered test scenario generator.

Generates test scenarios using Claude Opus API with:
- 4 category-specific prompts (golden_path, happy_path, compliance, edge_case)
- Cost tracking ($0.57/week budget)
- Prompt versioning and tracking
- Full schema validation and TypeScript syntax checking
- Pilot runner for 10 features (3 runs per feature)

Architecture:
- PromptLoader: Load and template prompts
- LLMGenerator: Call Claude Opus API
- TestValidator: Multi-layer validation (schema, syntax, quality)
- CostTracker: Budget and spending monitoring
- PilotRunner: Execute pilot with 10 features
"""

from __future__ import annotations

import os
import json
import re
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
import hashlib
from enum import Enum

import anthropic

from .schema import TestScenario, TestCategory, TestStatus, TestStep, TestAssertion
from .validators import (
    TypeScriptValidator,
    SchemaValidator,
    TestQualityValidator,
    ESLintValidator,
)


class PromptVersion(str, Enum):
    """Semantic versioning for prompts."""
    V1_0 = "1.0"
    V1_1 = "1.1"
    V2_0 = "2.0"


@dataclass
class CostMetrics:
    """Track LLM API costs."""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GenerationResult:
    """Result of a single test generation."""
    success: bool
    scenario: Optional[TestScenario] = None
    error: Optional[str] = None
    validation_errors: List[str] = field(default_factory=list)
    quality_issues: List[str] = field(default_factory=list)
    cost_metrics: Optional[CostMetrics] = None
    retry_count: int = 0
    timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        if self.scenario:
            data['scenario'] = self.scenario.to_dict()
        return data


@dataclass
class PromptMetadata:
    """Metadata for a prompt template."""
    category: str
    version: str
    hash: str
    created_at: str
    last_modified: str
    parameters: List[str] = field(default_factory=list)


class PromptLoader:
    """Load and template prompt files."""

    def __init__(self, prompts_dir: Path = None):
        if prompts_dir is None:
            prompts_dir = Path(__file__).parent / "prompts"
        self.prompts_dir = prompts_dir
        self.metadata: Dict[str, PromptMetadata] = {}
        self._load_metadata()

    def _load_metadata(self) -> None:
        """Load prompt metadata."""
        for category in ['golden_path', 'happy_path', 'compliance', 'edge_case']:
            prompt_file = self.prompts_dir / f"{category}.txt"
            if prompt_file.exists():
                stat = prompt_file.stat()
                content = prompt_file.read_text()
                file_hash = hashlib.sha256(content.encode()).hexdigest()[:12]

                # Extract parameters from template
                params = re.findall(r'\{(\w+)\}', content)
                unique_params = sorted(set(params))

                self.metadata[category] = PromptMetadata(
                    category=category,
                    version=PromptVersion.V1_0.value,
                    hash=file_hash,
                    created_at=datetime.fromtimestamp(stat.st_ctime).isoformat(),
                    last_modified=datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    parameters=unique_params,
                )

    def load(self, category: str) -> str:
        """Load prompt template for category.

        Args:
            category: one of 'golden_path', 'happy_path', 'compliance', 'edge_case'

        Returns:
            Prompt template string
        """
        prompt_file = self.prompts_dir / f"{category}.txt"
        if not prompt_file.exists():
            raise FileNotFoundError(f"Prompt not found: {prompt_file}")

        return prompt_file.read_text()

    def template(self, category: str, **kwargs) -> str:
        """Load and template prompt.

        Args:
            category: prompt category
            **kwargs: template parameters

        Returns:
            Templated prompt string
        """
        template = self.load(category)
        return template.format(**kwargs)

    def get_metadata(self, category: str) -> Optional[PromptMetadata]:
        """Get metadata for a prompt."""
        return self.metadata.get(category)


class CostTracker:
    """Track LLM API costs and enforce budget."""

    CLAUDE_OPUS_PRICES = {
        'input': 0.015 / 1_000_000,      # $15 per 1M input tokens
        'output': 0.075 / 1_000_000,     # $75 per 1M output tokens
    }

    WEEKLY_BUDGET_USD = 0.57

    def __init__(self, metrics_file: Path = None):
        if metrics_file is None:
            metrics_file = Path("/tmp/phase2_cost_metrics.json")
        self.metrics_file = metrics_file
        self.total_cost = 0.0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.generations_count = 0
        self.load_metrics()

    def load_metrics(self) -> None:
        """Load existing metrics from file."""
        if self.metrics_file.exists():
            try:
                data = json.loads(self.metrics_file.read_text())
                self.total_cost = data.get('total_cost', 0.0)
                self.total_input_tokens = data.get('total_input_tokens', 0)
                self.total_output_tokens = data.get('total_output_tokens', 0)
                self.generations_count = data.get('generations_count', 0)
            except json.JSONDecodeError:
                pass

    def save_metrics(self) -> None:
        """Save metrics to file."""
        data = {
            'total_cost': self.total_cost,
            'total_input_tokens': self.total_input_tokens,
            'total_output_tokens': self.total_output_tokens,
            'generations_count': self.generations_count,
            'last_updated': datetime.now().isoformat(),
            'weekly_budget': self.WEEKLY_BUDGET_USD,
            'budget_remaining': self.WEEKLY_BUDGET_USD - self.total_cost,
        }
        self.metrics_file.write_text(json.dumps(data, indent=2))

    def record(self, input_tokens: int, output_tokens: int) -> CostMetrics:
        """Record token usage and calculate cost.

        Args:
            input_tokens: number of input tokens
            output_tokens: number of output tokens

        Returns:
            CostMetrics with cost calculation
        """
        cost = (
            input_tokens * self.CLAUDE_OPUS_PRICES['input'] +
            output_tokens * self.CLAUDE_OPUS_PRICES['output']
        )

        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cost += cost
        self.generations_count += 1

        metrics = CostMetrics(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            timestamp=datetime.now().isoformat(),
        )

        self.save_metrics()
        return metrics

    def get_remaining_budget(self) -> float:
        """Get remaining weekly budget."""
        return max(0.0, self.WEEKLY_BUDGET_USD - self.total_cost)

    def within_budget(self, estimated_tokens: int = 3000) -> bool:
        """Check if generation would stay within budget."""
        estimated_cost = (
            estimated_tokens * self.CLAUDE_OPUS_PRICES['input'] +
            1500 * self.CLAUDE_OPUS_PRICES['output']
        )
        return (self.total_cost + estimated_cost) <= self.WEEKLY_BUDGET_USD


class LLMGenerator:
    """Generate test scenarios using Claude Opus API."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv('ANTHROPIC_API_KEY')
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.prompt_loader = PromptLoader()
        self.cost_tracker = CostTracker()

    def generate(
        self,
        category: str,
        feature_spec: str,
        feature_id: str,
        component: str,
        expected_users: str,
        success_criteria: str,
        run_number: int = 1,
        max_retries: int = 3,
    ) -> GenerationResult:
        """Generate a test scenario.

        Args:
            category: one of 'golden_path', 'happy_path', 'compliance', 'edge_case'
            feature_spec: feature specification text
            feature_id: unique feature identifier
            component: component name
            expected_users: target users
            success_criteria: success criteria text
            run_number: which run is this (1-3)
            max_retries: max retries on validation failure

        Returns:
            GenerationResult with scenario or error
        """
        result = GenerationResult(success=False, timestamp=datetime.now().isoformat())

        # Check budget before generating
        if not self.cost_tracker.within_budget():
            result.error = f"Budget exhausted. Remaining: ${self.cost_tracker.get_remaining_budget():.4f}"
            return result

        # Template the prompt
        try:
            prompt = self.prompt_loader.template(
                category=category,
                feature_spec=feature_spec,
                feature_id=feature_id,
                component=component,
                expected_users=expected_users,
                success_criteria=success_criteria,
                run_number=run_number,
                feature_name=feature_id.replace('_', ' ').title(),
            )
        except Exception as e:
            result.error = f"Prompt templating failed: {e}"
            return result

        # Generate with retries
        for attempt in range(max_retries):
            result.retry_count = attempt

            try:
                # Call Claude Opus API
                response = self.client.messages.create(
                    model="claude-opus-4-1-20250805",
                    max_tokens=2000,
                    messages=[
                        {
                            "role": "user",
                            "content": prompt,
                        }
                    ],
                )

                # Extract content
                content = response.content[0].text

                # Record cost
                cost_metrics = self.cost_tracker.record(
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                )
                result.cost_metrics = cost_metrics

                # Parse JSON response
                json_str = self._extract_json(content)
                if not json_str:
                    result.error = "No JSON found in response"
                    continue

                # Validate JSON syntax
                is_valid, error = SchemaValidator.validate_json_string(json_str)
                if not is_valid:
                    result.error = f"JSON validation failed: {error}"
                    continue

                # Parse JSON
                scenario_data = json.loads(json_str)

                # Validate schema
                is_valid, validation_errors = SchemaValidator.validate_scenario_dict(scenario_data)
                result.validation_errors = validation_errors
                if not is_valid:
                    result.error = "Schema validation failed"
                    continue

                # Validate quality
                is_valid, quality_issues = TestQualityValidator.validate_quality(scenario_data)
                result.quality_issues = quality_issues
                if not is_valid:
                    # Quality issues don't fail generation, just flag them
                    pass

                # Create TestScenario object
                scenario = TestScenario.from_dict(scenario_data)
                is_valid, validation_errors = scenario.validate()
                if not is_valid:
                    result.validation_errors = validation_errors
                    result.error = "Scenario validation failed"
                    continue

                # Mark as success
                result.success = True
                result.scenario = scenario
                result.error = None

                return result

            except anthropic.APIError as e:
                result.error = f"API error (attempt {attempt + 1}): {e}"
                time.sleep(0.5 * (attempt + 1))  # Exponential backoff
                continue
            except Exception as e:
                result.error = f"Generation error (attempt {attempt + 1}): {e}"
                continue

        return result

    @staticmethod
    def _extract_json(text: str) -> Optional[str]:
        """Extract JSON from LLM response (handles markdown wrapping).

        Args:
            text: LLM response text

        Returns:
            JSON string or None
        """
        # Try to find JSON block in markdown
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if json_match:
            return json_match.group(1)

        # Try to find raw JSON
        json_match = re.search(r'(\{.*?\})', text, re.DOTALL)
        if json_match:
            return json_match.group(1)

        # Return full text if it looks like JSON
        if text.strip().startswith('{'):
            return text.strip()

        return None


class PilotRunner:
    """Run pilot test generation for 10 features."""

    # 10 representative features to test with
    PILOT_FEATURES = [
        {
            'feature_id': 'user_auth_login',
            'name': 'User Authentication - Login',
            'component': 'AuthenticationModule',
            'spec': 'User login with email and password, including 2FA',
            'users': 'All users',
            'criteria': 'User successfully authenticated and session created',
        },
        {
            'feature_id': 'consent_management',
            'name': 'GDPR Consent Management',
            'component': 'ComplianceModule',
            'spec': 'Collect, manage, and track user consent preferences',
            'users': 'All users',
            'criteria': 'Consent preferences stored and respected',
        },
        {
            'feature_id': 'data_export',
            'name': 'User Data Export (GDPR Art. 17)',
            'component': 'DataPortabilityModule',
            'spec': 'Export user data in machine-readable format',
            'users': 'EU users',
            'criteria': 'Complete data export within 30 days',
        },
        {
            'feature_id': 'audit_log_viewer',
            'name': 'Audit Log Viewer',
            'component': 'AuditModule',
            'spec': 'View and filter audit trail events',
            'users': 'Administrators',
            'criteria': 'All audit events visible and filterable',
        },
        {
            'feature_id': 'feature_flag_toggle',
            'name': 'Feature Flag Toggle (Admin)',
            'component': 'FeatureFlagModule',
            'spec': 'Enable/disable features via admin panel',
            'users': 'Administrators',
            'criteria': 'Feature enabled/disabled state persists',
        },
        {
            'feature_id': 'plugin_marketplace',
            'name': 'Plugin Marketplace',
            'component': 'PluginModule',
            'spec': 'Browse and install community plugins',
            'users': 'Power users',
            'criteria': 'Plugin installed and functional',
        },
        {
            'feature_id': 'workflow_editor',
            'name': 'Workflow Editor',
            'component': 'WorkflowModule',
            'spec': 'Create and edit automation workflows',
            'users': 'Power users',
            'criteria': 'Workflow created and executes correctly',
        },
        {
            'feature_id': 'metric_dashboard',
            'name': 'Metrics Dashboard',
            'component': 'MetricsModule',
            'spec': 'Display real-time system metrics and KPIs',
            'users': 'Operators',
            'criteria': 'Metrics displayed accurately in real-time',
        },
        {
            'feature_id': 'webhook_config',
            'name': 'Webhook Configuration',
            'component': 'IntegrationModule',
            'spec': 'Configure webhooks for external integrations',
            'users': 'Developers',
            'criteria': 'Webhook registered and events delivered',
        },
        {
            'feature_id': 'session_management',
            'name': 'Session Management',
            'component': 'SessionModule',
            'spec': 'Manage user sessions with timeout and renewal',
            'users': 'All users',
            'criteria': 'Session lifecycle managed correctly',
        },
    ]

    def __init__(self, output_dir: Path = None):
        if output_dir is None:
            output_dir = Path("/tmp/phase2_pilot_results")
        self.output_dir = output_dir
        self.output_dir.mkdir(exist_ok=True)

        self.generator = LLMGenerator()
        self.results: Dict[str, List[GenerationResult]] = {}
        self.summary: Dict[str, Any] = {}

    def run(self) -> Dict[str, Any]:
        """Run pilot for all 10 features (3 runs each).

        Returns:
            Summary of pilot results
        """
        print(f"Phase 2 Pilot Test Generation Starting")
        print(f"Features: {len(self.PILOT_FEATURES)}")
        print(f"Runs per feature: 3")
        print(f"Total tests to generate: {len(self.PILOT_FEATURES) * 3 * 4} (3 runs × 4 categories)")
        print(f"Budget: ${self.generator.cost_tracker.WEEKLY_BUDGET_USD:.2f}")
        print()

        start_time = time.time()

        for feature in self.PILOT_FEATURES:
            feature_id = feature['feature_id']
            self.results[feature_id] = []

            print(f"Generating tests for: {feature['name']}")

            for run in range(1, 4):
                for category in ['golden_path', 'happy_path', 'compliance', 'edge_case']:
                    # Check budget
                    remaining = self.generator.cost_tracker.get_remaining_budget()
                    if remaining < 0.01:
                        print(f"  ⚠ Budget exhausted. Stopping.")
                        break

                    print(f"  [{run}/3] {category}...", end=" ", flush=True)

                    result = self.generator.generate(
                        category=category,
                        feature_spec=feature['spec'],
                        feature_id=feature_id,
                        component=feature['component'],
                        expected_users=feature['users'],
                        success_criteria=feature['criteria'],
                        run_number=run,
                    )

                    self.results[feature_id].append(result)

                    if result.success:
                        print("✅")
                    else:
                        print(f"❌ ({result.error})")

        duration_sec = time.time() - start_time

        # Generate summary
        self._generate_summary(duration_sec)

        return self.summary

    def _generate_summary(self, duration_sec: float) -> None:
        """Generate pilot summary."""
        total_generated = 0
        total_succeeded = 0
        total_failed = 0

        by_category = {
            'golden_path': {'success': 0, 'failed': 0},
            'happy_path': {'success': 0, 'failed': 0},
            'compliance': {'success': 0, 'failed': 0},
            'edge_case': {'success': 0, 'failed': 0},
        }

        errors = []

        for feature_id, results in self.results.items():
            for result in results:
                total_generated += 1
                if result.success:
                    total_succeeded += 1
                    category = result.scenario.category.value
                    by_category[category]['success'] += 1
                else:
                    total_failed += 1
                    if result.error:
                        errors.append(f"{feature_id}: {result.error}")
                    for ce in result.validation_errors:
                        errors.append(f"{feature_id} validation: {ce}")

        pass_rate = (total_succeeded / total_generated * 100) if total_generated > 0 else 0

        self.summary = {
            'pilot_status': 'complete',
            'timestamp': datetime.now().isoformat(),
            'duration_sec': duration_sec,
            'features_tested': len(self.PILOT_FEATURES),
            'total_tests_generated': total_generated,
            'total_succeeded': total_succeeded,
            'total_failed': total_failed,
            'pass_rate_percent': pass_rate,
            'by_category': by_category,
            'cost_metrics': {
                'total_cost_usd': self.generator.cost_tracker.total_cost,
                'budget_usd': self.generator.cost_tracker.WEEKLY_BUDGET_USD,
                'budget_remaining_usd': self.generator.cost_tracker.get_remaining_budget(),
                'total_input_tokens': self.generator.cost_tracker.total_input_tokens,
                'total_output_tokens': self.generator.cost_tracker.total_output_tokens,
            },
            'errors': errors[:10],  # First 10 errors
            'errors_total': len(errors),
        }

        # Save summary
        summary_file = self.output_dir / "pilot_summary.json"
        summary_file.write_text(json.dumps(self.summary, indent=2))

        # Print summary
        print()
        print("=" * 70)
        print("PILOT SUMMARY")
        print("=" * 70)
        print(f"Total tests: {total_generated}")
        print(f"Success: {total_succeeded} ({pass_rate:.1f}%)")
        print(f"Failed: {total_failed}")
        print(f"Duration: {duration_sec:.1f}s")
        print()
        print("By Category:")
        for category, metrics in by_category.items():
            success = metrics['success']
            failed = metrics['failed']
            total_cat = success + failed
            cat_rate = (success / total_cat * 100) if total_cat > 0 else 0
            print(f"  {category}: {success}/{total_cat} ({cat_rate:.1f}%)")
        print()
        print("Cost Metrics:")
        print(f"  Total cost: ${self.generator.cost_tracker.total_cost:.4f}")
        print(f"  Budget: ${self.generator.cost_tracker.WEEKLY_BUDGET_USD:.2f}")
        print(f"  Remaining: ${self.generator.cost_tracker.get_remaining_budget():.4f}")


if __name__ == '__main__':
    import sys

    runner = PilotRunner()
    summary = runner.run()

    sys.exit(0 if summary.get('pass_rate_percent', 0) >= 95 else 1)
