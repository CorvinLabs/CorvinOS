"""Forge Bundle export (ADR-2229 Phase 2).

``build_bundle`` packs an explicit, caller-given list of artifact selections
into a Forge Bundle ZIP. Each kind reuses that forge's OWN packaging rather
than a new one:

  skill  -> ``core.skills.skill_packager.SkillPackager`` (ADR-0674), packaged
            FRESH on every export from a temporary COPY of the ``skills_gen``
            folder — a cached ZIP could be older than the folder, and the
            packager writes ``.forge/`` metadata into what it packages. Export
            writes nothing outside a temp dir.
  tool   -> ``forge.multi_registry.MultiRegistry.get()`` + the impl file on disk,
            plus the behavioural subset of ``meta`` (requirements, secret key
            names, budget, deterministic) — see ``tool_quarantine.clean_tool_meta``
  layer  -> ``core.orchestration.layer_forge.registry.LayerRegistry.get()``,
            with this install's registry state (status, review flags, ``_*``
            keys) stripped (ADR-2229 D5: status never travels)
  plugin -> an ADR-0511 plugin package the operator already has on disk
            (``manifest.json`` with name/version/author) — the shape the
            importing side's StagingManager accepts; anything else, e.g. a bare
            wheel, is refused here instead of producing a bundle that cannot
            be imported

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
        import shutil
        import tempfile

        from core.skills.manifest_v2 import SkillManifestV2
        from core.skills.skill_packager import SkillPackager

        unresolved = _skills_gen_root() / self.skill_id
        _require(not unresolved.is_symlink(), f"skill {self.skill_id}: the skill folder is a symbolic link; export refuses to follow it")
        skill_folder = unresolved.resolve()
        _require(skill_folder.is_dir(), f"skill not found: {self.skill_id}")
        manifest_file = skill_folder / "skill.json"
        _require(manifest_file.exists(), f"skill {self.skill_id}: missing skill.json")
        try:
            manifest = SkillManifestV2.from_dict(json.loads(manifest_file.read_text()))
        except (ValueError, KeyError, TypeError) as exc:
            raise ExportError(f"skill {self.skill_id}: skill.json is invalid ({type(exc).__name__})") from None
        _require(
            manifest.version == self.version,
            f"skill {self.skill_id}: on-disk version {manifest.version!r} != requested {self.version!r}",
        )
        with tempfile.TemporaryDirectory() as tmp:
            # Packaged from a COPY: SkillPackager writes .forge/ metadata into the
            # folder it packages, and export must leave the forge's store untouched.
            work = Path(tmp) / "src" / self.skill_id
            _require(not any(p.is_symlink() for p in skill_folder.rglob("*")),
                     f"skill {self.skill_id}: contains a symbolic link; export refuses to follow it")
            try:
                shutil.copytree(skill_folder, work, symlinks=False)
                zip_path, _zip_hash, _metadata = SkillPackager(Path(tmp) / "out").package(work, manifest)
            except (ValueError, OSError, shutil.Error) as exc:
                raise ExportError(f"skill {self.skill_id}: cannot be packaged ({type(exc).__name__})") from None
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

        from .tool_quarantine import QuarantineError, clean_tool_meta

        spec = MultiRegistry(tenant_id=tenant_id).get(self.name)
        _require(spec is not None, f"tool not found in any scope: {self.name}")
        impl_path = Path(spec.impl_path)
        _require(impl_path.is_file(), f"tool {self.name}: implementation file missing")
        try:
            meta = clean_tool_meta({k: spec.meta.get(k) for k in ("requirements", "secrets", "budget", "deterministic")}
                                   if isinstance(spec.meta, dict) else None)
        except QuarantineError as exc:
            raise ExportError(f"tool {self.name}: {exc}") from None

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
            "meta": meta,
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

        from .import_module import clean_layer_manifest

        clean = clean_layer_manifest(manifest)
        prefix = f"artifacts/layer/{self.entry_id}@{self.version}/"
        return {prefix + "manifest.json": json.dumps(clean, indent=2).encode("utf-8")}


@dataclass(frozen=True)
class PluginSelection:
    plugin_id: str
    version: str
    package_path: Path
    requires: tuple[Requirement, ...] = ()
    kind: str = "plugin"

    def __post_init__(self) -> None:
        _validate_selection_shape(self.kind, self.plugin_id, self.version, self.requires)
        _require(self.package_path.is_file(), f"plugin {self.plugin_id}: package file not found")

    @property
    def id(self) -> str:
        return self.plugin_id

    def collect(self, tenant_id: str) -> dict[str, bytes]:
        # No build is triggered here: building is its own audited, mutating
        # operation (Plugin Builder, ADR-0262). Export only reads what the
        # operator already built — and refuses what the target could not stage.
        from core.plugins.staging import StagingManager

        ok, manifest, _errors = StagingManager(tenant_id).validate_zip_file(self.package_path)
        _require(ok, f"plugin {self.plugin_id}: not a plugin package (it needs a manifest.json with name, version and author)")
        _require(manifest.get("name") == self.plugin_id and manifest.get("version") == self.version,
                 f"plugin {self.plugin_id}: package manifest names {manifest.get('name')!r}@{manifest.get('version')!r}")
        prefix = f"artifacts/plugin/{self.plugin_id}@{self.version}/"
        return {prefix + self.package_path.name: self.package_path.read_bytes()}


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
        try:
            files = sel.collect(tenant_id)
        except ExportError:
            raise
        except Exception as exc:  # noqa: BLE001 — a forge's own error must not surface as a bare 500
            raise ExportError(f"{sel.kind} {sel.id}: could not be collected ({type(exc).__name__})") from None
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
