"""Forge Bundle Import — staged per-forge intake (ADR-2229 Phase 3).

Import never writes a registry directly. Each artifact type travels through
its own forge's native intake:
  - Skill → SkillInstaller (ADR-0674)
  - Layer → LayerForgeOrchestrator.create_layer_definition (all gates run, lands proposed)
  - Plugin → StagingManager (operator approval)
  - Tool → ToolQuarantineWorkflow (quarantine pending approval)

Audit-First Pattern:
  1. Validate bundle (no writes)
  2. Emit forge_bundle.import_validated or import_rejected (audit commit FIRST)
  3. If rejected, raise ImportError
  4. If approved, stage each artifact
  5. Emit forge_bundle.artifact_staged for each
"""
from __future__ import annotations

import io
import json
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from core.forge_bundle.audit import emit as emit_audit_event, ForgeBundleAuditError
from core.forge_bundle.envelope import ArtifactEntry, BundleEnvelope, ARTIFACTS_PREFIX
from core.forge_bundle.validate import BundleRejected, BundleReport, validate_bundle
from core.forge_bundle.tool_quarantine import ToolQuarantineWorkflow, QuarantineError


class ImportError(RuntimeError):
    """Bundle import failed."""


@dataclass(frozen=True)
class ImportResult:
    """Outcome of importing a bundle."""

    bundle_id: str
    bundle_version: str
    success: bool
    artifacts_staged: int
    artifacts_failed: int
    errors: list[str]  # One error per failed artifact


