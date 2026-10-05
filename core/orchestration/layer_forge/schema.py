"""Layer-Definition manifest schema and validation (ADR-2222 D1/D2).

A layer-definition is a versioned configuration artifact: it names the layers
it targets, its dependencies on other registry entries, its quality gates,
its enforcement rules, and the host-awareness paths that must match between
the source tree and a deployed runtime.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from pathlib import Path

JSON_SCHEMA: dict = {
    "type": "object",
    "required": ["id", "version", "targets"],
    "properties": {
        "id": {"type": "string", "pattern": r"^[a-z0-9][a-z0-9._-]*$"},
        "version": {"type": "string", "pattern": r"^\d+\.\d+\.\d+$"},
        "type": {"type": "string", "enum": ["layer_definition"]},
        "targets": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["layer_id"],
                "properties": {
                    "layer_id": {"type": "string"},
                    "layer_name": {"type": "string"},
                },
            },
        },
        "dependencies": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "type"],
                "properties": {
                    "id": {"type": "string"},
                    "type": {"type": "string", "enum": ["layer_definition", "skill", "tool"]},
                },
            },
        },
        "quality_gates": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["gate_id", "test_path"],
                "properties": {
                    "gate_id": {"type": "string"},
                    "description": {"type": "string"},
                    "test_path": {"type": "string"},
                    "severity": {"type": "string", "enum": ["CRITICAL", "WARNING"]},
                },
            },
        },
        "enforcement_rules": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["rule_id", "type"],
                "properties": {
                    "rule_id": {"type": "string"},
                    "type": {"type": "string", "enum": ["compile_time", "boot_time"]},
                    "description": {"type": "string"},
                },
            },
        },
        "host_awareness": {
            "type": "object",
            "properties": {
                "source_tree": {
                    "type": "object",
                    "properties": {"paths": {"type": "array", "items": {"type": "string"}}},
                },
                "runtime": {
                    "type": "object",
                    "properties": {"paths": {"type": "array", "items": {"type": "string"}}},
                },
                "cross_check": {"type": "string", "enum": ["sha256_match_or_fail", "none"]},
            },
        },
    },
}


class LayerSchemaValidationError(ValueError):
    pass


class LayerDependencyDAGError(ValueError):
    pass


@dataclass(frozen=True)
class LayerTarget:
    layer_id: str
    layer_name: str = ""


@dataclass(frozen=True)
class LayerDependency:
    id: str
    type: str = "layer_definition"


@dataclass(frozen=True)
class QualityGateSpec:
    gate_id: str
    test_path: str
    description: str = ""
    severity: str = "CRITICAL"


@dataclass(frozen=True)
class EnforcementRuleSpec:
    rule_id: str
    type: str  # compile_time | boot_time
    description: str = ""


@dataclass(frozen=True)
class HostAwareness:
    source_tree_paths: tuple = field(default_factory=tuple)
    runtime_paths: tuple = field(default_factory=tuple)
    cross_check: str = "none"


@dataclass(frozen=True)
class LayerRegistryEntry:
    id: str
    version: str
    targets: tuple  # tuple[LayerTarget, ...]
    dependencies: tuple = field(default_factory=tuple)
    quality_gates: tuple = field(default_factory=tuple)
    enforcement_rules: tuple = field(default_factory=tuple)
    host_awareness: HostAwareness = field(default_factory=HostAwareness)
    status: str = "proposed"

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def validate_manifest(manifest: dict) -> None:
    """Minimal structural validation against JSON_SCHEMA (no external jsonschema
    dependency required — hand-rolled checks for the fields the MVP needs)."""
    for key in JSON_SCHEMA["required"]:
        if key not in manifest:
            raise LayerSchemaValidationError(f"missing required field: {key}")

    if not isinstance(manifest["targets"], list) or not manifest["targets"]:
        raise LayerSchemaValidationError("targets must be a non-empty list")
    for t in manifest["targets"]:
        if "layer_id" not in t:
            raise LayerSchemaValidationError("each target needs layer_id")

    import re
    if not re.match(JSON_SCHEMA["properties"]["id"]["pattern"], manifest["id"]):
        raise LayerSchemaValidationError(f"invalid id format: {manifest['id']}")
    if not re.match(JSON_SCHEMA["properties"]["version"]["pattern"], manifest["version"]):
        raise LayerSchemaValidationError(f"invalid version (need semver): {manifest['version']}")

    for gate in manifest.get("quality_gates", []):
        if "gate_id" not in gate or "test_path" not in gate:
            raise LayerSchemaValidationError("quality_gate needs gate_id + test_path")

    for rule in manifest.get("enforcement_rules", []):
        if rule.get("type") not in ("compile_time", "boot_time"):
            raise LayerSchemaValidationError(f"enforcement_rule type invalid: {rule.get('type')}")


def validate_dependency_dag(entry_id: str, dependencies: list, registry_lookup) -> None:
    """Depth-first cycle detection over dependency ids resolved via registry_lookup
    (a callable: id -> list[dependency ids] or None if not found).

    Raises LayerDependencyDAGError on a cycle or a dependency that resolves to
    nothing (fail-closed — ADR-2222 D2)."""
    visiting: set = set()
    visited: set = set()

    def visit(node_id: str, chain: list):
        if node_id in visited:
            return
        if node_id in visiting:
            raise LayerDependencyDAGError(
                f"circular dependency: {' -> '.join(chain + [node_id])}"
            )
        visiting.add(node_id)
        deps = registry_lookup(node_id)
        if deps is None and node_id != entry_id:
            raise LayerDependencyDAGError(f"unresolvable dependency: {node_id}")
        for dep_id in (deps or []):
            visit(dep_id, chain + [node_id])
        visiting.discard(node_id)
        visited.add(node_id)

    dep_ids = [d["id"] if isinstance(d, dict) else d.id for d in dependencies]
    visit(entry_id, [])
    for dep_id in dep_ids:
        visit(dep_id, [entry_id])


def content_hash_of_paths(paths: list, root: Path) -> str:
    """SHA256 over the sorted, concatenated content of the given paths
    (relative to root). Missing files raise FileNotFoundError (fail-closed)."""
    h = hashlib.sha256()
    for rel in sorted(paths):
        p = root / rel
        h.update(rel.encode("utf-8"))
        h.update(p.read_bytes())
    return h.hexdigest()
