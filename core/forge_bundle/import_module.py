"""Forge Bundle import — staged per-forge intake (ADR-2229 D2/D3, Phase 3).

Import never writes a registry directly. After the whole bundle passes
``validate_bundle`` (with this install's inventory as ``known``), each artifact
enters through its own forge's existing intake:

  skill  -> ``SkillInstaller`` (ADR-0674/0680), checksum = the envelope's sha256
  layer  -> ``LayerForgeOrchestrator.create_layer_definition`` — every gate runs,
            the result is the same state a locally forged layer reaches
  plugin -> ``StagingManager`` (ADR-0511): staged ``pending_approval``; the
            operator approves it under the existing plugin-upload routes
  tool   -> :mod:`.tool_quarantine` — nothing is callable until an operator
            accepts it

Audit-first and fail-closed: ``import_rejected`` / ``import_validated`` commit
before anything is written; for every artifact ``artifact_staged`` commits
BEFORE its intake runs, and an intake that then fails is recorded as
``artifact_failed``. A chain write that does not commit raises
:class:`ForgeBundleAuditError` and stops the import at that point.
"""
from __future__ import annotations

import io
import json
import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .audit import ForgeBundleAuditError, emit
from .envelope import ArtifactEntry, BundleEnvelope
from .validate import BundleRejected, BundleReport, validate_bundle

INTENDED_STATUS = {
    "skill": "installed",
    "layer": "forged",
    "plugin": "pending_approval",
    "tool": "quarantined",
}


class BundleImportError(RuntimeError):
    """The bundle was refused before any artifact was staged."""

    def __init__(self, stage: str, reason: str):
        super().__init__(f"[{stage}] {reason}")
        self.stage = stage
        self.reason = reason


class _IntakeFailed(Exception):
    pass


@dataclass(frozen=True)
class ArtifactOutcome:
    kind: str
    id: str
    version: str
    status: str            # installed | forged | pending_approval | quarantined | failed
    detail: str = ""       # quarantine id, plugin upload id, layer key, or failure reason

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass(frozen=True)
class ImportResult:
    bundle_id: str
    bundle_version: str
    outcomes: tuple[ArtifactOutcome, ...]
    unscanned_files: tuple[str, ...]
    origin_verified: bool = False

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "failed")

    def to_dict(self) -> dict:
        return {
            "bundle_id": self.bundle_id,
            "bundle_version": self.bundle_version,
            "artifact_count": len(self.outcomes),
            "failed_count": self.failed,
            "outcomes": [o.to_dict() for o in self.outcomes],
            "unscanned_files": list(self.unscanned_files),
            "origin_verified": self.origin_verified,
        }


def check_bundle(data: bytes, *, tenant_id: str) -> BundleReport:
    """Validate against this install's inventory. Pure: writes nothing."""
    from .inventory import known

    return validate_bundle(data, known=known(tenant_id))


def import_bundle(data: bytes, *, tenant_id: str, actor: str) -> ImportResult:
    try:
        report = check_bundle(data, tenant_id=tenant_id)
    except BundleRejected as exc:
        emit("forge_bundle.import_rejected", tenant_id=tenant_id,
             rejected_stage=exc.stage, actor=actor)
        raise BundleImportError(exc.stage, exc.reason) from None

    env = report.envelope
    emit("forge_bundle.import_validated", tenant_id=tenant_id,
         bundle_id=env.id, bundle_version=env.version,
         artifact_count=len(env.artifacts),
         total_uncompressed_bytes=report.total_uncompressed_bytes,
         unscanned_files_count=len(report.unscanned_files), actor=actor)

    outcomes: list[ArtifactOutcome] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for art in env.artifacts:
            outcomes.append(_stage_one(art, env, zf, tenant_id=tenant_id, actor=actor))

    return ImportResult(env.id, env.version, tuple(outcomes), report.unscanned_files)


def _stage_one(art: ArtifactEntry, env: BundleEnvelope, zf: zipfile.ZipFile, *,
               tenant_id: str, actor: str) -> ArtifactOutcome:
    common = dict(tenant_id=tenant_id, bundle_id=env.id, artifact_kind=art.kind,
                  artifact_id=art.id, artifact_version=art.version, actor=actor)
    emit("forge_bundle.artifact_staged", status=INTENDED_STATUS[art.kind], **common)
    try:
        detail = _INTAKES[art.kind](art, env, zf, tenant_id)
    except ForgeBundleAuditError:
        raise
    except Exception as exc:  # noqa: BLE001 — any intake failure is recorded, then reported
        emit("forge_bundle.artifact_failed", phase="intake",
             error_class=type(exc).__name__, **common)
        reason = str(exc) if isinstance(exc, _IntakeFailed) else type(exc).__name__
        return ArtifactOutcome(art.kind, art.id, art.version, "failed", reason[:500])
    return ArtifactOutcome(art.kind, art.id, art.version, INTENDED_STATUS[art.kind], detail)


