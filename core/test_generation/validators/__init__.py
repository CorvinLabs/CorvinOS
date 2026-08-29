"""Validators for generated test scenarios.

TypeScript syntax validation, schema validation, and test quality checks.
"""

import re
import json
from typing import Optional, List, Tuple, Dict, Any
from pathlib import Path
import subprocess


class TypeScriptValidator:
    """Validates TypeScript syntax in test expressions."""

    # Common Jest/Vitest/testing-library patterns
    VALID_PATTERNS = [
        r'^screen\.get(.*?)\(.*?\)$',
        r'^screen\.find(.*?)\(.*?\)$',
        r'^screen\.query(.*?)\(.*?\)$',
        r'^expect\(.*?\)\.(.*?)\(.*?\)$',
        r'^element\.(.*?)\(.*?\)$',
        r'^document\.querySelector(.*?)$',
        r'^document\.getElementById(.*?)$',
    ]

    # Forbidden patterns
    FORBIDDEN_PATTERNS = [
        r'eval\(',
        r'__proto__',
        r'constructor',
        r'innerHTML',  # XSS risk
    ]

    @staticmethod
    def validate_expression(expression: str) -> Tuple[bool, Optional[str]]:
        """Validate TypeScript expression syntax.

        Returns:
            (is_valid, error_message)
        """
        if not expression or not isinstance(expression, str):
            return (False, "Expression must be non-empty string")

        # Check for forbidden patterns
        for pattern in TypeScriptValidator.FORBIDDEN_PATTERNS:
            if re.search(pattern, expression):
                return (False, f"Expression contains forbidden pattern: {pattern}")

        # Check for balanced parentheses and braces
        if expression.count('(') != expression.count(')'):
            return (False, "Unbalanced parentheses")
        if expression.count('[') != expression.count(']'):
            return (False, "Unbalanced brackets")
        if expression.count('{') != expression.count('}'):
            return (False, "Unbalanced braces")

        # Check if it matches at least one valid pattern
        has_valid_pattern = any(re.match(pattern, expression.strip()) for pattern in TypeScriptValidator.VALID_PATTERNS)
        if not has_valid_pattern and not expression.startswith('expect('):
            return (False, "Expression does not match known valid patterns")

        return (True, None)

    @staticmethod
    def validate_action(action: str) -> Tuple[bool, Optional[str]]:
        """Validate test action type.

        Returns:
            (is_valid, error_message)
        """
        valid_actions = {
            'navigate', 'click', 'type', 'clear', 'submit', 'wait',
            'hover', 'focus', 'blur', 'check', 'uncheck', 'select',
            'rapid_click', 'type_boundary', 'submit_form', 'verify_consent',
            'audit_log', 'simulate_error', 'wait_for'
        }

        if action not in valid_actions:
            return (False, f"Action '{action}' not in valid actions: {sorted(valid_actions)}")

        return (True, None)


