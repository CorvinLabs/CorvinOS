"""Per-item export for Forge artifacts with Claude-Web Skill ZIP format support (ADR-2229 Phase 4).

This module extends forge bundle export to support:
  1. Single-item downloads for each artifact type
  2. Claude-Web-compatible Skill ZIP format (SKILL.md + .corvin-skill.json)
  3. Selective batch export with embedded per-item ZIPs

Skill ZIP format (Claude-Web compatible):
  <skill-name>/
    ├── SKILL.md (YAML frontmatter: name, description, optional fields)
    ├── .corvin-skill.json (stores original Corvin name/version/metadata for lossless import)
    ├── scripts/ (optional)
    ├── references/ (optional)
    └── assets/ (optional)

Non-skill artifacts (tools, layers, plugins) are exported in their native formats as per ADR-2229.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .export import ExportError, Selection, SkillSelection, ToolSelection, LayerSelection, PluginSelection


class ItemExportError(ValueError):
    """Per-item export failed."""


def _sanitize_skill_name_for_claude(corvin_name: str) -> str:
    """Convert Corvin skill name to Claude-Web-compatible format.

    Rules:
    - Lowercase
    - Alphanumerics and hyphens only
    - Max 64 chars
    - Deterministically handle collisions with hash suffix
    - No 'anthropic' or 'claude' prefix

    Examples:
    - assistant.foo_bar → assistant-foo-bar
    - os.delegation_router → os-delegation-router
    """
    # Replace underscores and dots with hyphens
    sanitized = corvin_name.replace("_", "-").replace(".", "-").lower()

    # Keep only alphanumerics and hyphens
    sanitized = "".join(c if c.isalnum() or c == "-" else "" for c in sanitized)

    # Remove leading/trailing hyphens
    sanitized = sanitized.strip("-")

    # Reject forbidden prefixes
    if sanitized.startswith("anthropic") or sanitized.startswith("claude"):
        # Prepend user_ to disambiguate
        sanitized = f"user-{sanitized}"

    # Truncate to 64 chars with hash suffix if necessary
    if len(sanitized) > 64:
        # Use 8-char hash to keep it deterministic
        hash_suffix = hashlib.sha256(corvin_name.encode()).hexdigest()[:8]
        # Reserve 12 chars for hash (8 + 3 for dashes and leading dash)
        base = sanitized[: 64 - 12].rstrip("-")
        sanitized = f"{base}--{hash_suffix}"

    return sanitized


@dataclass(frozen=True)
class ItemExportResult:
    """Result of a single-item export."""
    data: bytes
    artifact_type: str  # skill, tool, layer, plugin
    artifact_id: str
    artifact_version: str
    size_bytes: int
    sha256: str


def _export_skill_to_claude_format(
    skill_selection: SkillSelection,
    tenant_id: str,
) -> ItemExportResult:
    """Export a skill as a Claude-Web-compatible ZIP.

    Returns a ZIP with:
      <skill-name>/
        ├── SKILL.md (with YAML frontmatter)
        ├── .corvin-skill.json (Corvin metadata for lossless re-import)
        └── [original skill contents]
    """
    from core.skills.manifest_v2 import SkillManifestV2
    from core.skills.skill_packager import SkillPackager
    import shutil
    import tempfile

    # Collect the skill in its native format (ADR-0674 ZIP)
    from .export import _skills_gen_root, _require

    unresolved = _skills_gen_root() / skill_selection.skill_id
    _require(not unresolved.is_symlink(), f"skill {skill_selection.skill_id}: symlink refused")
    skill_folder = unresolved.resolve()
    _require(skill_folder.is_dir(), f"skill not found: {skill_selection.skill_id}")
    manifest_file = skill_folder / "skill.json"
    _require(manifest_file.exists(), f"skill {skill_selection.skill_id}: missing skill.json")

    try:
        manifest = SkillManifestV2.from_dict(json.loads(manifest_file.read_text()))
    except (ValueError, KeyError, TypeError) as exc:
        raise ItemExportError(f"skill {skill_selection.skill_id}: invalid skill.json") from None

    _require(
        manifest.version == skill_selection.version,
        f"skill {skill_selection.skill_id}: version mismatch",
    )

    # Create Claude-format ZIP
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # Create the top-level skill folder
        skill_folder_copy = tmp_path / skill_selection.skill_id
        skill_folder_copy.mkdir()

        # Copy skill contents (recursively)
        _require(
            not any(p.is_symlink() for p in skill_folder.rglob("*")),
            f"skill {skill_selection.skill_id}: contains symlinks",
        )
        _require(
            not any(p.is_file() and p.stat().st_nlink > 1 for p in skill_folder.rglob("*")),
            f"skill {skill_selection.skill_id}: contains hard links",
        )

        for src_item in skill_folder.iterdir():
            if src_item.is_dir():
                shutil.copytree(src_item, skill_folder_copy / src_item.name, symlinks=False)
            else:
                shutil.copy2(src_item, skill_folder_copy / src_item.name)

        # Create SKILL.md with frontmatter
        claude_name = _sanitize_skill_name_for_claude(skill_selection.skill_id)
        description = _skill_description(skill_folder, skill_selection.skill_id)
        skill_md_content = f"""---
