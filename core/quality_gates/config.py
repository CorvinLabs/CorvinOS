"""Configuration for Quality Gates System (ADR-0688).

Gate configuration, thresholds, and defaults.
"""

from typing import Dict, Any, Type
from .validators import (
    BaseValidator,
    IdeaGateValidator,
    ConceptGateValidator,
    ADRGateValidator,
    ImplementationPlanGateValidator,
)


# Gate configuration defaults
GATE_CONFIG: Dict[str, Dict[str, Any]] = {
    "IdeaGate": {
        "validator_class": IdeaGateValidator,
        "min_evidence_count": 2,
        "min_recurrence_tasks": 2,
        "description": "Validates that ideas have sufficient evidence and recurrence",
    },
    "ConceptGate": {
        "validator_class": ConceptGateValidator,
        "min_narrative_length": 100,
        "min_evidence_commits": 2,
        "description": "Validates that concepts have complete narrative and evidence",
    },
    "ADRGate": {
        "validator_class": ADRGateValidator,
        "require_frontmatter": True,
        "description": "Validates that ADRs have complete frontmatter",
    },
    "ImplementationPlanGate": {
        "validator_class": ImplementationPlanGateValidator,
        "min_phases_defined": 3,
        "success_criteria_required": True,
        "description": "Validates that implementation plans have required structure",
    },
}


def load_gate_config(gate_name: str) -> Dict[str, Any]:
    """Load configuration for a specific gate.

    Args:
        gate_name: Name of gate (e.g., 'IdeaGate')

    Returns:
        Configuration dict

    Raises:
        KeyError: If gate_name not found
    """
    if gate_name not in GATE_CONFIG:
        raise KeyError(f"Unknown gate: {gate_name}")

    return GATE_CONFIG[gate_name]


def get_validator_class(gate_name: str) -> Type[BaseValidator]:
    """Get validator class for a gate.

    Args:
        gate_name: Name of gate

    Returns:
        Validator class

    Raises:
        KeyError: If gate_name not found
    """
    config = load_gate_config(gate_name)
    return config["validator_class"]


def list_gates() -> list:
    """List all available gates.

    Returns:
        List of gate names
    """
    return list(GATE_CONFIG.keys())


def get_gate_description(gate_name: str) -> str:
    """Get description of a gate.

    Args:
        gate_name: Name of gate

    Returns:
        Description string
    """
    config = load_gate_config(gate_name)
    return config.get("description", "")