class SchemaValidator:
    """Validates TestScenario schema."""

    @staticmethod
    def validate_scenario_dict(data: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Validate raw scenario dictionary before creating TestScenario.

        Returns:
            (is_valid, error_messages)
        """
        errors = []

        # Required top-level fields
        required_fields = ['id', 'name', 'feature_id', 'category', 'description', 'steps', 'assertions']
        for field in required_fields:
            if field not in data:
                errors.append(f"Missing required field: {field}")
                continue
            if isinstance(data[field], str) and not data[field].strip():
                errors.append(f"Field '{field}' is empty string")

        # Validate category
        if 'category' in data:
            valid_categories = {'golden_path', 'happy_path', 'compliance', 'edge_case'}
            if data['category'] not in valid_categories:
                errors.append(f"Invalid category '{data['category']}'. Must be one of: {valid_categories}")

        # Validate steps
        if 'steps' in data:
            if not isinstance(data['steps'], list):
                errors.append("steps must be a list")
            elif len(data['steps']) == 0:
                errors.append("steps list cannot be empty")
            else:
                for i, step in enumerate(data['steps']):
                    if not isinstance(step, dict):
                        errors.append(f"step {i} must be a dict")
                        continue
                    if 'action' not in step or 'target' not in step:
                        errors.append(f"step {i} missing 'action' or 'target'")

        # Validate assertions
        if 'assertions' in data:
            if not isinstance(data['assertions'], list):
                errors.append("assertions must be a list")
            elif len(data['assertions']) == 0:
                errors.append("assertions list cannot be empty")
            else:
                for i, assertion in enumerate(data['assertions']):
                    if not isinstance(assertion, dict):
                        errors.append(f"assertion {i} must be a dict")
                        continue
                    if 'name' not in assertion or 'expression' not in assertion:
                        errors.append(f"assertion {i} missing 'name' or 'expression'")

        # Validate numeric fields
        if 'estimated_duration_sec' in data:
            try:
                duration = float(data['estimated_duration_sec'])
                if duration <= 0:
                    errors.append("estimated_duration_sec must be positive")
            except (ValueError, TypeError):
                errors.append("estimated_duration_sec must be a number")

        return (len(errors) == 0, errors)

    @staticmethod
    def validate_json_string(json_str: str) -> Tuple[bool, Optional[str]]:
        """Validate JSON string syntax.

        Returns:
            (is_valid, error_message)
        """
        try:
            json.loads(json_str)
            return (True, None)
        except json.JSONDecodeError as e:
            return (False, f"Invalid JSON: {e}")


class TestQualityValidator:
    """Validates overall test quality."""

    @staticmethod
    def validate_quality(scenario_data: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Validate test quality metrics.

        Returns:
            (meets_quality_threshold, issues)
        """
        issues = []

        # Description quality
        desc = scenario_data.get('description', '')
        if len(desc) < 20:
            issues.append("Description is too short (min 20 chars)")
        if len(desc) > 500:
            issues.append("Description is too long (max 500 chars)")

        # Preconditions/postconditions
        preconditions = scenario_data.get('preconditions', [])
        postconditions = scenario_data.get('postconditions', [])
        if not preconditions:
            issues.append("No preconditions specified")
        if not postconditions:
            issues.append("No postconditions specified")

        # Step quality
        steps = scenario_data.get('steps', [])
        if len(steps) < 2:
            issues.append("Minimum 2 steps required")
        if len(steps) > 10:
            issues.append("Maximum 10 steps allowed")

        for i, step in enumerate(steps):
            if 'expected_outcome' not in step or not step['expected_outcome'].strip():
                issues.append(f"Step {i} missing expected_outcome")

        # Assertion quality
        assertions = scenario_data.get('assertions', [])
        if len(assertions) < 2:
            issues.append("Minimum 2 assertions required")
        if len(assertions) > 5:
            issues.append("Maximum 5 assertions allowed")

        for i, assertion in enumerate(assertions):
            # Validate expression syntax
            expr = assertion.get('expression', '')
            is_valid, error = TypeScriptValidator.validate_expression(expr)
            if not is_valid:
                issues.append(f"Assertion {i} expression invalid: {error}")

        # Priority validation
        priority = scenario_data.get('priority', '')
        if priority not in {'low', 'medium', 'high'}:
            issues.append(f"Invalid priority '{priority}'")

        # Tags
        tags = scenario_data.get('tags', [])
        if not tags or len(tags) == 0:
            issues.append("At least one tag is required")

        return (len(issues) == 0, issues)


class ESLintValidator:
    """Validates TypeScript syntax using ESLint (when available)."""

    @staticmethod
    def validate_with_eslint(test_code: str) -> Tuple[bool, Optional[str]]:
        """Validate TypeScript/JavaScript syntax using ESLint.

        Returns:
            (is_valid, error_message)
        """
        try:
            # Try to run eslint
            result = subprocess.run(
                ['npx', 'eslint', '--stdin', '--parser-options=ecmaVersion=2021'],
                input=test_code,
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode != 0:
                return (False, result.stderr)

            return (True, None)

        except FileNotFoundError:
            # ESLint not available, skip this validation
            return (True, None)
        except subprocess.TimeoutExpired:
            return (False, "ESLint validation timed out")
        except Exception as e:
            return (False, f"ESLint validation error: {e}")

    @staticmethod
    def validate_with_tsc(test_code: str) -> Tuple[bool, Optional[str]]:
        """Validate TypeScript syntax using tsc (when available).

        Returns:
            (is_valid, error_message)
        """
        try:
            result = subprocess.run(
                ['tsc', '--noEmit', '--skipLibCheck'],
                input=test_code,
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode != 0:
                return (False, result.stderr)

            return (True, None)

        except FileNotFoundError:
            # TypeScript not available, skip this validation
            return (True, None)
        except subprocess.TimeoutExpired:
            return (False, "TypeScript validation timed out")
        except Exception as e:
            return (False, f"TypeScript validation error: {e}")