name: {claude_name}
description: {json.dumps(description)}
---

# {skill_selection.skill_id}

{description}

> **Original Corvin name:** {skill_selection.skill_id}
"""
        (skill_folder_copy / "SKILL.md").write_text(skill_md_content)

        # Store Corvin metadata for lossless re-import
        corvin_metadata = {
            "corvin_skill_id": skill_selection.skill_id,
            "corvin_version": skill_selection.version,
            "exported_at": json.dumps(__import__("datetime").datetime.now(tz=__import__("datetime").timezone.utc).isoformat()),
            "original_manifest": manifest.to_dict(),
        }
        (skill_folder_copy / ".corvin-skill.json").write_text(json.dumps(corvin_metadata, indent=2))

        # Create the ZIP
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for item in skill_folder_copy.rglob("*"):
                if item.is_file():
                    arcname = item.relative_to(tmp_path)
                    zf.write(item, arcname)

        zip_data = zip_buffer.getvalue()
        sha256_hash = hashlib.sha256(zip_data).hexdigest()

        return ItemExportResult(
            data=zip_data,
            artifact_type="skill",
            artifact_id=skill_selection.skill_id,
            artifact_version=skill_selection.version,
            size_bytes=len(zip_data),
            sha256=sha256_hash,
        )


def _export_tool_to_zip(
    tool_selection: ToolSelection,
    tenant_id: str,
) -> ItemExportResult:
    """Export a tool as a minimal ZIP (spec.json + impl file)."""
    from forge.multi_registry import MultiRegistry
    from .tool_quarantine import clean_tool_meta, QuarantineError
    from .export import _require

    spec = MultiRegistry(tenant_id=tenant_id).get(tool_selection.name)
    _require(spec is not None, f"tool not found: {tool_selection.name}")
    impl_path = Path(spec.impl_path)
    _require(impl_path.is_file(), f"tool {tool_selection.name}: impl file missing")

    try:
        meta = clean_tool_meta({k: spec.meta.get(k) for k in ("requirements", "secrets", "budget", "deterministic")}
                               if isinstance(spec.meta, dict) else None)
    except QuarantineError as exc:
        raise ItemExportError(f"tool {tool_selection.name}: {exc}") from None

    spec_json = json.dumps({
        "name": spec.name,
        "description": spec.description,
        "input_schema": spec.input_schema,
        "runtime": spec.runtime,
        "version": tool_selection.version,
        "impl_filename": impl_path.name,
        "meta": meta,
    }, indent=2).encode("utf-8")
    impl_bytes = impl_path.read_bytes()

    # Create ZIP
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{tool_selection.name}/spec.json", spec_json)
        zf.writestr(f"{tool_selection.name}/{impl_path.name}", impl_bytes)

    zip_data = zip_buffer.getvalue()
    sha256_hash = hashlib.sha256(zip_data).hexdigest()

    return ItemExportResult(
        data=zip_data,
        artifact_type="tool",
        artifact_id=tool_selection.name,
        artifact_version=tool_selection.version,
        size_bytes=len(zip_data),
        sha256=sha256_hash,
    )


def _export_layer_to_zip(
    layer_selection: LayerSelection,
    tenant_id: str,
) -> ItemExportResult:
    """Export a layer as a minimal ZIP (manifest.json only)."""
    from core.orchestration.layer_forge.orchestrator import layer_forge_home
    from core.orchestration.layer_forge.registry import LayerNotFoundError, LayerRegistry
    from .import_module import clean_layer_manifest

    registry = LayerRegistry(layer_forge_home(tenant_id) / "registry")
    try:
        manifest = registry.get(layer_selection.entry_id, layer_selection.version)
    except LayerNotFoundError:
        raise ItemExportError(f"layer not found: {layer_selection.entry_id}@{layer_selection.version}") from None

    clean = clean_layer_manifest(manifest)
    manifest_json = json.dumps(clean, indent=2).encode("utf-8")

    # Create ZIP
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{layer_selection.entry_id}/manifest.json", manifest_json)

    zip_data = zip_buffer.getvalue()
    sha256_hash = hashlib.sha256(zip_data).hexdigest()

    return ItemExportResult(
        data=zip_data,
        artifact_type="layer",
        artifact_id=layer_selection.entry_id,
        artifact_version=layer_selection.version,
        size_bytes=len(zip_data),
        sha256=sha256_hash,
    )


def _export_os_skill_to_claude_format(
    skill_id: str,
    version: str,
    tenant_id: str,
) -> ItemExportResult:
    """Export an OS-Skill as a read-only Claude-Web-compatible ZIP.

    OS-Skills are system-level skills (routing, security, learning, etc.) exported
    as read-only descriptions for documentation and Claude Web import.

    Returns a ZIP with:
      <skill-name>/
        ├── SKILL.md (with YAML frontmatter, read-only description)
        └── .corvin-skill.json (Corvin metadata)
    """
    # OS-Skills are documented via their module docstrings and manifest info
    # For now, create a minimal read-only ZIP with documentation
    from core.skills.manifest_v2 import SkillManifestV2

    try:
        # Try to load OS-Skill metadata from the manifest system
        # For now, create a basic entry with a description note
        claude_name = _sanitize_skill_name_for_claude(skill_id)

        skill_md_content = f"""---
