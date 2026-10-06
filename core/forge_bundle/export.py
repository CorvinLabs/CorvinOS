"""Forge Bundle export (ADR-2229 Phase 2).

``build_bundle`` packs an explicit, caller-given list of artifact selections
into a Forge Bundle ZIP. Each kind reuses that forge's OWN packaging rather
than a new one:

  skill  -> ``core.skills.skill_packager.SkillPackager`` (ADR-0674), same
            ``skills_gen`` / ``skills_packages`` roots the console's
            ``/v1/skill-forge/package`` route already writes to
  tool   -> ``forge.multi_registry.MultiRegistry.get()`` + the impl file on disk
  layer  -> ``core.orchestration.layer_forge.registry.LayerRegistry.get()``,
            with registry-only fields (``status``, ``_created_at``, ``_promoted_at``)
            stripped before export (ADR-2229 D5: status never travels)
  plugin -> a wheel the operator already built (Plugin Builder, ADR-0262);
            export never triggers a build — a build is its own audited
            mutation, and export is read-only

Tool Forge tools carry no version of their own (``ToolSpec`` has no
``version`` field) — the version in a :class:`ToolSelection` is supplied by
the caller at export time, not derived from the registry.

``requires`` (cross-artifact dependencies) are declared by the caller per
selection, not auto-resolved — nothing in Skill/Tool/Layer today exposes a
uniform dependency list to walk automatically.

The produced bytes are ALWAYS round-tripped through
:func:`core.forge_bundle.validate.validate_bundle` before being returned: a
bundle this process could not later import is never handed out.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .audit import emit as _audit_emit
from .envelope import ARTIFACT_KINDS, FORMAT, FORMAT_VERSION, Requirement, is_safe_id, is_semver
from .validate import BundleRejected, validate_bundle

ENVELOPE_NAME = "forge-bundle.json"


class ExportError(ValueError):
    """A selection could not be collected, or the built bundle failed self-validation."""


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ExportError(msg)


def _req_to_dict(req: Requirement) -> dict:
    out = {"kind": req.kind, "id": req.id}
    if req.version is not None:
        out["version"] = req.version
    return out


def _validate_selection_shape(kind: str, aid: str, version: str, requires: Sequence[Requirement]) -> None:
    _require(kind in ARTIFACT_KINDS, f"unknown artifact kind {kind!r}")
    _require(is_safe_id(aid), f"{kind} id {aid!r} is not a safe identifier")
    _require(is_semver(version), f"{kind} {aid!r}: version {version!r} is not semver")
    for req in requires:
        _require(req.kind in ARTIFACT_KINDS, f"{kind} {aid!r}: requires an unknown kind {req.kind!r}")


# ── per-kind collectors ──────────────────────────────────────────────────────


def _skills_gen_root() -> Path:
    from forge import paths as _forge_paths
    return _forge_paths.corvin_home() / "skills_gen"


def _skills_packages_root() -> Path:
    from forge import paths as _forge_paths
    return _forge_paths.corvin_home() / "skills_packages"


@dataclass(frozen=True)
class SkillSelection:
    skill_id: str
    version: str
    requires: tuple[Requirement, ...] = ()
    kind: str = "skill"

    def __post_init__(self) -> None:
        _validate_selection_shape(self.kind, self.skill_id, self.version, self.requires)

    @property
    def id(self) -> str:
        return self.skill_id

    def collect(self, tenant_id: str) -> dict[str, bytes]:
        from core.skills.manifest_v2 import SkillManifestV2
        from core.skills.skill_packager import SkillPackager

        skill_folder = (_skills_gen_root() / self.skill_id).resolve()
        _require(skill_folder.exists(), f"skill not found: {self.skill_id} (looked in {skill_folder})")
        manifest_file = skill_folder / "skill.json"
        _require(manifest_file.exists(), f"skill {self.skill_id}: missing skill.json")
        manifest = SkillManifestV2.from_dict(json.loads(manifest_file.read_text()))
        _require(
            manifest.version == self.version,
            f"skill {self.skill_id}: on-disk version {manifest.version!r} != requested {self.version!r}",
        )

        packages_root = _skills_packages_root()
        zip_path = packages_root / f"{self.skill_id}_{self.version}.zip"
        if not zip_path.exists():
            packager = SkillPackager(packages_root)
            zip_path, _zip_hash, _metadata = packager.package(skill_folder, manifest)

        prefix = f"artifacts/skill/{self.skill_id}@{self.version}/"
        return {prefix + zip_path.name: zip_path.read_bytes()}


@dataclass(frozen=True)
class ToolSelection:
    name: str
    version: str
    requires: tuple[Requirement, ...] = ()
    kind: str = "tool"

    def __post_init__(self) -> None:
        _validate_selection_shape(self.kind, self.name, self.version, self.requires)

    @property
    def id(self) -> str:
        return self.name

    def collect(self, tenant_id: str) -> dict[str, bytes]:
        from forge.multi_registry import MultiRegistry

        spec = MultiRegistry(tenant_id=tenant_id).get(self.name)
        _require(spec is not None, f"tool not found in any scope: {self.name}")
        impl_path = Path(spec.impl_path)
        _require(impl_path.exists(), f"tool {self.name}: impl file missing ({impl_path})")

        # Registry-only state (scope, call_count, promoted, created_at, meta)
        # never travels — ADR-2229 D5. A spec.json carries only what re-creates
        # the tool: name, description, input_schema, runtime, version, and the
        # impl file's own name.
        prefix = f"artifacts/tool/{self.name}@{self.version}/"
        impl_bytes = impl_path.read_bytes()
        spec_json = json.dumps({
            "name": spec.name,
            "description": spec.description,
            "input_schema": spec.input_schema,
            "runtime": spec.runtime,
            "version": self.version,
            "impl_filename": impl_path.name,
        }, indent=2).encode("utf-8")
        return {
            prefix + "spec.json": spec_json,
            prefix + impl_path.name: impl_bytes,
        }


@dataclass(frozen=True)
class LayerSelection:
    entry_id: str
    version: str
    requires: tuple[Requirement, ...] = ()
    kind: str = "layer"

    def __post_init__(self) -> None:
        _validate_selection_shape(self.kind, self.entry_id, self.version, self.requires)

    @property
    def id(self) -> str:
        return self.entry_id

    def collect(self, tenant_id: str) -> dict[str, bytes]:
        from core.orchestration.layer_forge.orchestrator import layer_forge_home
        from core.orchestration.layer_forge.registry import LayerNotFoundError, LayerRegistry

        registry = LayerRegistry(layer_forge_home(tenant_id) / "registry")
        try:
            manifest = registry.get(self.entry_id, self.version)
        except LayerNotFoundError:
            raise ExportError(f"layer not found: {self.entry_id}@{self.version}") from None

        # Registry-only fields never travel (ADR-2229 D5): status and the
        # lifecycle timestamps are this install's state, not the definition.
        clean = {k: v for k, v in manifest.items() if k not in {"status", "_created_at", "_promoted_at"}}
        prefix = f"artifacts/layer/{self.entry_id}@{self.version}/"
        return {prefix + "manifest.json": json.dumps(clean, indent=2).encode("utf-8")}


@dataclass(frozen=True)
class PluginSelection:
    plugin_id: str
    version: str
    wheel_path: Path
    requires: tuple[Requirement, ...] = ()
    kind: str = "plugin"

    def __post_init__(self) -> None:
        _validate_selection_shape(self.kind, self.plugin_id, self.version, self.requires)
        _require(self.wheel_path.exists() and self.wheel_path.is_file(),
                  f"plugin {self.plugin_id}: wheel not found ({self.wheel_path})")

    @property
    def id(self) -> str:
        return self.plugin_id

    def collect(self, tenant_id: str) -> dict[str, bytes]:
        # No build is triggered here: building is its own audited, mutating
        # operation (Plugin Builder, ADR-0262). Export only reads what the
        # operator already built.
        prefix = f"artifacts/plugin/{self.plugin_id}@{self.version}/"
        return {prefix + self.wheel_path.name: self.wheel_path.read_bytes()}


Selection = SkillSelection | ToolSelection | LayerSelection | PluginSelection


@dataclass(frozen=True)
class BundleResult:
    data: bytes
    bundle_id: str
    bundle_version: str
    artifact_count: int
    total_bytes: int
    audit_hash: str


def build_bundle(
    *,
    bundle_id: str,
    bundle_version: str,
    selections: Sequence[Selection],
    tenant_id: str,
    description: str | None = None,
) -> BundleResult:
    """Collect every selection, assemble + self-validate the ZIP, audit, return it.

    Raises :class:`ExportError` if any selection cannot be collected or the
    assembled bundle fails :func:`validate_bundle` (never a half-written ZIP).
    Raises :class:`core.forge_bundle.audit.ForgeBundleAuditError` if the audit
    chain write fails — the export is reported as failed, not handed back.
    """
    _require(is_safe_id(bundle_id), f"bundle id {bundle_id!r} is not a safe identifier")
    _require(is_semver(bundle_version), f"bundle version {bundle_version!r} is not semver")
    _require(len(selections) > 0, "a bundle needs at least one artifact")

    seen_keys: set[tuple[str, str]] = set()
    entries: dict[str, bytes] = {}
    artifact_manifests: list[dict] = []

    for sel in selections:
        key = (sel.kind, sel.id)
        _require(key not in seen_keys, f"duplicate selection: {sel.kind} {sel.id!r}")
        seen_keys.add(key)

        # Every file path is prefixed with this selection's (kind, id, version);
        # the (kind, id) dedup above already makes two selections collide
        # before file collection, so no later path can ever repeat here.
        files = sel.collect(tenant_id)
        entries.update(files)

        artifact_manifests.append({
            "kind": sel.kind,
            "id": sel.id,
            "version": sel.version,
            "files": [
                {"path": p, "sha256": hashlib.sha256(d).hexdigest(), "size": len(d)}
                for p, d in sorted(files.items())
            ],
            "requires": [_req_to_dict(r) for r in sel.requires],
        })

    envelope = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "id": bundle_id,
        "version": bundle_version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": artifact_manifests,
    }
    if description is not None:
        envelope["description"] = description
    entries[ENVELOPE_NAME] = json.dumps(envelope, indent=2).encode("utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(entries):  # deterministic archive order
            zf.writestr(name, entries[name])
    data = buf.getvalue()

    # Never hand out what this process could not later import.
    try:
        validate_bundle(data)
    except BundleRejected as exc:
        raise ExportError(f"built bundle failed self-validation: {exc}") from exc

    total_bytes = sum(len(v) for v in entries.values())
    digest = _audit_emit(
        "forge_bundle.exported",
        tenant_id=tenant_id,
        bundle_id=bundle_id,
        bundle_version=bundle_version,
        artifact_count=len(artifact_manifests),
        total_bytes=total_bytes,
    )

    return BundleResult(
        data=data,
        bundle_id=bundle_id,
        bundle_version=bundle_version,
        artifact_count=len(artifact_manifests),
        total_bytes=total_bytes,
        audit_hash=digest,
    )
