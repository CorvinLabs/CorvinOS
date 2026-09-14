"""Phase 2: Unified Creator — Generate artifacts from data."""

from .models import CreationRequest, CreationResult, CreationType
from datetime import datetime


class UnifiedCreator:
    """Create Skills/Tools/Datasets/Pipelines from ingested data (Phase 2)."""

    def __init__(self):
        self.templates = self._load_templates()

    def create(self, request: CreationRequest) -> CreationResult:
        """Create artifact from data request (Phases 2-7: analysis → validation → generation)."""

        # Phase 2: Analyze complexity
        complexity_score = self._analyze_complexity(request)

        # Phase 3: Generate artifact
        artifact_body = self._generate_artifact(request, complexity_score)

        # Phase 4: Validate artifact
        validation_errors = self._validate_artifact(artifact_body, request.creation_type)

        # Phase 5: Add tests
        tests = self._generate_tests(artifact_body, request.creation_type)

        # Phase 6: Optimize
        artifact_body = self._optimize_artifact(artifact_body)

        # Phase 7: Final check
        is_valid = len(validation_errors) == 0 or not request.fail_closed

        return CreationResult(
            success=is_valid,
            artifact_name=request.name,
            artifact_type=request.creation_type,
            artifact_body=artifact_body,
            generated_at=datetime.utcnow().isoformat() + "Z",
            test_count=len(tests),
            validation_errors=validation_errors,
        )

    def _analyze_complexity(self, request: CreationRequest) -> float:
        """Analyze data complexity (Phase 2)."""

        # Simple heuristic
        row_count = request.data.sample_rows

        if row_count < 100:
            return 0.2  # Low
        elif row_count < 1000:
            return 0.5  # Medium
        else:
            return 0.8  # High

    def _generate_artifact(self, request: CreationRequest, complexity: float) -> str:
        """Generate artifact body (Phase 3)."""

        if request.creation_type == CreationType.SKILL:
            return self._generate_skill(request, complexity)
        elif request.creation_type == CreationType.TOOL:
            return self._generate_tool(request, complexity)
        elif request.creation_type == CreationType.DATASET:
            return self._generate_dataset(request, complexity)
        elif request.creation_type == CreationType.PIPELINE:
            return self._generate_pipeline(request, complexity)

        return ""

    def _generate_skill(self, request: CreationRequest, complexity: float) -> str:
        """Generate a Skill from data (Phase 3: Skill path)."""

        return f"""# {request.name}

**Type:** Learned Experience
**Generated:** Automated from data
**Complexity:** {request.complexity}

## Overview

{request.description}

## Pattern

Based on analysis of {request.data.sample_rows} rows:
- Data type: {request.data.source_type.value}
- Complexity score: {complexity:.1%}

## When to Use

Use this skill when working with {request.data.source_type.value} data sources.

## Examples

See embedded data analysis for concrete examples.
"""

    def _generate_tool(self, request: CreationRequest, complexity: float) -> str:
        """Generate a Tool from data."""

        return f"""# {request.name} Tool

**Input:** {request.data.source_type.value} data
**Output:** Processed data

## Function

```python
def {request.name.lower().replace('-', '_')}(data):
    '''Process data from {request.data.source_type.value} source.'''
    return data
```

## Description

{request.description}
"""

    def _generate_dataset(self, request: CreationRequest, complexity: float) -> str:
        """Generate a Dataset from data."""

        return f"""# {request.name} Dataset

**Source:** {request.data.source_type.value}
**Rows:** {request.data.sample_rows}

## Overview

{request.description}

## Schema

[Inferred from source]
"""

    def _generate_pipeline(self, request: CreationRequest, complexity: float) -> str:
        """Generate a Pipeline from data."""

        return f"""# {request.name} Pipeline

**Stage 1:** Ingest {request.data.source_type.value}
**Stage 2:** Analyze
**Stage 3:** Transform
**Stage 4:** Output

## Steps

1. Load data
2. Validate schema
3. Apply transformations
4. Output result
"""

    def _validate_artifact(self, body: str, artifact_type: CreationType) -> list:
        """Validate generated artifact (Phase 4)."""

        errors = []

        # Basic checks
        if not body or len(body) < 50:
            errors.append("Artifact too short")

        if "\n" not in body:
            errors.append("Artifact missing structure")

        # Type-specific checks
        if artifact_type == CreationType.SKILL and "Pattern" not in body:
            errors.append("Skill missing Pattern section")

        return errors

    def _generate_tests(self, body: str, artifact_type: CreationType) -> list:
        """Generate tests for artifact (Phase 5)."""

        tests = []

        if artifact_type == CreationType.SKILL:
            tests.append("test_skill_generated")
            tests.append("test_skill_pattern_present")

        elif artifact_type == CreationType.TOOL:
            tests.append("test_tool_callable")
            tests.append("test_tool_input_validation")

        return tests

    def _optimize_artifact(self, body: str) -> str:
        """Optimize artifact (Phase 6)."""

        # Remove extra whitespace
        lines = [line.rstrip() for line in body.split('\n')]
        return '\n'.join(lines)

    def _load_templates(self) -> dict:
        """Load artifact templates."""

        return {
            CreationType.SKILL: "# Skill Template\n\n## Pattern\n\n## Examples",
            CreationType.TOOL: "def tool(): pass",
            CreationType.DATASET: "# Dataset\n\n## Schema",
            CreationType.PIPELINE: "# Pipeline\n\n## Steps",
        }
