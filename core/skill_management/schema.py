"""JSON Schema definition for skill metadata."""

SKILL_METADATA_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "Skill Metadata",
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
            "pattern": "^[a-z0-9_-]+$",
            "minLength": 1,
            "maxLength": 100,
            "description": "Skill identifier (lowercase, alphanumeric, hyphens, underscores)"
        },
        "name": {
            "type": "string",
            "minLength": 1,
            "maxLength": 200,
            "description": "Human-readable skill name"
        },
        "version": {
            "type": "string",
            "pattern": "^(0|[1-9]\\d*)\\.(0|[1-9]\\d*)\\.(0|[1-9]\\d*)(?:-((?:0|[1-9]\\d*|\\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\\.(?:0|[1-9]\\d*|\\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?(?:\\+([0-9a-zA-Z-]+(?:\\.[0-9a-zA-Z-]+)*))?$",
            "description": "Semantic version (major.minor.patch)"
        },
        "scope": {
            "type": "string",
            "enum": ["_platform", "_shared", "_local"],
            "description": "Skill scope/layer"
        },
        "created": {
            "type": "string",
            "format": "date-time",
            "description": "ISO 8601 creation timestamp"
        },
        "last_modified": {
            "type": "string",
            "format": "date-time",
            "description": "ISO 8601 last modification timestamp"
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Searchable tags"
        },
        "dependencies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "pattern": "^[a-z0-9_-]+$"},
                    "scope": {"type": "string", "enum": ["_platform", "_shared", "_local"]},
                    "min_version": {"type": "string", "pattern": "^\\d+\\.\\d+\\.\\d+"}
                },
                "required": ["id", "scope"],
                "additionalProperties": False
            },
            "description": "Transitive dependencies on other skills"
        },
        "compatibility": {
            "type": "object",
            "properties": {
                "corvinOS_min": {"type": "string"},
                "python_min": {"type": "string", "pattern": "^\\d+\\.\\d+"}
            },
            "description": "Compatibility constraints"
        },
        "metrics": {
            "type": "object",
            "properties": {
                "auto_grade_score": {"type": "number", "minimum": 0, "maximum": 1},
                "usage_count": {"type": "integer", "minimum": 0},
                "success_rate": {"type": "number", "minimum": 0, "maximum": 1},
                "last_used": {"type": ["string", "null"], "format": "date-time"}
            },
            "description": "Usage and quality metrics"
        },
        "exported_to": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "branch": {"type": "string"},
                    "path": {"type": "string"},
                    "last_export": {"type": "string", "format": "date-time"},
                    "export_hash": {"type": "string"}
                },
                "required": ["repo", "branch"]
            },
            "description": "GitHub export history"
        },
        "audit_trail": {
            "type": "object",
            "properties": {
                "created_by": {"type": "string"},
                "created_session": {"type": "string"},
                "last_modified_by": {"type": "string"},
                "last_modified_session": {"type": "string"}
            },
            "description": "Audit metadata"
        }
    },
    "required": ["id", "version", "scope", "created", "last_modified"],
    "additionalProperties": False
}

TOOL_METADATA_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "Tool Metadata",
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
            "pattern": "^[a-z0-9_-]+$",
            "description": "Tool identifier"
        },
        "name": {"type": "string"},
        "version": {
            "type": "string",
            "pattern": "^\\d+\\.\\d+\\.\\d+$"
        },
        "scope": {"type": "string", "enum": ["_platform", "_shared", "_local"]},
        "type": {"type": "string", "enum": ["forge_tool", "mcp_server"]},
        "runtime": {"type": "string"},
        "dependencies": {
            "type": "object",
            "description": "External dependencies (python packages, etc.)"
        },
        "cost_metadata": {
            "type": "object",
            "properties": {
                "cost_units_per_run": {"type": "integer"},
                "cost_units_per_gb_input": {"type": "integer"},
                "estimated_duration_seconds": {"type": "number"}
            }
        }
    },
    "required": ["id", "version", "scope", "type"],
    "additionalProperties": False
}