def _files(art: ArtifactEntry, zf: zipfile.ZipFile) -> dict[str, bytes]:
    """Payload files keyed by their name relative to the artifact's prefix."""
    return {f.path[len(art.prefix):]: zf.read(f.path) for f in art.files}


def _single(art: ArtifactEntry, zf: zipfile.ZipFile) -> tuple[str, bytes]:
    files = _files(art, zf)
    if len(files) != 1:
        raise _IntakeFailed(f"{art.kind} payload must be exactly one file, got {len(files)}")
    return next(iter(files.items()))


def _intake_skill(art: ArtifactEntry, env: BundleEnvelope, zf: zipfile.ZipFile, tenant_id: str) -> str:
    from forge import paths as _forge_paths

    from core.skills.skill_installer import SkillInstaller

    name, payload = _single(art, zf)
    sha = next(f.sha256 for f in art.files)
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / Path(name).name
        zip_path.write_bytes(payload)
        installer = SkillInstaller(_forge_paths.corvin_home() / "skills_installed")
        ok, message = installer.install_skill(
            zip_path, sha, {"skill_id": art.id, "version": art.version, "dependencies": []})
    if not ok:
        raise _IntakeFailed(f"skill installer refused: {message}")
    return f"{art.id}@{art.version}"


def _intake_layer(art: ArtifactEntry, env: BundleEnvelope, zf: zipfile.ZipFile, tenant_id: str) -> str:
    from core.orchestration.layer_forge.orchestrator import LayerForgeOrchestrator

    files = _files(art, zf)
    if "manifest.json" not in files:
        raise _IntakeFailed("layer payload has no manifest.json")
    try:
        manifest = json.loads(files["manifest.json"])
    except ValueError:
        raise _IntakeFailed("layer manifest.json is not JSON") from None
    if manifest.get("id") != art.id or manifest.get("version") != art.version:
        raise _IntakeFailed("layer manifest id/version differ from the envelope")
    result = LayerForgeOrchestrator(tenant_id, actor="bundle_import").create_layer_definition(manifest)
    if result.status != "SUCCESS":
        raise _IntakeFailed(f"layer forge refused at {result.phase}: {result.error}")
    return result.registry_key or f"{art.id}@{art.version}"


def _intake_plugin(art: ArtifactEntry, env: BundleEnvelope, zf: zipfile.ZipFile, tenant_id: str) -> str:
    from core.plugins.staging import StagingManager

    _name, payload = _single(art, zf)
    manager = StagingManager(tenant_id)
    fd, tmp_name = tempfile.mkstemp(dir=manager.staging_root, prefix="bundle.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        ok, manifest, errors = manager.validate_zip_file(tmp_path)
        if not ok:
            raise _IntakeFailed("not an ADR-0511 plugin package: " + "; ".join(errors[:5]))
        upload_id = manager.compute_file_hash(tmp_path)[:16]
        manager.store_staged_upload(upload_id, tmp_path, manifest)
    finally:
        tmp_path.unlink(missing_ok=True)
    return upload_id


def _intake_tool(art: ArtifactEntry, env: BundleEnvelope, zf: zipfile.ZipFile, tenant_id: str) -> str:
    from .tool_quarantine import QuarantineError, ToolQuarantine

    files = _files(art, zf)
    if "spec.json" not in files or len(files) != 2:
        raise _IntakeFailed("tool payload must be spec.json plus exactly one implementation file")
    try:
        spec = json.loads(files.pop("spec.json"))
    except ValueError:
        raise _IntakeFailed("tool spec.json is not JSON") from None
    (_impl_name, impl_bytes), = files.items()
    try:
        entry = ToolQuarantine(tenant_id).stage(
            tool_id=art.id, version=art.version, bundle_id=env.id, bundle_version=env.version,
            spec=spec, impl_bytes=impl_bytes)
    except QuarantineError as exc:
        raise _IntakeFailed(f"tool quarantine refused: {exc}") from None
    return entry.quarantine_id


_INTAKES = {
    "skill": _intake_skill,
    "layer": _intake_layer,
    "plugin": _intake_plugin,
    "tool": _intake_tool,
}
