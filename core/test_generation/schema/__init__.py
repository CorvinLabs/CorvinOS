"""TestScenario schema and validation for Phase 2 Test Generation.

Defines immutable, JSON-serializable schema for generated test scenarios.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any, Literal
from enum import Enum
import json


class TestCategory(str, Enum):
    """Test generation categories."""
    GOLDEN_PATH = "golden_path"
    HAPPY_PATH = "happy_path"
    COMPLIANCE = "compliance"
    EDGE_CASE = "edge_case"


class TestStatus(str, Enum):
    """Test execution status."""
    PENDING = "pending"
    GENERATED = "generated"
    VALIDATED = "validated"
    EXECUTED = "executed"
    FAILED = "failed"


@dataclass
class TestAssertion:
    """Single test assertion."""
    name: str
    description: str
    expression: str  # TypeScript expression
    expected_result: str

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> 'TestAssertion':
        return cls(**data)


@dataclass
class TestStep:
    """Single test execution step."""
    order: int
    action: str  # e.g., "click", "submit", "call"
    target: str  # element/method identifier
    params: Dict[str, Any] = field(default_factory=dict)
    expected_outcome: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TestStep':
        return cls(**data)


@dataclass
class TestScenario:
    """Complete test scenario definition.

    Immutable schema for LLM-generated test cases.
    """
    # Metadata
    id: str
    name: str
    feature_id: str
    category: TestCategory

    # Description
    description: str
    preconditions: List[str] = field(default_factory=list)
    postconditions: List[str] = field(default_factory=list)

    # Test definition
    steps: List[TestStep] = field(default_factory=list)
    assertions: List[TestAssertion] = field(default_factory=list)

    # Metadata
    author: str = "test_generator"
    tags: List[str] = field(default_factory=list)
    priority: Literal["low", "medium", "high"] = "medium"
    estimated_duration_sec: float = 5.0

    # Tracking
    status: TestStatus = TestStatus.GENERATED
    prompt_version: str = "1.0"
    llm_model: str = "claude-opus"
    generation_timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary (preserving enums as strings)."""
        data = asdict(self)
        data['category'] = self.category.value
        data['status'] = self.status.value
        data['steps'] = [s.to_dict() for s in self.steps]
        data['assertions'] = [a.to_dict() for a in self.assertions]
        return data

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TestScenario':
        """Create from dictionary."""
        # Parse nested objects
        if isinstance(data.get('category'), str):
            data['category'] = TestCategory(data['category'])
        if isinstance(data.get('status'), str):
            data['status'] = TestStatus(data['status'])

        steps = data.pop('steps', [])
        assertions = data.pop('assertions', [])

        obj = cls(**data)
        obj.steps = [TestStep.from_dict(s) if isinstance(s, dict) else s for s in steps]
        obj.assertions = [TestAssertion.from_dict(a) if isinstance(a, dict) else a for a in assertions]

        return obj

    @classmethod
    def from_json(cls, json_str: str) -> 'TestScenario':
        """Create from JSON string."""
        data = json.loads(json_str)
        return cls.from_dict(data)

    def validate(self) -> tuple[bool, List[str]]:
        """Validate schema integrity.

        Returns:
            (is_valid, error_messages)
        """
        errors = []

        if not self.id or not self.id.strip():
            errors.append("id must be non-empty string")
        if not self.name or not self.name.strip():
            errors.append("name must be non-empty string")
        if not self.feature_id or not self.feature_id.strip():
            errors.append("feature_id must be non-empty string")
        if not isinstance(self.category, TestCategory):
            errors.append(f"category must be TestCategory, got {type(self.category)}")
        if not self.description or len(self.description.strip()) < 10:
            errors.append("description must be at least 10 characters")

        if not self.steps:
            errors.append("at least one step is required")
        for i, step in enumerate(self.steps):
            if not step.action or not step.target:
                errors.append(f"step {i} missing action or target")

        if not self.assertions:
            errors.append("at least one assertion is required")
        for i, assertion in enumerate(self.assertions):
            if not assertion.name or not assertion.expression:
                errors.append(f"assertion {i} missing name or expression")

        if self.estimated_duration_sec <= 0:
            errors.append("estimated_duration_sec must be positive")

        return (len(errors) == 0, errors)
