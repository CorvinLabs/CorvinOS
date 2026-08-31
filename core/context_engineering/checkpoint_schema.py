"""Schema validation for SessionCheckpoint to prevent injection attacks.

Provides JSON schema validation for all checkpoint JSON to prevent:
- Type confusion attacks
- Injection of arbitrary fields
- Deserialization gadgets (mitigated by JSON-only, no pickle)

ADR-0XXX: Checkpoint JSON Schema Validation (BR-002 remediation)
"""

import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

# Strict JSON schema for SessionCheckpoint
# Prevents unknown fields and type confusion
CHECKPOINT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": [
        "checkpoint_id", "task_id", "session_id", "tenant_id",
        "context_state", "decision_history", "checkpoints",
        "created_at", "last_activity_at"
    ],
    "properties": {
        "checkpoint_id": {
            "type": "string",
            "pattern": "^[a-f0-9-]{36}$",  # UUID format
            "description": "Unique checkpoint identifier (UUID)"
        },
        "task_id": {
            "type": "string",
            "maxLength": 256,
            "description": "Task this checkpoint belongs to"
        },
        "session_id": {
            "type": "string",
            "maxLength": 256,
            "description": "Session this checkpoint was created in"
        },
        "tenant_id": {
            "type": "string",
            "pattern": "^[a-zA-Z0-9_-]+$",
            "maxLength": 64,
            "description": "Tenant identifier"
        },

        "context_state": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "maxLength": 256},
                "tenant_id": {"type": "string", "maxLength": 64},
                "task_template": {"type": "object", "maxProperties": 100},
                "context_stack": {"type": "string", "maxLength": 1000},
                "budget_remaining": {"type": "number", "minimum": 0, "maximum": 1e9},
                "time_remaining": {"type": "integer", "minimum": 0, "maximum": 86400},
                "model": {"type": "string", "maxLength": 128},
                "strategy": {"type": "string", "maxLength": 256},
                "strategy_confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "guidance_overrides": {"type": "object", "maxProperties": 50},
            },
            "required": ["task_id", "tenant_id"],
            "additionalProperties": False,
            "maxProperties": 10,
            "description": "Serialized ExecutionContext state"
        },

        "decision_history": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "timestamp": {
                        "type": "string",
                        "format": "date-time",
                        "description": "Decision timestamp"
                    },
                    "subsystem": {
                        "type": "string",
                        "maxLength": 64,
                        "description": "Subsystem that made decision"
                    },
                    "decision_type": {
                        "type": "string",
                        "maxLength": 64,
                        "description": "Type of decision"
                    },
                    "value": {
                        "description": "Decision value (any JSON type)"
                    },
                    "reasoning": {
                        "type": "string",
                        "maxLength": 2000,
                        "description": "Reasoning for decision"
                    },
                    "context_stack": {
                        "type": "string",
                        "maxLength": 1000,
                        "description": "Context stack at time of decision"
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                        "description": "Confidence level"
                    },
                    "guidance_applied": {
                        "type": "boolean",
                        "description": "Whether guidance was applied"
                    },
                },
                "required": ["timestamp", "subsystem", "decision_type"],
                "additionalProperties": False,
                "description": "Single decision record"
            },
            "maxItems": 1000,
            "description": "History of decisions made"
        },

        "checkpoints": {
            "type": "array",
            "items": {"type": "object"},
            "maxItems": 100,
            "description": "Internal recovery checkpoints"
        },

        "created_at": {
            "type": "string",
            "format": "date-time",
            "description": "When checkpoint was created"
        },
        "last_activity_at": {
            "type": "string",
            "format": "date-time",
            "description": "Last activity timestamp"
        },

        "turn_number": {
            "type": "integer",
            "minimum": 0,
            "maximum": 1000000,
            "description": "Turn number this checkpoint represents"
        },
        "tokens_consumed": {
            "type": "integer",
            "minimum": 0,
            "maximum": 10000000,
            "description": "Total tokens consumed"
        },
        "cost_consumed_cents": {
            "type": "number",
            "minimum": 0,
            "maximum": 1000000,
            "description": "Cost in cents"
        },

        "error_recovery_state": {
            "oneOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "maxProperties": 50,
                    "description": "Error recovery metadata"
                }
            ]
        },

        "signature": {
            "type": ["string", "null"],
            "pattern": "^[a-f0-9]{64}$",
            "description": "HMAC-SHA256 signature (optional)"
        }
    },
    "additionalProperties": False,
    "maxProperties": 20,
    "description": "SessionCheckpoint data structure"
}


