"""Import handler for single-item ZIPs (ADR-2229 Phase 4).

Detects and imports single-item ZIPs (Skill, Tool, Layer, Plugin) that are not
part of a larger bundle. Works alongside the existing bundle import system.

Format detection:
  - Bundle: Has forge-bundle.json manifest
  - Single Skill: Single top-level folder with SKILL.md + .corvin-skill.json
  - Single Tool: Single top-level folder with spec.json + impl file
  - Single Layer: Single top-level folder with manifest.json
  - Single Plugin: Pre-built plugin ZIP (validated by StagingManager)
"""
from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .audit import ForgeBundleAuditError, emit


class ItemImportError(ValueError):
    """Single-item import failed."""


@dataclass(frozen=True)
class DetectedItemZip:
    """Detected single-item ZIP metadata."""
    item_type: Literal["skill", "tool", "layer", "plugin"]
    item_id: str
    item_version: str
    data: bytes


def detect_item_type_from_zip(data: bytes) -> DetectedItemZip | None:
    """Detect if a ZIP is a single-item ZIP and identify its type.

    Returns:
        DetectedItemZip if detected as single-item, None if it's a bundle or unrecognized.

    Raises:
        ItemImportError if the ZIP is malformed.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            names = zf.namelist()

            # Rule 1: If forge-bundle.json exists, it's a bundle (not a single-item)
            if "forge-bundle.json" in names:
                return None

            # Rule 2: Check for single top-level folder (single-item signature)
            top_level = {n.split("/")[0] for n in names if "/" in n}
            if len(top_level) != 1:
                # No single top-level folder; could be a bundle or unrecognized format
                return None

            folder = list(top_level)[0]

            # Rule 3: Single-Skill ZIP detection
            if any(n == f"{folder}/SKILL.md" for n in names):
                corvin_meta_path = f"{folder}/.corvin-skill.json"
                if corvin_meta_path in names:
                    try:
                        meta = json.loads(zf.read(corvin_meta_path).decode())
                        skill_id = meta.get("corvin_skill_id", folder)
                        version = meta.get("corvin_version", "1.0.0")
                        return DetectedItemZip(
                            item_type="skill",
                            item_id=skill_id,
                            item_version=version,
                            data=data,
                        )
                    except (json.JSONDecodeError, KeyError) as exc:
                        raise ItemImportError(f"Skill ZIP: invalid .corvin-skill.json ({exc})") from None

            # Rule 4: Single-Tool ZIP detection
            if any(n == f"{folder}/spec.json" for n in names):
                try:
                    spec = json.loads(zf.read(f"{folder}/spec.json").decode())
                    tool_id = spec.get("name", folder)
                    version = spec.get("version", "1.0.0")

                    # Verify impl file exists
                    impl_filename = spec.get("impl_filename")
                    if impl_filename and f"{folder}/{impl_filename}" not in names:
                        raise ItemImportError(f"Tool ZIP: impl file {impl_filename} not found")

                    return DetectedItemZip(
                        item_type="tool",
                        item_id=tool_id,
                        item_version=version,
                        data=data,
                    )
                except (json.JSONDecodeError, KeyError) as exc:
                    raise ItemImportError(f"Tool ZIP: invalid spec.json ({exc})") from None

            # Rule 5: Single-Layer ZIP detection
            if any(n == f"{folder}/manifest.json" for n in names):
                try:
                    manifest = json.loads(zf.read(f"{folder}/manifest.json").decode())
                    layer_id = manifest.get("id", folder)
                    version = manifest.get("version", "1.0.0")
                    return DetectedItemZip(
                        item_type="layer",
                        item_id=layer_id,
                        item_version=version,
                        data=data,
                    )
                except (json.JSONDecodeError, KeyError) as exc:
                    raise ItemImportError(f"Layer ZIP: invalid manifest.json ({exc})") from None

            # Rule 6: If none of the above, it's unrecognized (not a single-item)
            return None

    except zipfile.BadZipFile as exc:
        raise ItemImportError(f"Not a valid ZIP file ({exc})") from None
    except Exception as exc:  # noqa: BLE001
        raise ItemImportError(f"ZIP detection failed ({type(exc).__name__}: {exc})") from None


def import_single_item(
    detected: DetectedItemZip,
    tenant_id: str,
    actor: str,
) -> dict[str, str]:
    """Import a detected single-item ZIP.

    Args:
        detected: DetectedItemZip from detect_item_type_from_zip()
        tenant_id: Tenant context
        actor: Actor performing the import (e.g., "console")

    Returns:
        Dict with import outcome (status, detail)

    Raises:
        ItemImportError on validation failure
        ForgeBundleAuditError on audit failure
    """
    if detected.item_type == "skill":
        return _import_single_skill(detected, tenant_id, actor)
    elif detected.item_type == "tool":
        return _import_single_tool(detected, tenant_id, actor)
    elif detected.item_type == "layer":
        return _import_single_layer(detected, tenant_id, actor)
    elif detected.item_type == "plugin":
        return _import_single_plugin(detected, tenant_id, actor)
    else:
        raise ItemImportError(f"Unknown item type: {detected.item_type}")


def _import_single_skill(
    detected: DetectedItemZip,
    tenant_id: str,
    actor: str,
) -> dict[str, str]:
    """Import a single skill ZIP."""
    # Audit: import started
    try:
        emit(
            "forge_bundle.artifact_intake_started",
            tenant_id=tenant_id,
            bundle_id="single-item",
            artifact_kind="skill",
            artifact_id=detected.item_id,
            artifact_version=detected.item_version,
            status="intake_started",
            actor=actor,
        )
    except ForgeBundleAuditError as exc:
        raise ItemImportError(f"Audit failure: {exc}") from None

    # Re-pack the Claude-Web layout into the packager format, then install it
    # through the same SkillInstaller a bundle import uses.
    try:
        import shutil
        import tempfile

        from forge import paths as _forge_paths

        from core.skills.manifest_v2 import SkillManifestV2
        from core.skills.skill_installer import SkillInstaller
        from core.skills.skill_packager import SkillPackager

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "src" / detected.item_id
            work.mkdir(parents=True)
            total = 0
            prompt_body: str | None = None  # the SKILL.md the packager layout drops
            with zipfile.ZipFile(io.BytesIO(detected.data)) as zf:
                for info in zf.infolist():
                    parts = info.filename.split("/")
                    if info.is_dir():
                        continue
                    if info.filename.startswith("/") or ".." in parts or "\\" in info.filename:
                        raise ItemImportError("skill ZIP holds an unsafe path")
                    if (info.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ItemImportError("skill ZIP holds a symbolic link")
                    total += info.file_size
                    if total > 50 * 1024 * 1024:
                        raise ItemImportError("skill ZIP is larger than 50 MiB unpacked")
                    rel = Path(*parts[1:]) if len(parts) > 1 else Path(parts[0])
                    if rel.name in ("SKILL.md", ".corvin-skill.json") and len(rel.parts) == 1:
                        if rel.name == "SKILL.md" and info.file_size <= 256 * 1024:
                            prompt_body = zf.read(info).decode("utf-8", "replace")
                        continue
                    dest = work / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(zf.read(info))
            # A ZIP carries no empty directories, but the packager requires them.
            for sub in ("src", "hooks", "tests", "scripts", "docs", "references"):
                (work / sub).mkdir(exist_ok=True)
            manifest_file = work / "skill.json"
            if not manifest_file.is_file():
                raise ItemImportError("skill ZIP has no skill.json (only skills exported by Corvin can be imported)")
            manifest = SkillManifestV2.from_dict(json.loads(manifest_file.read_text()))
            zip_path, zip_hash, _meta = SkillPackager(Path(tmp) / "out").package(work, manifest)
            ok, message = SkillInstaller(_forge_paths.corvin_home() / "skills_installed").install_skill(
                zip_path, zip_hash,
                {"skill_id": detected.item_id, "version": detected.item_version, "dependencies": []})
            if not ok:
                raise ItemImportError(
                    "skill installer error" if message.startswith("Error") else f"skill installer refused: {message[:200]}")

        # Audit: import succeeded
        try:
            emit(
                "forge_bundle.artifact_staged",
                tenant_id=tenant_id,
                bundle_id="single-item",
                artifact_kind="skill",
                artifact_id=detected.item_id,
                artifact_version=detected.item_version,
                status="installed",
                actor=actor,
            )
        except ForgeBundleAuditError:
            # Audit failure after install succeeded; log but don't fail
            pass

        # G9: an installed package runs nothing by itself. Its SKILL.md becomes a
        # SkillForge prompt skill (linter, name prefix and grade gate apply); the
        # outcome is reported, never assumed.
        from core.skills.installed_prompt_skills import activate_installed_skill

        activation = activate_installed_skill(
            detected.item_id, detected.item_version or "", prompt_body or "", tenant_id=tenant_id)
        return {
            "status": "installed",
            "detail": f"Skill {detected.item_id} installed; {activation.describe()}",
        }
    except Exception as exc:  # noqa: BLE001
        # Audit: import failed
        try:
            emit(
                "forge_bundle.artifact_failed",
                tenant_id=tenant_id,
                bundle_id="single-item",
                artifact_kind="skill",
                artifact_id=detected.item_id,
                artifact_version=detected.item_version,
                phase="import",
                error_class=type(exc).__name__,
                actor=actor,
            )
        except ForgeBundleAuditError:
            pass

        raise ItemImportError(f"Skill import failed: {exc}") from None


def _import_single_tool(
    detected: DetectedItemZip,
    tenant_id: str,
    actor: str,
) -> dict[str, str]:
    """Import a single tool ZIP to quarantine."""
    # Tools always go to quarantine (manual approval required)
    try:
        emit(
            "forge_bundle.artifact_intake_started",
            tenant_id=tenant_id,
            bundle_id="single-item",
            artifact_kind="tool",
            artifact_id=detected.item_id,
            artifact_version=detected.item_version,
            status="quarantine_queued",
            actor=actor,
        )
    except ForgeBundleAuditError as exc:
        raise ItemImportError(f"Audit failure: {exc}") from None

    try:
        from core.forge_bundle.tool_quarantine import ToolQuarantine

        with zipfile.ZipFile(io.BytesIO(detected.data)) as zf:
            members = [i for i in zf.infolist() if not i.is_dir()]
            spec_infos = [i for i in members if i.filename.rsplit("/", 1)[-1] == "spec.json"]
            impl_infos = [i for i in members if i not in spec_infos]
            if len(spec_infos) != 1 or len(impl_infos) != 1:
                raise ItemImportError("tool ZIP must hold spec.json plus exactly one implementation file")
            spec = json.loads(zf.read(spec_infos[0]))
            impl_bytes = zf.read(impl_infos[0])
        if not isinstance(spec, dict):
            raise ItemImportError("tool spec.json must be an object")
        entry, _created = ToolQuarantine(tenant_id).stage(
            tool_id=detected.item_id, version=detected.item_version,
            bundle_id="single-item", bundle_version=detected.item_version,
            spec=spec, impl_bytes=impl_bytes)
        qid = entry.quarantine_id

        try:
            emit(
                "forge_bundle.artifact_staged",
                tenant_id=tenant_id,
                bundle_id="single-item",
                artifact_kind="tool",
                artifact_id=detected.item_id,
                artifact_version=detected.item_version,
                status="quarantined",
                actor=actor,
            )
        except ForgeBundleAuditError:
            pass

        return {
            "status": "quarantined",
            "detail": f"Tool {detected.item_id} queued for review (quarantine ID: {qid})",
        }
    except Exception as exc:  # noqa: BLE001
        try:
            emit(
                "forge_bundle.artifact_failed",
                tenant_id=tenant_id,
                bundle_id="single-item",
                artifact_kind="tool",
                artifact_id=detected.item_id,
                artifact_version=detected.item_version,
                phase="quarantine",
                error_class=type(exc).__name__,
                actor=actor,
            )
        except ForgeBundleAuditError:
            pass

        raise ItemImportError(f"Tool import failed: {exc}") from None


def _import_single_layer(
    detected: DetectedItemZip,
    tenant_id: str,
    actor: str,
) -> dict[str, str]:
    """Import a single layer ZIP."""
    try:
        emit(
            "forge_bundle.artifact_intake_started",
            tenant_id=tenant_id,
            bundle_id="single-item",
            artifact_kind="layer",
            artifact_id=detected.item_id,
            artifact_version=detected.item_version,
            status="layer_forging",
            actor=actor,
        )
    except ForgeBundleAuditError as exc:
        raise ItemImportError(f"Audit failure: {exc}") from None

    try:
        from core.orchestration.layer_forge.orchestrator import LayerForgeOrchestrator

        from core.forge_bundle.import_module import _check_imported_layer, _IntakeFailed, clean_layer_manifest

        with zipfile.ZipFile(io.BytesIO(detected.data), "r") as zf:
            infos = [i for i in zf.infolist() if i.filename.rsplit("/", 1)[-1] == "manifest.json"]
            if len(infos) != 1:
                raise ItemImportError("layer ZIP must hold exactly one manifest.json")
            manifest = json.loads(zf.read(infos[0]))
        if not isinstance(manifest, dict):
            raise ItemImportError("layer manifest.json must be an object")
        if manifest.get("id") != detected.item_id or manifest.get("version") != detected.item_version:
            raise ItemImportError("layer manifest id/version differ from the ZIP")
        # Same checks as a bundle import: another install's state is dropped and
        # gates/rules/ids are validated BEFORE Layer Forge sees the manifest.
        manifest = clean_layer_manifest(manifest)
        try:
            _check_imported_layer(manifest)
        except _IntakeFailed as exc:
            raise ItemImportError(str(exc)) from None

        orchestrator = LayerForgeOrchestrator(tenant_id, actor="bundle_import")
        result = orchestrator.create_layer_definition(manifest)
        if result.status != "SUCCESS":
            raise ItemImportError(f"layer forge refused at the {result.phase} phase")

        try:
            emit(
                "forge_bundle.artifact_staged",
                tenant_id=tenant_id,
                bundle_id="single-item",
                artifact_kind="layer",
                artifact_id=detected.item_id,
                artifact_version=detected.item_version,
                status="forged",
                actor=actor,
            )
        except ForgeBundleAuditError:
            pass

        return {
            "status": "forged",
            "detail": f"Layer {detected.item_id} forged successfully",
        }
    except Exception as exc:  # noqa: BLE001
        try:
            emit(
                "forge_bundle.artifact_failed",
                tenant_id=tenant_id,
                bundle_id="single-item",
                artifact_kind="layer",
                artifact_id=detected.item_id,
                artifact_version=detected.item_version,
                phase="forge",
                error_class=type(exc).__name__,
                actor=actor,
            )
        except ForgeBundleAuditError:
            pass

        raise ItemImportError(f"Layer import failed: {exc}") from None


def _import_single_plugin(
    detected: DetectedItemZip,
    tenant_id: str,
    actor: str,
) -> dict[str, str]:
    """Import a single plugin ZIP to staging."""
    try:
        emit(
            "forge_bundle.artifact_intake_started",
            tenant_id=tenant_id,
            bundle_id="single-item",
            artifact_kind="plugin",
            artifact_id=detected.item_id,
            artifact_version=detected.item_version,
            status="plugin_staging",
            actor=actor,
        )
    except ForgeBundleAuditError as exc:
        raise ItemImportError(f"Audit failure: {exc}") from None

    try:
        from core.plugins.staging import StagingManager

        staging = StagingManager(tenant_id)
        # Validate and stage the plugin
        ok, manifest, errors = staging.validate_zip_file(io.BytesIO(detected.data))
        if not ok:
            raise ItemImportError(f"Plugin validation failed: {errors}")

        # Write to staging directory
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            with zipfile.ZipFile(io.BytesIO(detected.data), "r") as zf:
                zf.extractall(tmp_path)

            # Stage the plugin (copying to the plugin-uploads directory)
            upload_id = staging.stage_plugin(tmp_path)

        try:
            emit(
                "forge_bundle.artifact_staged",
                tenant_id=tenant_id,
                bundle_id="single-item",
                artifact_kind="plugin",
                artifact_id=detected.item_id,
                artifact_version=detected.item_version,
                status="staged",
                actor=actor,
            )
        except ForgeBundleAuditError:
            pass

        return {
            "status": "staged",
            "detail": f"Plugin {detected.item_id} staged for approval (ID: {upload_id})",
        }
    except Exception as exc:  # noqa: BLE001
        try:
            emit(
                "forge_bundle.artifact_failed",
                tenant_id=tenant_id,
                bundle_id="single-item",
                artifact_kind="plugin",
                artifact_id=detected.item_id,
                artifact_version=detected.item_version,
                phase="staging",
                error_class=type(exc).__name__,
                actor=actor,
            )
        except ForgeBundleAuditError:
            pass

        raise ItemImportError(f"Plugin import failed: {exc}") from None