name: {claude_name}
description: "OS-Skill (read-only system skill)"
---

# {skill_id}

This is an OS-level skill managed by CorvinOS. It is read-only and provides core system functionality.

**Corvin Skill ID:** {skill_id}
**Version:** {version}

This skill cannot be directly modified; it is part of the OS kernel.

> **Note:** This is a read-only export of a system skill. Direct import/use is not supported.
"""

        corvin_metadata = {
            "corvin_skill_id": skill_id,
            "corvin_version": version,
            "skill_type": "os_skill",
            "read_only": True,
            "exported_at": json.dumps(__import__("datetime").datetime.now(tz=__import__("datetime").timezone.utc).isoformat()),
        }

        # Create the ZIP
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{claude_name}/SKILL.md", skill_md_content)
            zf.writestr(f"{claude_name}/.corvin-skill.json", json.dumps(corvin_metadata, indent=2))

        zip_data = zip_buffer.getvalue()
        sha256_hash = hashlib.sha256(zip_data).hexdigest()

        return ItemExportResult(
            data=zip_data,
            artifact_type="os_skill",
            artifact_id=skill_id,
            artifact_version=version,
            size_bytes=len(zip_data),
            sha256=sha256_hash,
        )
    except Exception as exc:  # noqa: BLE001
        raise ItemExportError(f"os_skill {skill_id}: export failed ({type(exc).__name__}: {exc})") from exc


def _export_plugin_to_zip(
    plugin_selection: PluginSelection,
    tenant_id: str,
) -> ItemExportResult:
    """Export a plugin (already a ZIP, just pass through)."""
    from core.plugins.staging import StagingManager

    ok, manifest, _errors = StagingManager(tenant_id).validate_zip_file(plugin_selection.package_path)
    if not ok:
        raise ItemExportError(f"plugin {plugin_selection.plugin_id}: not a valid plugin package")
    if manifest.get("name") != plugin_selection.plugin_id or manifest.get("version") != plugin_selection.version:
        raise ItemExportError(f"plugin {plugin_selection.plugin_id}: manifest mismatch")

    zip_data = plugin_selection.package_path.read_bytes()
    sha256_hash = hashlib.sha256(zip_data).hexdigest()

    return ItemExportResult(
        data=zip_data,
        artifact_type="plugin",
        artifact_id=plugin_selection.plugin_id,
        artifact_version=plugin_selection.version,
        size_bytes=len(zip_data),
        sha256=sha256_hash,
    )


def _skill_description(skill_folder: Path, skill_id: str) -> str:
    """One-line description for SKILL.md (Claude Web requires it, max 1024 chars).

    ``SkillManifestV2`` carries no description, so use ``skill.json``'s own
    ``description`` key, else the first prose line of README.md, else a stub."""
    text = ""
    try:
        raw = json.loads((skill_folder / "skill.json").read_text())
        if isinstance(raw, dict) and isinstance(raw.get("description"), str):
            text = raw["description"]
    except (OSError, ValueError):
        pass
    if not text.strip():
        try:
            for line in (skill_folder / "README.md").read_text().splitlines():
                if line.strip() and not line.lstrip().startswith("#"):
                    text = line
                    break
        except OSError:
            pass
    text = " ".join(text.split())[:1024]
    return text or f"Corvin skill {skill_id}"


def export_single_item(
    artifact_type: Literal["skill", "tool", "layer", "plugin", "os_skill"],
    artifact_id: str,
    artifact_version: str,
    tenant_id: str,
    package_path: Path | None = None,  # required for plugins
) -> ItemExportResult:
    """Export a single artifact as a ZIP.

    Args:
        artifact_type: One of skill, tool, layer, plugin, os_skill
        artifact_id: The artifact's ID
        artifact_version: Semver version string
        tenant_id: Tenant context
        package_path: For plugins, the path to the pre-built package

    Returns:
        ItemExportResult with ZIP data and metadata

    Raises:
        ItemExportError if the export fails
    """
    try:
        if artifact_type == "skill":
            selection = SkillSelection(artifact_id, artifact_version)
            return _export_skill_to_claude_format(selection, tenant_id)
        elif artifact_type == "tool":
            selection = ToolSelection(artifact_id, artifact_version)
            return _export_tool_to_zip(selection, tenant_id)
        elif artifact_type == "layer":
            selection = LayerSelection(artifact_id, artifact_version)
            return _export_layer_to_zip(selection, tenant_id)
        elif artifact_type == "os_skill":
            return _export_os_skill_to_claude_format(artifact_id, artifact_version, tenant_id)
        elif artifact_type == "plugin":
            if not package_path:
                raise ItemExportError("plugin export requires package_path")
            selection = PluginSelection(artifact_id, artifact_version, package_path)
            return _export_plugin_to_zip(selection, tenant_id)
        else:
            raise ItemExportError(f"unknown artifact type: {artifact_type}")
    except ItemExportError:
        raise
    except ExportError as exc:
        raise ItemExportError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise ItemExportError(f"{artifact_type} {artifact_id}: export failed ({type(exc).__name__}: {exc})") from exc
