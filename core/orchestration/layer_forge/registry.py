"""LayerRegistry — versioning + promotion lifecycle for layer-definitions
(ADR-2222 D2). Mirrors the SkillRegistry/ToolRegistry shape: create() validates
schema + DAG, promote() moves proposed -> accepted -> deployed, get() reads by id.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .primitive import atomic_write_json
from .schema import (
    JSON_SCHEMA,
    LayerDependencyDAGError,
    LayerSchemaValidationError,
    validate_dependency_dag,
    validate_manifest,
)

_ID_PATTERN = JSON_SCHEMA["properties"]["id"]["pattern"]
_VERSION_PATTERN = JSON_SCHEMA["properties"]["version"]["pattern"]

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

    Writes are atomic (``atomic_write_json``) but the registry holds no lock:
    a create or transition is a read-modify-write, so the caller holds
    ``LayerPrimitive(entry_id).locked()`` around it (the orchestrator does,
    ADR-2222 D4).
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _check_key(entry_id: str, version: str | None = None) -> None:
        # ids/versions reach file names and glob patterns; callers include HTTP routes
        if not isinstance(entry_id, str) or not re.fullmatch(_ID_PATTERN, entry_id):
            raise LayerNotFoundError(entry_id)
        if version is not None and (
            not isinstance(version, str) or not re.fullmatch(_VERSION_PATTERN, version)
        ):
            raise LayerNotFoundError(f"{entry_id}@{version}")

    def _path_for(self, entry_id: str, version: str) -> Path:
        self._check_key(entry_id, version)
        return self.root / f"{entry_id}@{version}.json"

    def _all_versions_of(self, entry_id: str) -> list[Path]:
        self._check_key(entry_id)
        def semver(p: Path) -> tuple[int, ...]:
            return tuple(int(x) for x in p.stem.rsplit("@", 1)[1].split("."))
        return sorted(self.root.glob(f"{entry_id}@*.json"), key=semver)

    def validate(self, manifest: dict) -> str:
        """Schema + dependency DAG + version-not-taken; writes nothing.

        Returns the registry key ("<id>@<version>").
        """
        validate_manifest(manifest)

        def lookup(node_id: str):
            existing = self._latest_version_manifest(node_id)
            if existing is None:
                return None
            return [d["id"] for d in existing.get("dependencies", [])]

        validate_dependency_dag(manifest["id"], manifest.get("dependencies", []), lookup)

        if self._path_for(manifest["id"], manifest["version"]).exists():
            raise LayerPromotionError(
                f"entry already exists: {manifest['id']}@{manifest['version']} "
                "(versions are immutable — bump version to change it)"
            )
        return f"{manifest['id']}@{manifest['version']}"

    def create(self, manifest: dict) -> str:
        """Validate, then write as 'proposed'. Nothing is written on failure."""
        key = self.validate(manifest)
        record = dict(manifest)
        record["status"] = "proposed"
        record["_created_at"] = time.time()
        atomic_write_json(self._path_for(manifest["id"], manifest["version"]), record)
        return key

    def current_status(self, entry_id: str, version: str) -> str:
        return self.get(entry_id, version).get("status", "proposed")

    def check_transition(self, entry_id: str, version: str, to_status: str) -> str:
        """Return the current status if ``to_status`` is reachable, else raise."""
        current = self.current_status(entry_id, version)
        allowed = _VALID_TRANSITIONS.get(current, set())
        if to_status not in allowed:
            raise LayerPromotionError(
                f"invalid transition {current} -> {to_status} for {entry_id}@{version} "
                f"(allowed: {sorted(allowed)})"
            )
        return current

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
        self.check_transition(entry_id, version, to_status)
        record = self.get(entry_id, version)
        record["status"] = to_status
        record["_promoted_at"] = time.time()
        atomic_write_json(self._path_for(entry_id, version), record)
        return record

    def list_all(self) -> list[dict]:
        return [json.loads(p.read_text()) for p in sorted(self.root.glob("*.json"))]
