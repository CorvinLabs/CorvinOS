# Validation Suite Layers 1–2: Usage Guide

## Overview

The Autonomous Skill Forge Validation Suite provides two deterministic validation layers for generated skills:

- **Layer 1: Structural Validation** — Checks manifest integrity, schema compliance, and folder structure (deterministic, no subprocess)
- **Layer 2: Test-Based Validation** — Runs pytest, validates coverage ≥85% (subprocess-based)

Both layers implement **fail-closed** semantics: any error causes validation to fail.

## Quick Start

### Basic Usage

```python
from pathlib import Path
from autonomous.validator import SkillValidator

# Create validator
validator = SkillValidator()

# Validate all layers
skill_dir = Path("/path/to/skill_id_v1.2.3")
results = validator.validate_all_layers(skill_dir)

# Check results
for result in results:
    if result.passed:
        print(f"✅ Layer {result.layer}: PASS ({result.metrics})")
    else:
        print(f"❌ Layer {result.layer}: FAIL")
        for error in result.errors:
            print(f"  - {error}")
```

### Verbose Mode

```python
overall_passed, results = validator.validate_all_layers_verbose(skill_dir)

if overall_passed:
    print("✅ All layers passed!")
else:
    print("❌ Validation failed")
    for result in results:
        print(result)  # Uses ValidationResult.__str__()
```

### Layer Selection (Bitmask)

```python
# Run only Layer 1
results = validator.validate_all_layers(skill_dir, layer_mask=0b01)

# Run only Layer 2
results = validator.validate_all_layers(skill_dir, layer_mask=0b10)

# Run both (default)
results = validator.validate_all_layers(skill_dir, layer_mask=0b11)
```

## Skill Directory Structure

A valid skill must have this structure:

```
skill_id/
├── skill.json           # Manifest with required fields
├── src/
│   ├── skill.py         # Implementation (≥1 .py file required)
│   └── ...
├── tests/
│   ├── test_skill.py    # Tests matching test_*.py pattern
│   └── ...
├── hooks/               # Must exist
│   └── ...
└── scripts/             # Must exist
    └── ...
```

### skill.json Required Fields

```json
{
  "id": "skill.id",
  "version": "1.0.0",
  "capabilities": ["inference"],
  "dependencies": [],
  "hooks": [],
  "lom_binding": "skill.id:execute"
}
```

**Required fields:** `id`, `version`, `capabilities`, `dependencies`, `hooks`, `lom_binding`

**Field types:**
- `id` (string): Skill identifier
- `version` (string): Semantic version (X.Y.Z format)
- `capabilities` (list or dict): Capability list
- `dependencies` (list or dict): Dependency list
- `hooks` (list or dict): Hook list
- `lom_binding` (string or dict): Line of Moral Responsibility binding

## Layer 1: Structural Validation

Validates:
- Manifest exists and is valid JSON
- All required fields present in schema
- All required folders (src/, tests/, hooks/, scripts/) exist
- src/ contains ≥1 .py file
- tests/ contains ≥1 test_*.py file
- Field types are correct
- Version follows semantic versioning (X.Y.Z)

**Errors** (fail-closed):
- Missing or invalid manifest
- Missing required fields
- Missing required folders
- Wrong field types

**Warnings** (non-blocking):
- Non-semver version format
- Empty src/ or tests/ folders

**Example:**
```python
from autonomous.validator_layer1 import StructuralValidator

validator = StructuralValidator()
result = validator.validate(Path("/path/to/skill"))

print(f"Passed: {result.passed}")
print(f"Errors: {result.errors}")
print(f"Warnings: {result.warnings}")
print(f"Metrics: {result.metrics}")
print(f"Duration: {result.duration_ms}ms")
```

## Layer 2: Test-Based Validation

Validates (requires pytest):
- tests/ directory exists
- ≥1 test_*.py file exists
- All tests pass (exit code 0)
- Code coverage ≥85% (configurable)

**Errors** (fail-closed):
- No tests/ directory
- No test files
- Test failures
- Coverage below minimum (default 85%)
- pytest not found in PATH

**Metrics:**
- `test_files_found`: Number of test files discovered
- `tests_passed`: Number of passing tests
- `tests_failed`: Number of failing tests
- `coverage_percent`: Code coverage percentage

**Example:**
```python
from autonomous.validator_layer2 import TestValidator

validator = TestValidator(min_coverage=85.0)
result = validator.validate(Path("/path/to/skill"))

if result.passed:
    print(f"Coverage: {result.metrics['coverage_percent']}%")
else:
    print(f"Coverage error: {result.errors[0]}")
```

## ValidationResult Dataclass

```python
@dataclass
class ValidationResult:
    layer: int                           # 1, 2, 3, ...
    passed: bool                        # Overall result
    errors: list[str]                  # Fail-closed errors
    warnings: list[str]                # Non-blocking warnings
    metrics: dict[str, Any]            # coverage %, test count, etc.
    duration_ms: float                 # Validation time
```

**Example output:**
```
✅ Layer 1: PASS (src_python_files=2, test_files=3, manifest_file_size=245)
❌ Layer 2: FAIL (coverage_percent=72.5) — Errors: Code coverage 72.5% is below minimum 85.0%
```

## Fail-Closed Design

Both layers implement **fail-closed** semantics:

1. **Layer 1 fails** → Stop; don't run Layer 2 (structural problems first)
2. **Layer 2 fails** → Validation fails (test/coverage problems)

No layer is "skipped" or "ignored" — all enabled layers must pass.

## Exit Codes

Use in automation:

```python
import sys
from pathlib import Path
from autonomous.validator import SkillValidator

validator = SkillValidator()
overall, results = validator.validate_all_layers_verbose(Path(sys.argv[1]))

sys.exit(0 if overall else 1)
```

## Troubleshooting

### "pytest not found in PATH"
Install pytest: `pip install pytest pytest-cov`

### "Missing required fields"
Check skill.json has all required fields: id, version, capabilities, dependencies, hooks, lom_binding

### "Coverage below minimum"
Increase test coverage or lower min_coverage: `TestValidator(min_coverage=80.0)`

### "No test files found"
Add test files matching test_*.py pattern to tests/ folder
