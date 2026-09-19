"""
MCP Tool Registry — defines 6 KG query tools for the MCP server.
Each tool has a JSON-RPC schema with input/output validation.
"""

from typing import Dict, Any, List
from enum import Enum


class ToolName(str, Enum):
    """MCP tool names (must start with kg:)"""
    QUERY_ENTITY = "kg:query_entity"
    SEARCH = "kg:search"
    LIST_RELATIONS = "kg:list_relations"
    GET_SCHEMA = "kg:get_schema"
    ADR_DEPENDENCY_GRAPH = "kg:adr_dependency_graph"
    AUDIT_TRAIL_LINKS = "kg:audit_trail_links"


# Tool: kg:query_entity
QUERY_ENTITY_SCHEMA = {
    "name": "kg:query_entity",
    "description": "Query a single entity by ID from the Knowledge Graph",
    "inputSchema": {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "Entity ID (e.g., 'ADR-0516', 'CONCEPT-0001')"
            },
            "tenant_id": {
                "type": ["string", "null"],
                "description": "Tenant ID (optional, defaults to current_tenant())"
            }
        },
        "required": ["entity_id"]
    },
    "outputSchema": {
        "type": "object",
        "description": "Entity record with all fields and related entities"
    }
}


# Tool: kg:search
SEARCH_SCHEMA = {
    "name": "kg:search",
    "description": "Full-text search across the Knowledge Graph",
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (e.g., 'ADR dependency', 'learning loop')"
            },
            "limit": {
                "type": "integer",
                "description": "Max results (1-100, default 10)",
                "minimum": 1,
                "maximum": 100,
                "default": 10
            },
            "tenant_id": {
                "type": ["string", "null"],
                "description": "Tenant ID (optional, defaults to current_tenant())"
            }
        },
        "required": ["query"]
    },
    "outputSchema": {
        "type": "array",
        "description": "List of matching entities with search scores",
        "items": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "score": {"type": "number"},
                "entity_type": {"type": "string"},
                "summary": {"type": "string"}
            }
        }
    }
}


# Tool: kg:list_relations
LIST_RELATIONS_SCHEMA = {
    "name": "kg:list_relations",
    "description": "List outgoing relations from an entity (graph edges)",
    "inputSchema": {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "Source entity ID"
            },
            "tenant_id": {
                "type": ["string", "null"],
                "description": "Tenant ID (optional, defaults to current_tenant())"
            }
        },
        "required": ["entity_id"]
    },
    "outputSchema": {
        "type": "array",
        "description": "List of (target_id, relation_type, attributes) tuples",
        "items": {
            "type": "object",
            "properties": {
                "target_id": {"type": "string"},
                "relation_type": {"type": "string"},
                "attributes": {"type": "object"}
            }
        }
    }
}


# Tool: kg:get_schema
GET_SCHEMA_SCHEMA = {
    "name": "kg:get_schema",
    "description": "Introspect entity type schema (fields, constraints, types)",
    "inputSchema": {
        "type": "object",
        "properties": {
            "entity_type": {
                "type": "string",
                "description": "Entity type (e.g., 'ADR', 'Task', 'Entity', 'CONCEPT')"
            },
            "tenant_id": {
                "type": ["string", "null"],
                "description": "Tenant ID (optional, defaults to current_tenant())"
            }
        },
        "required": ["entity_type"]
    },
    "outputSchema": {
        "type": "object",
        "description": "Schema definition with field specs, constraints, examples",
        "properties": {
            "entity_type": {"type": "string"},
            "fields": {
                "type": "object",
                "description": "Field name -> {type, required, description}"
            },
            "constraints": {"type": "array"},
            "examples": {"type": "array"}
        }
    }
}


