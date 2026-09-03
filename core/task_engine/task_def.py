"""Task definition schema and parser (ADR-0540)."""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
import json


@dataclass
class Gate:
    """Success criterion for a phase (ADR-0542)."""
    type: str  # "finding_count", "test_pass_rate", "drift_detection", "custom_check"
    config: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Gate":
        gate_type = data.pop("type")
        return cls(type=gate_type, config=data)


@dataclass
class Phase:
    """Task phase definition (ADR-0540)."""
    id: str
    goal: str
    skills: List[str]  # Skills to dispatch
    gates: List[Gate]
    depends_on: List[str] = field(default_factory=list)
    timeout_hours: int = 24
    parallelizable: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Phase":
        gates = [Gate.from_dict(g) for g in data.pop("gates", [])]
        depends_on = data.pop("depends_on", [])
        parallelizable = data.pop("parallelizable", [])
        return cls(gates=gates, depends_on=depends_on, parallelizable=parallelizable, **data)


@dataclass
class TaskDefinition:
    """Root task definition (JSON-LD format, ADR-0540)."""
    task_id: str
    tenant_id: str = "_default"
    description: str = ""
    phases: List[Phase] = field(default_factory=list)
    autonomy_level: int = 2  # 1-5, see ADR-0540
    success_criteria: Dict[str, Any] = field(default_factory=dict)
    timeout_weeks: int = 4

    @classmethod
    def from_json(cls, json_str: str) -> "TaskDefinition":
        """Parse task definition from JSON-LD string."""
        data = json.loads(json_str)
        phases = [Phase.from_dict(p) for p in data.pop("phases", [])]
        success_criteria = data.pop("success_criteria", {})
        return cls(phases=phases, success_criteria=success_criteria, **data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskDefinition":
        """Parse from dict."""
        phases = [Phase.from_dict(p) for p in data.pop("phases", [])]
        success_criteria = data.pop("success_criteria", {})
        return cls(phases=phases, success_criteria=success_criteria, **data)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "description": self.description,
            "phases": [asdict(p) for p in self.phases],
            "autonomy_level": self.autonomy_level,
            "success_criteria": self.success_criteria,
            "timeout_weeks": self.timeout_weeks,
        }


def asdict(obj):
    """Convert dataclass to dict recursively."""
    if hasattr(obj, "__dataclass_fields__"):
        result = {}
        for field_name, field_obj in obj.__dataclass_fields__.items():
            value = getattr(obj, field_name)
            if hasattr(value, "__dataclass_fields__"):
                result[field_name] = asdict(value)
            elif isinstance(value, list):
                result[field_name] = [asdict(v) if hasattr(v, "__dataclass_fields__") else v for v in value]
            else:
                result[field_name] = value
        return result
    return obj