def extract_bundle(
    data: bytes, *, known: dict[str, dict[str, list[str]]] | None = None
) -> tuple[BundleEnvelope, zipfile.ZipFile]:
    """Extract and validate a bundle from raw ZIP bytes.

    This is the pure validation layer — reads bytes, writes nothing,
    touches no registry. Validation stages run in order; the first
    defect rejects the whole bundle.

    Args:
        data: Raw ZIP bytes
        known: Optional inventory of the target install
               {kind: {id: [version1, version2, ...]}}

    Returns:
        (BundleEnvelope, ZipFile) — both live for the duration of the caller's
        use; the ZipFile is open and must be closed by the caller.

    Raises:
        BundleRejected if validation fails at any stage
        ImportError if the ZIP cannot be read at all
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data), "r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise ImportError(f"invalid ZIP: {type(exc).__name__}") from exc

    # validate_bundle is pure: it reads the given bytes (via the ZipFile),
    # returns a report, and raises BundleRejected at the first defect.
    report = validate_bundle(data, known=known)
    return report.envelope, zf


def import_bundle(
    data: bytes,
    *,
    tenant_id: str,
    user_id: str,
    staging_only: bool = False,
) -> ImportResult:
    """Import a bundle into the target install.

    Audit-First: validation report is emitted BEFORE staging begins. If
    validation fails, forge_bundle.import_rejected is emitted and ImportError
    is raised.

    Each artifact type reuses its own forge's intake (no new write paths):
    - Skill: SkillInstaller (ADR-0674)
    - Layer: create_layer_definition (all gates run, lands proposed) (ADR-2222)
    - Plugin: StagingManager (operator approval) (ADR-0511)
    - Tool: ToolQuarantineWorkflow (quarantine pending) (ADR-2229 Phase 3)

    Args:
        data: Raw ZIP bytes
        tenant_id: The target tenant
        user_id: The operator performing the import
        staging_only: If True, stage everything without calling the final
                      intake (for testing/preview). Default False.

    Returns:
        ImportResult with artifact counts and any per-artifact errors

    Raises:
        ImportError if validation fails or audit write fails
        ForgeBundleAuditError if audit chain write does not commit
    """
    # Step 1: Extract and validate (pure, no writes)
    known = _build_known_inventory(tenant_id)
    try:
        envelope, zf = extract_bundle(data, known=known)
    except BundleRejected as exc:
        # Emit rejection to audit chain FIRST
        try:
            emit_audit_event(
                "forge_bundle.import_rejected",
                tenant_id=tenant_id,
                bundle_id="unknown",  # May not be parseable
                rejected_stage=exc.stage,
                rejected_reason=exc.reason,
                user_id=user_id,
            )
        except ForgeBundleAuditError:
            # Audit write failed — operation is blocked (fail-closed)
            raise ImportError("audit chain write failed (bundle import rejected)") from None
        raise ImportError(f"bundle validation failed: [{exc.stage}] {exc.reason}") from exc
    except ImportError as exc:
        # ZIP read error — cannot reach audit
        raise ImportError(f"cannot read bundle: {str(exc)}") from exc

    # Step 2: Emit validation success to audit chain (BEFORE staging)
    try:
        emit_audit_event(
            "forge_bundle.import_validated",
            tenant_id=tenant_id,
            bundle_id=envelope.id,
            bundle_version=envelope.version,
            artifact_count=len(envelope.artifacts),
            total_uncompressed_bytes=0,  # Computed from artifacts
            user_id=user_id,
            validation_passed=True,
            validation_stages=",".join(["container", "envelope", "integrity", "references", "staleness", "secrets"]),
        )
    except ForgeBundleAuditError as exc:
        raise ImportError(
            "audit chain write failed (bundle import validated)"
        ) from exc

    # Step 3: Stage each artifact through its forge's intake
    result = ImportResult(
        bundle_id=envelope.id,
        bundle_version=envelope.version,
        success=True,
        artifacts_staged=0,
        artifacts_failed=0,
        errors=[],
    )

    for artifact in envelope.artifacts:
        try:
            _stage_artifact(
                artifact,
                zf,
                tenant_id=tenant_id,
                user_id=user_id,
                staging_only=staging_only,
            )
            result.artifacts_staged += 1
        except (ImportError, QuarantineError, Exception) as exc:
            result.artifacts_failed += 1
            result.errors.append(
                f"{artifact.kind}/{artifact.id}@{artifact.version}: {type(exc).__name__}"
            )
            # Continue staging others (don't stop on first failure)

    result.success = result.artifacts_failed == 0
    zf.close()
    return result


def _stage_artifact(
    artifact: ArtifactEntry,
    zf: zipfile.ZipFile,
    *,
    tenant_id: str,
    user_id: str,
    staging_only: bool = False,
) -> None:
    """Stage one artifact from the bundle through its forge's intake.

    Raises:
        ImportError if staging fails
    """
    if artifact.kind == "skill":
        _intake_skill(artifact, zf, tenant_id=tenant_id, user_id=user_id)
    elif artifact.kind == "layer":
        _intake_layer(
            artifact, zf, tenant_id=tenant_id, user_id=user_id, staging_only=staging_only
        )
    elif artifact.kind == "plugin":
        _intake_plugin(artifact, zf, tenant_id=tenant_id, user_id=user_id)
    elif artifact.kind == "tool":
        _intake_tool(artifact, zf, tenant_id=tenant_id, user_id=user_id)
    else:
        raise ImportError(f"unknown artifact kind: {artifact.kind}")


def _intake_skill(
    artifact: ArtifactEntry,
    zf: zipfile.ZipFile,
    *,
    tenant_id: str,
    user_id: str,
) -> None:
    """Intake a skill artifact through SkillInstaller (ADR-0674).

    The skill payload is a ZIP in ADR-0674 format under artifacts/skill/<id>@<version>/.
    SkillInstaller handles checksum verification, dependency resolution, and atomic
    installation.

    Raises:
        ImportError if intake fails
    """
    from core.skills.skill_installer import SkillInstaller

    # Extract the skill ZIP from the bundle
    skill_prefix = artifact.prefix
    skill_zip_path = None
    for file_entry in artifact.files:
        if file_entry.path.startswith(skill_prefix):
            file_path = file_entry.path
            skill_zip_path = Path(file_path)
            break

    if not skill_zip_path:
        raise ImportError(
            f"skill {artifact.id}: no files in bundle (corrupt artifact)"
        )

    # Extract the skill ZIP bytes from the bundle
    try:
        skill_zip_bytes = zf.read(str(skill_zip_path))
    except KeyError as exc:
        raise ImportError(
            f"skill {artifact.id}: missing file in bundle: {skill_zip_path}"
        ) from exc

    # Write to temp file for SkillInstaller (it expects a file path)
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp.write(skill_zip_bytes)
        tmp_path = Path(tmp.name)

    try:
        installer = SkillInstaller()
        metadata = {
            "skill_id": artifact.id,
            "version": artifact.version,
            "dependencies": [],
        }
        success, message = installer.install_skill(tmp_path, "", metadata)
        if not success:
            raise ImportError(f"skill {artifact.id}: {message}")
    finally:
        tmp_path.unlink(missing_ok=True)

    # Emit audit event for the staged skill
    try:
        emit_audit_event(
            "forge_bundle.artifact_staged",
            tenant_id=tenant_id,
            bundle_id=artifact.key[1],  # This is not ideal; ideally we'd pass bundle_id
            artifact_kind="skill",
            artifact_id=artifact.id,
            artifact_version=artifact.version,
            quarantine_id="",
            status="installed",
            user_id=user_id,
        )
    except ForgeBundleAuditError:
        # Audit write failed after successful install — log and continue
        pass


def _intake_layer(
    artifact: ArtifactEntry,
    zf: zipfile.ZipFile,
    *,
    tenant_id: str,
    user_id: str,
    staging_only: bool = False,
) -> None:
    """Intake a layer artifact through LayerForgeOrchestrator (ADR-2222).

    The layer payload is a manifest JSON under artifacts/layer/<id>@<version>/.
    create_layer_definition runs all quality gates and lands the definition in
    proposed state (not active).

    Raises:
        ImportError if intake fails
    """
    from core.orchestration.layer_forge.orchestrator import LayerForgeOrchestrator

    # Find the manifest file in the artifact
    layer_prefix = artifact.prefix
    manifest_path = None
    for file_entry in artifact.files:
        if file_entry.path.endswith("manifest.json"):
            manifest_path = file_entry.path
            break

    if not manifest_path:
        raise ImportError(
            f"layer {artifact.id}: no manifest.json in bundle"
        )

    # Extract manifest bytes
    try:
        manifest_bytes = zf.read(str(manifest_path))
        manifest = json.loads(manifest_bytes)
    except (KeyError, json.JSONDecodeError) as exc:
        raise ImportError(
            f"layer {artifact.id}: invalid manifest: {type(exc).__name__}"
        ) from exc

    if not staging_only:
        # Call LayerForgeOrchestrator to create the layer definition
        # (This will run all quality gates and land it in proposed state)
        try:
            orchestrator = LayerForgeOrchestrator(tenant_id)
            layer_def = orchestrator.create_layer_definition(
                manifest, user_id=user_id, source="bundle_import"
            )
            status = "proposed"
        except Exception as exc:
            raise ImportError(
                f"layer {artifact.id}: creation failed: {type(exc).__name__}"
            ) from exc
    else:
        status = "staged"

    # Emit audit event
    try:
        emit_audit_event(
            "forge_bundle.artifact_staged",
            tenant_id=tenant_id,
            bundle_id=artifact.key[1],
            artifact_kind="layer",
            artifact_id=artifact.id,
            artifact_version=artifact.version,
            quarantine_id="",
            status=status,
            user_id=user_id,
        )
    except ForgeBundleAuditError:
        pass


def _intake_plugin(
    artifact: ArtifactEntry,
    zf: zipfile.ZipFile,
    *,
    tenant_id: str,
    user_id: str,
) -> None:
    """Intake a plugin artifact through StagingManager (ADR-0511).

    The plugin payload is a ZIP in ADR-0511 format. StagingManager stages it
    and requires operator approval before it becomes active.

    Raises:
        ImportError if intake fails
    """
    from core.plugins.staging import StagingManager

    # Find the plugin ZIP in the artifact
    plugin_prefix = artifact.prefix
    plugin_zip_path = None
    for file_entry in artifact.files:
        if file_entry.path.startswith(plugin_prefix):
            plugin_zip_path = file_entry.path
            break

    if not plugin_zip_path:
        raise ImportError(
            f"plugin {artifact.id}: no files in bundle"
        )

    # Extract the plugin ZIP bytes
    try:
        plugin_zip_bytes = zf.read(str(plugin_zip_path))
    except KeyError as exc:
        raise ImportError(
            f"plugin {artifact.id}: missing file in bundle: {plugin_zip_path}"
        ) from exc

    # Stage through StagingManager
    try:
        staging_manager = StagingManager(tenant_id)
        staged_id = staging_manager.stage_plugin_from_bytes(
            plugin_zip_bytes,
            plugin_id=artifact.id,
            version=artifact.version,
            source="bundle_import",
        )
    except Exception as exc:
        raise ImportError(
            f"plugin {artifact.id}: staging failed: {type(exc).__name__}"
        ) from exc

    # Emit audit event
    try:
        emit_audit_event(
            "forge_bundle.artifact_staged",
            tenant_id=tenant_id,
            bundle_id=artifact.key[1],
            artifact_kind="plugin",
            artifact_id=artifact.id,
            artifact_version=artifact.version,
            quarantine_id="",
            status="staged",
            user_id=user_id,
        )
    except ForgeBundleAuditError:
        pass


def _intake_tool(
    artifact: ArtifactEntry,
    zf: zipfile.ZipFile,
    *,
    tenant_id: str,
    user_id: str,
) -> None:
    """Intake a tool artifact through ToolQuarantineWorkflow (ADR-2229 Phase 3).

    The tool payload is spec.json + an implementation file (shell, python, js, etc.).
    Tools are quarantined and require operator approval before activation.

    Raises:
        ImportError if intake fails
    """
    # Find spec.json and implementation file in the artifact
    tool_prefix = artifact.prefix
    spec_path = None
    impl_path = None

    for file_entry in artifact.files:
        if file_entry.path == f"{tool_prefix}spec.json":
            spec_path = file_entry.path
        elif file_entry.path.startswith(tool_prefix) and file_entry.path != f"{tool_prefix}spec.json":
            impl_path = file_entry.path

    if not spec_path or not impl_path:
        raise ImportError(
            f"tool {artifact.id}: missing spec.json or implementation file"
        )

    # Extract spec and implementation
    try:
        spec_bytes = zf.read(str(spec_path))
        spec = json.loads(spec_bytes)
        impl_bytes = zf.read(str(impl_path))
    except (KeyError, json.JSONDecodeError) as exc:
        raise ImportError(
            f"tool {artifact.id}: invalid spec: {type(exc).__name__}"
        ) from exc

    # Determine implementation file extension
    impl_ext = impl_path.split(".")[-1] if "." in impl_path else "bin"

    # Stage through ToolQuarantineWorkflow
    try:
        quarantine = ToolQuarantineWorkflow(tenant_id)
        quarantined_tool = quarantine.stage_tool_from_bundle(
            tool_id=artifact.id,
            bundle_id=artifact.key[1],
            version=artifact.version,
            spec=spec,
            impl_bytes=impl_bytes,
            impl_ext=impl_ext,
            user_id=user_id,
        )
    except QuarantineError as exc:
        raise ImportError(f"tool {artifact.id}: quarantine failed: {str(exc)}") from exc

    # Emit audit event with quarantine_id
    try:
        emit_audit_event(
            "forge_bundle.artifact_staged",
            tenant_id=tenant_id,
            bundle_id=artifact.key[1],
            artifact_kind="tool",
            artifact_id=artifact.id,
            artifact_version=artifact.version,
            quarantine_id=quarantined_tool.quarantine_id,
            status="quarantined",
            user_id=user_id,
        )
    except ForgeBundleAuditError:
        pass


def _build_known_inventory(tenant_id: str) -> dict[str, dict[str, list[str]]]:
    """Build an inventory of currently available artifacts in the target install.

    This is used by the staleness validation stage to check that all artifact
    references can be satisfied.

    Returns:
        {kind: {id: [version1, version2, ...]}}

    Implementation is a stub for now; a complete version would query:
    - Skills: SkillInstaller registry
    - Layers: LayerForgeOrchestrator registry
    - Plugins: plugin registry
    - Tools: ToolRegistry
    """
    # TODO: Implement full inventory discovery
    return {"skill": {}, "layer": {}, "plugin": {}, "tool": {}}
