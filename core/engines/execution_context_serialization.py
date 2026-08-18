"""ExecutionContext Serialization (Phase 2, Week 10).

Serialize/deserialize ExecutionContext to JSON for transmission and storage.
"""

from __future__ import annotations

import json
import gzip
import base64
from typing import Optional, Any

from core.engines.execution_context import ExecutionContext


class ExecutionContextSerializer:
    """Serializes ExecutionContext to/from JSON."""

    SCHEMA_VERSION = 1

    @staticmethod
    def serialize(context: ExecutionContext, compress: bool = False) -> str:
        """Serialize ExecutionContext to JSON.

        Args:
            context: ExecutionContext to serialize
            compress: If True, gzip and base64 encode (for large contexts)

        Returns:
            JSON string (or compressed string if compress=True)
        """
        # Convert to dict
        data = context.to_dict()

        # Add schema version
        data["__schema_version__"] = ExecutionContextSerializer.SCHEMA_VERSION

        # Serialize to JSON
        json_str = json.dumps(data, indent=2)

        # Optionally compress
        if compress and len(json_str) > 1024:
            compressed = gzip.compress(json_str.encode('utf-8'))
            encoded = base64.b64encode(compressed).decode('utf-8')
            return f"__compressed__:{encoded}"

        return json_str

    @staticmethod
    def deserialize(json_str: str) -> ExecutionContext:
        """Deserialize ExecutionContext from JSON.

        Args:
            json_str: JSON string (or compressed string)

        Returns:
            ExecutionContext

        Raises:
            ValueError: If JSON is invalid or missing required fields
        """
        # Handle compressed format
        if json_str.startswith("__compressed__:"):
            try:
                encoded = json_str[len("__compressed__:"):]
                compressed = base64.b64decode(encoded)
                json_str = gzip.decompress(compressed).decode('utf-8')
            except Exception as e:
                raise ValueError(f"Failed to decompress: {e}")

        # Parse JSON
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}")

        # Check schema version
        schema_version = data.pop("__schema_version__", 1)
        if schema_version != ExecutionContextSerializer.SCHEMA_VERSION:
            # Could implement version migration here
            pass

        # Reconstruct ExecutionContext
        try:
            return ExecutionContext.from_dict(data)
        except TypeError as e:
            raise ValueError(f"Failed to reconstruct ExecutionContext: {e}")

    @staticmethod
    def round_trip_verify(context: ExecutionContext) -> bool:
        """Verify that context survives round-trip serialization.

        Returns:
            True if context == deserialize(serialize(context))
        """
        serialized = ExecutionContextSerializer.serialize(context)
        deserialized = ExecutionContextSerializer.deserialize(serialized)

        # Compare key fields
        return (
            context.task_id == deserialized.task_id and
            context.state == deserialized.state and
            context.output == deserialized.output and
            context.tokens_input == deserialized.tokens_input and
            context.tokens_output == deserialized.tokens_output and
            context.cost_cents == deserialized.cost_cents and
            context.audit_hash == deserialized.audit_hash
        )


class ContextVersionConverter:
    """Converts between v0.4 and v0.5 ExecutionContext formats."""

    @staticmethod
    def upgrade_v04_to_v05(context_dict: dict) -> dict:
        """Upgrade v0.4 context to v0.5.

        Adds new v0.5 fields with defaults:
        - routing_decision (new)
        - fallback_level (new)
        - engine_chain_attempted (new)
        """
        # Add v0.5 fields if missing
        if "routing_decision" not in context_dict:
            context_dict["routing_decision"] = None

        if "fallback_level" not in context_dict:
            context_dict["fallback_level"] = 0

        if "engine_chain_attempted" not in context_dict:
            context_dict["engine_chain_attempted"] = []

        return context_dict

    @staticmethod
    def downgrade_v05_to_v04(context_dict: dict) -> dict:
        """Downgrade v0.5 context to v0.4.

        Removes v0.5-only fields:
        - routing_decision
        - fallback_level
        - engine_chain_attempted
        """
        # Remove v0.5 fields
        context_dict.pop("routing_decision", None)
        context_dict.pop("fallback_level", None)
        context_dict.pop("engine_chain_attempted", None)

        return context_dict

    @staticmethod
    def get_schema_version(context_dict: dict) -> int:
        """Infer schema version from context dict."""
        # v0.5 has routing_decision field
        if "routing_decision" in context_dict:
            return 5
        # v0.4 doesn't have it
        else:
            return 4