# Tool: kg:adr_dependency_graph
ADR_DEPENDENCY_GRAPH_SCHEMA = {
    "name": "kg:adr_dependency_graph",
    "description": "Trace ADR dependency graph (depends_on chain and related ADRs)",
    "inputSchema": {
        "type": "object",
        "properties": {
            "adr_id": {
                "type": "string",
                "description": "ADR ID (e.g., 'ADR-0516')"
            },
            "tenant_id": {
                "type": ["string", "null"],
                "description": "Tenant ID (optional, defaults to current_tenant())"
            }
        },
        "required": ["adr_id"]
    },
    "outputSchema": {
        "type": "object",
        "description": "ADR with dependency graph, related ADRs, full DAG",
        "properties": {
            "adr_id": {"type": "string"},
            "title": {"type": "string"},
            "status": {"type": "string"},
            "depends_on": {
                "type": "array",
                "description": "ADRs this one depends on"
            },
            "depended_by": {
                "type": "array",
                "description": "ADRs depending on this one"
            },
            "related": {
                "type": "array",
                "description": "Related ADRs"
            },
            "dag": {
                "type": "object",
                "description": "Full dependency DAG"
            }
        }
    }
}


# Tool: kg:audit_trail_links
AUDIT_TRAIL_LINKS_SCHEMA = {
    "name": "kg:audit_trail_links",
    "description": "Find audit events linked to an entity (cross-reference audit trail)",
    "inputSchema": {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "Entity ID to cross-reference"
            },
            "tenant_id": {
                "type": ["string", "null"],
                "description": "Tenant ID (optional, defaults to current_tenant())"
            }
        },
        "required": ["entity_id"]
    },
    "outputSchema": {
        "type": "array",
        "description": "List of audit events linked to this entity",
        "items": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "event_type": {"type": "string"},
                "timestamp": {"type": "string"},
                "entity_id": {"type": "string"},
                "status": {"type": "string"}
            }
        }
    }
}


# All tool schemas
ALL_TOOLS = [
    QUERY_ENTITY_SCHEMA,
    SEARCH_SCHEMA,
    LIST_RELATIONS_SCHEMA,
    GET_SCHEMA_SCHEMA,
    ADR_DEPENDENCY_GRAPH_SCHEMA,
    AUDIT_TRAIL_LINKS_SCHEMA
]


def get_tool_by_name(tool_name: str) -> Dict[str, Any]:
    """
    Retrieve tool schema by name.

    Args:
        tool_name: Tool name (e.g., "kg:query_entity")

    Returns:
        Tool schema dict

    Raises:
        ValueError: If tool not found
    """
    for tool in ALL_TOOLS:
        if tool["name"] == tool_name:
            return tool
    raise ValueError(f"Unknown tool: {tool_name}")


def validate_tool_input(tool_name: str, input_params: Dict[str, Any]) -> bool:
    """
    Validate input against tool schema.

    Args:
        tool_name: Tool name
        input_params: Input parameters to validate

    Returns:
        True if valid

    Raises:
        ValueError: If validation fails
    """
    tool = get_tool_by_name(tool_name)
    schema = tool["inputSchema"]

    # Basic validation: check required fields
    required = schema.get("required", [])
    for field in required:
        if field not in input_params:
            raise ValueError(f"Missing required field: {field}")

    # Check field types
    for field, value in input_params.items():
        if field in schema.get("properties", {}):
            prop_schema = schema["properties"][field]
            expected_type = prop_schema.get("type")

            if isinstance(expected_type, list):
                # Multiple types allowed
                if value is None and "null" in expected_type:
                    continue
                expected_type = [t for t in expected_type if t != "null"][0]

            if expected_type == "string" and not isinstance(value, str):
                raise ValueError(f"Field '{field}' must be string, got {type(value).__name__}")
            elif expected_type == "integer" and not isinstance(value, int):
                raise ValueError(f"Field '{field}' must be integer, got {type(value).__name__}")
            elif expected_type == "array" and not isinstance(value, list):
                raise ValueError(f"Field '{field}' must be array, got {type(value).__name__}")

    return True
