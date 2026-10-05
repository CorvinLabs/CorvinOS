"""LayerRegistry — versioning + promotion lifecycle for layer-definitions
(ADR-2222 D2). Mirrors the SkillRegistry/ToolRegistry shape: create() validates
schema + DAG, promote() moves proposed -> accepted -> deployed, get() reads by id.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .schema import (
    LayerDependencyDAGError,
    LayerSchemaValidationError,
    validate_dependency_dag,
    validate_manifest,
)

_VALID_TRANSITIONS = {
    "proposed": {"accepted"},
    "accepted": {"deployed", "proposed"},
    "deployed": {"superseded"},
    "superseded": set(),
}


class LayerNotFoundError(KeyError):
    pass


class LayerPromotionError(ValueError):
    pass


class LayerRegistry:
    """File-backed registry. One JSON file per (id, version) under root/.

    Not concurrency-safe on its own for a single entry's lifecycle transitions
    (promote-after-promote races) — callers that need that guarantee wrap the
    promote() call with a LayerPrimitive keyed on the entry id (ADR-2222 D4).
    create() is idempotent-safe because it refuses to overwrite an existing
    (id, version) file.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, entry_id: str, version: str) -> Path:
        return self.root / f"{entry_id}@{version}.json"

    def _all_versions_of(self, entry_id: str) -> list[Path]:
        return sorted(self.root.glob(f"{entry_id}@*.json"))

    def create(self, manifest: dict) -> str:
        """Validate manifest schema + dependency DAG, write as 'proposed'.

        Returns the registry key ("<id>@<version>"). Raises
        LayerSchemaValidationError / LayerDependencyDAGError fail-closed;
        nothing is written on failure.
        """
        validate_manifest(manifest)

        def lookup(node_id: str):
            existing = self._latest_version_manifest(node_id)
            if existing is None:
                return None
            return [d["id"] for d in existing.get("dependencies", [])]

        validate_dependency_dag(manifest["id"], manifest.get("dependencies", []), lookup)

        path = self._path_for(manifest["id"], manifest["version"])
        if path.exists():
            raise LayerPromotionError(
                f"entry already exists: {manifest['id']}@{manifest['version']} "
                "(versions are immutable — bump version to change it)"
            )

        record = dict(manifest)
        record["status"] = "proposed"
        record["_created_at"] = time.time()
        path.write_text(json.dumps(record, indent=2, sort_keys=True))
        return f"{manifest['id']}@{manifest['version']}"

    def get(self, entry_id: str, version: str | None = None) -> dict:
        if version is None:
            manifest = self._latest_version_manifest(entry_id)
            if manifest is None:
                raise LayerNotFoundError(entry_id)
            return manifest
        path = self._path_for(entry_id, version)
        if not path.exists():
            raise LayerNotFoundError(f"{entry_id}@{version}")
        return json.loads(path.read_text())

    def _latest_version_manifest(self, entry_id: str) -> dict | None:
        versions = self._all_versions_of(entry_id)
        if not versions:
            return None
        return json.loads(versions[-1].read_text())

    def promote(self, entry_id: str, version: str, to_status: str) -> dict:
        path = self._path_for(entry_id, version)
        if not path.exists():
            raise LayerNotFoundError(f"{entry_id}@{version}")
        record = json.loads(path.read_text())
        current = record.get("status", "proposed")
        allowed = _VALID_TRANSITIONS.get(current, set())
        if to_status not in allowed:
            raise LayerPromotionError(
                f"invalid transition {current} -> {to_status} for {entry_id}@{version} "
                f"(allowed: {sorted(allowed)})"
            )
        record["status"] = to_status
        record["_promoted_at"] = time.time()
        path.write_text(json.dumps(record, indent=2, sort_keys=True))
        return record

    def list_all(self) -> list[dict]:
        return [json.loads(p.read_text()) for p in sorted(self.root.glob("*.json"))]
