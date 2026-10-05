"""Layer-Definition manifest schema and validation (ADR-2222 D1/D2).

A layer-definition is a versioned configuration artifact: it names the layers
it targets, its dependencies on other registry entries, its quality gates,
its enforcement rules, and the host-awareness paths that must match between
the source tree and a deployed runtime.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

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


def _check_repo_relpath(value, *, field_name: str, required_prefix: str | None = None) -> None:
    """A manifest path must stay inside the repo: relative, no ``..``, no NUL.

    Quality-gate test paths are executed as pytest and host-awareness paths are
    read and hashed, so an absolute or ``..`` path would let a manifest submitted
    over the console reach any file the console's OS user can read or run.
    """
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise LayerSchemaValidationError(f"{field_name}: invalid path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts:
        raise LayerSchemaValidationError(f"{field_name}: path must be repo-relative without '..'")
    if required_prefix and (not pure.parts or pure.parts[0] != required_prefix):
        raise LayerSchemaValidationError(f"{field_name}: path must be under {required_prefix}/")


def _list_of_dicts(manifest: dict, key: str) -> list:
    value = manifest.get(key, [])
    if not isinstance(value, list) or not all(isinstance(x, dict) for x in value):
        raise LayerSchemaValidationError(f"{key} must be a list of objects")
    return value


def validate_manifest(manifest: dict) -> None:
    """Minimal structural validation against JSON_SCHEMA (no external jsonschema
    dependency required — hand-rolled checks for the fields the MVP needs)."""
    if not isinstance(manifest, dict):
        raise LayerSchemaValidationError("manifest must be an object")
    for key in JSON_SCHEMA["required"]:
        if key not in manifest:
            raise LayerSchemaValidationError(f"missing required field: {key}")

    if not isinstance(manifest["id"], str) or not re.fullmatch(
        JSON_SCHEMA["properties"]["id"]["pattern"], manifest["id"]
    ):
        raise LayerSchemaValidationError("invalid id format")
    if not isinstance(manifest["version"], str) or not re.fullmatch(
        JSON_SCHEMA["properties"]["version"]["pattern"], manifest["version"]
    ):
        raise LayerSchemaValidationError("invalid version (need semver)")

    targets = manifest["targets"]
    if not isinstance(targets, list) or not targets:
        raise LayerSchemaValidationError("targets must be a non-empty list")
    for t in targets:
        if not isinstance(t, dict) or not isinstance(t.get("layer_id"), str):
            raise LayerSchemaValidationError("each target needs layer_id")

    for dep in _list_of_dicts(manifest, "dependencies"):
        if not isinstance(dep.get("id"), str) or not re.fullmatch(
            JSON_SCHEMA["properties"]["id"]["pattern"], dep["id"]
        ):
            raise LayerSchemaValidationError("dependency needs a valid id")

    for gate in _list_of_dicts(manifest, "quality_gates"):
        if not isinstance(gate.get("gate_id"), str) or "test_path" not in gate:
            raise LayerSchemaValidationError("quality_gate needs gate_id + test_path")
        _check_repo_relpath(gate["test_path"], field_name="quality_gate.test_path",
                            required_prefix="tests")

    for rule in _list_of_dicts(manifest, "enforcement_rules"):
        if rule.get("type") not in ("compile_time", "boot_time"):
            raise LayerSchemaValidationError("enforcement_rule type invalid")

    ha = manifest.get("host_awareness")
    if ha is not None:
        if not isinstance(ha, dict):
            raise LayerSchemaValidationError("host_awareness must be an object")
        if ha.get("cross_check", "none") not in ("sha256_match_or_fail", "none"):
            raise LayerSchemaValidationError("host_awareness.cross_check invalid")
        for side in ("source_tree", "runtime"):
            block = ha.get(side, {})
            paths = block.get("paths", []) if isinstance(block, dict) else None
            if not isinstance(paths, list):
                raise LayerSchemaValidationError(f"host_awareness.{side}.paths must be a list")
            for p in paths:
                _check_repo_relpath(p, field_name=f"host_awareness.{side}.paths")


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