class CheckpointValidationError(ValueError):
    """Raised when checkpoint JSON fails schema validation."""
    pass


def validate_checkpoint_json(json_str: str) -> Dict[str, Any]:
    """
    Parse and validate checkpoint JSON against schema.

    Enforces fail-closed validation: any schema violation raises exception.

    Args:
        json_str: JSON string to validate

    Returns:
        Validated dict (safe to reconstruct)

    Raises:
        CheckpointValidationError: If JSON is invalid or violates schema
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise CheckpointValidationError(f"Invalid JSON syntax: {e}")

    if not isinstance(data, dict):
        raise CheckpointValidationError(
            f"Checkpoint must be JSON object, got {type(data).__name__}"
        )

    # Validate using jsonschema (simple implementation without external dependency)
    errors = _validate_against_schema(data, CHECKPOINT_SCHEMA)

    if errors:
        error_summary = "; ".join(errors[:3])  # Show first 3 errors
        logger.warning(f"Checkpoint validation failed: {error_summary}")
        raise CheckpointValidationError(
            f"Checkpoint validation failed: {error_summary}"
        )

    logger.debug(f"Checkpoint validation passed (id={data.get('checkpoint_id')})")
    return data


def _validate_against_schema(data: Any, schema: Dict[str, Any]) -> list[str]:
    """
    Simple schema validator without external dependencies.

    Returns list of validation error messages (empty if valid).
    """
    errors = []

    # Check type
    expected_type = schema.get("type")
    if expected_type and type(data).__name__ != expected_type:
        errors.append(
            f"Expected {expected_type}, got {type(data).__name__}"
        )
        return errors  # Can't validate further if type is wrong

    # Check required fields (for objects)
    if expected_type == "object":
        required = schema.get("required", [])
        for field in required:
            if field not in data:
                errors.append(f"Missing required field: {field}")

        # Check for additional properties
        allowed_props = set(schema.get("properties", {}).keys())
        additional_allowed = schema.get("additionalProperties", True)

        for key in data.keys():
            if key not in allowed_props and not additional_allowed:
                errors.append(f"Unknown property: {key}")

    # Check maxProperties
    if "maxProperties" in schema and isinstance(data, dict):
        if len(data) > schema["maxProperties"]:
            errors.append(
                f"Object has {len(data)} properties, max is {schema['maxProperties']}"
            )

    # Validate specific properties if schema defines them
    if "properties" in schema and isinstance(data, dict):
        for prop, prop_schema in schema["properties"].items():
            if prop in data:
                prop_errors = _validate_against_schema(data[prop], prop_schema)
                errors.extend([f"{prop}: {e}" for e in prop_errors])

    # Check string patterns and limits
    if isinstance(data, str) and expected_type == "string":
        if "pattern" in schema:
            import re
            pattern = schema["pattern"]
            if not re.match(pattern, data):
                errors.append(f"String doesn't match pattern {pattern}")

        if "maxLength" in schema and len(data) > schema["maxLength"]:
            errors.append(
                f"String length {len(data)} exceeds max {schema['maxLength']}"
            )

    # Check numeric ranges
    if isinstance(data, (int, float)):
        if "minimum" in schema and data < schema["minimum"]:
            errors.append(f"Value {data} is below minimum {schema['minimum']}")
        if "maximum" in schema and data > schema["maximum"]:
            errors.append(f"Value {data} exceeds maximum {schema['maximum']}")

    # Check arrays
    if isinstance(data, list) and expected_type == "array":
        if "maxItems" in schema and len(data) > schema["maxItems"]:
            errors.append(
                f"Array has {len(data)} items, max is {schema['maxItems']}"
            )

        if "items" in schema:
            for idx, item in enumerate(data):
                item_errors = _validate_against_schema(item, schema["items"])
                errors.extend([f"[{idx}]: {e}" for e in item_errors])

    return errors
