"""Forge Bundle import — staged per-forge intake (ADR-2229 D2/D3, Phase 3).

Import never writes a registry directly. After the whole bundle passes
``validate_bundle`` (with this install's inventory as ``known``), each artifact
enters through its own forge's existing intake, in dependency order:

  skill  -> ``SkillInstaller`` (ADR-0674/0680), checksum = the envelope's sha256.
            The installed-skill store is host-wide, so — exactly like a manual
            upload through the skill manager — only an owner/admin may do it.
  layer  -> ``LayerForgeOrchestrator.create_layer_definition`` — every gate,
            enforcement rule and the review run; registry state that travelled
            in the manifest (status, review flags, ``_*`` keys) is dropped first
  plugin -> ``StagingManager`` (ADR-0511): staged ``pending_approval``; approved
            under the existing plugin-upload routes
  tool   -> :mod:`.tool_quarantine` — nothing is callable until an operator accepts

Audit, two records per artifact: ``artifact_intake_started`` (the intent)
commits BEFORE the intake runs, then ``artifact_staged`` (the actual outcome)
or ``artifact_failed``. If a record cannot commit, the import stops there and
:class:`BundleImportAborted` carries every outcome so far — what landed, what
was not attempted — instead of a blanket "nothing changed".
"""
from __future__ import annotations

import io
import json
import re
import shutil
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
# Registry state of the exporting install that must never become this install's state.
LAYER_STATE_KEYS = frozenset({"status", "review_flagged", "review_flags"})
MAX_IMPORTED_GATES = 16             # per layer
MAX_IMPORTED_GATES_PER_BUNDLE = 16  # all layers together: every gate is a pytest run in the request
_GATE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_TEST_FILE_RE = re.compile(r"^tests/[A-Za-z0-9_./-]+\.py$")


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
    # installed | forged | pending_approval | quarantined | failed | not_attempted
    status: str
    detail: str = ""       # quarantine id, plugin upload id, layer key, or failure reason

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass(frozen=True)
class ImportResult:
    bundle_id: str
    bundle_version: str
    outcomes: tuple[ArtifactOutcome, ...]
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
            "origin_verified": self.origin_verified,
        }


class BundleImportAborted(ForgeBundleAuditError):
    """An audit record did not commit mid-import; ``result`` says what landed."""

    def __init__(self, result: ImportResult, cause: str):
        super().__init__(cause)
        self.result = result


def check_bundle(data: bytes, *, tenant_id: str) -> BundleReport:
    """Validate against this install's inventory. Pure: writes nothing.

    An inventory that cannot be read is a refusal (stage ``inventory``), never
    a pass and never a 500."""
    from .inventory import InventoryUnavailable, known

    try:
        inventory = known(tenant_id)
    except InventoryUnavailable as exc:
        raise BundleRejected("inventory", str(exc)) from None
    return validate_bundle(data, known=inventory)


def import_bundle(data: bytes, *, tenant_id: str, actor: str, may_install_skills: bool) -> ImportResult:
    try:
        report = check_bundle(data, tenant_id=tenant_id)
    except BundleRejected as exc:
        emit("forge_bundle.import_rejected", tenant_id=tenant_id, rejected_stage=exc.stage, actor=actor)
        raise BundleImportError(exc.stage, exc.reason) from None

    env = report.envelope
    total_gates = _count_layer_gates(env, data)
    if total_gates > MAX_IMPORTED_GATES_PER_BUNDLE:
        emit("forge_bundle.import_rejected", tenant_id=tenant_id, rejected_stage="limits", actor=actor)
        raise BundleImportError("limits", f"the bundle's layers declare {total_gates} quality gates; "
                                          f"at most {MAX_IMPORTED_GATES_PER_BUNDLE} run per import")
    emit("forge_bundle.import_validated", tenant_id=tenant_id,
         bundle_id=env.id, bundle_version=env.version, artifact_count=len(env.artifacts),
         total_uncompressed_bytes=report.total_uncompressed_bytes, actor=actor)

    outcomes: list[ArtifactOutcome] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        ordered, edges = _dependency_order(env, zf)
        version_of = {a.key: a.version for a in env.artifacts}
        landed: set[tuple[str, str]] = set()
        for i, art in enumerate(ordered):
            missing = [f"{k} {d}" for k, d in sorted(edges[art.key])
                       if (k, d) not in landed and not _host_has(tenant_id, k, d, version_of[(k, d)])]
            try:
                outcome = _stage_one(art, env, zf, tenant_id=tenant_id, actor=actor,
                                     may_install_skills=may_install_skills, missing_deps=missing)
                outcomes.append(outcome)
                if outcome.status not in ("failed", "not_attempted"):
                    landed.add(art.key)
            except _Aborted as abort:
                if abort.outcome is not None:
                    outcomes.append(abort.outcome)
                outcomes.extend(ArtifactOutcome(a.kind, a.id, a.version, "not_attempted")
                                for a in ordered[i + 1 if abort.outcome is not None else i:])
                raise BundleImportAborted(ImportResult(env.id, env.version, tuple(outcomes)),
                                          "audit chain unavailable; import stopped") from None
    return ImportResult(env.id, env.version, tuple(outcomes))


class _Aborted(Exception):
    def __init__(self, outcome: ArtifactOutcome | None):
        super().__init__("audit chain unavailable")
        self.outcome = outcome


def _host_has(tenant_id: str, kind: str, aid: str, version: str) -> bool:
    """This install already holds exactly ``aid@version`` — a bundled copy that
    failed only because it is already there still satisfies its dependents."""
    from .inventory import InventoryUnavailable, known

    try:
        versions = known(tenant_id).get(kind, {}).get(aid)
    except InventoryUnavailable:
        return False
    return versions is not None and version in versions


def _count_layer_gates(env: BundleEnvelope, data: bytes) -> int:
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for a in env.artifacts:
            if a.kind != "layer":
                continue
            try:
                manifest = json.loads(zf.read(f"{a.prefix}manifest.json"))
                gates = manifest.get("quality_gates", []) if isinstance(manifest, dict) else []
                total += len(gates) if isinstance(gates, list) else 0
            except (KeyError, ValueError, RecursionError):
                pass  # the layer intake reports the broken manifest
    return total


def _dependency_order(env: BundleEnvelope, zf: zipfile.ZipFile) -> tuple[list[ArtifactEntry], dict]:
    """Requirements first. Edges: declared ``requires`` plus, for layers, the
    manifest's own ``dependencies`` on other layers in the same bundle (the
    validator already refused cycles among declared requires)."""
    by_key = {a.key: a for a in env.artifacts}
    edges: dict[tuple[str, str], set[tuple[str, str]]] = {a.key: set() for a in env.artifacts}
    for a in env.artifacts:
        for r in a.requires:
            if (r.kind, r.id) in by_key:
                edges[a.key].add((r.kind, r.id))
        if a.kind == "layer":
            try:
                manifest = json.loads(zf.read(f"{a.prefix}manifest.json"))
                deps = manifest.get("dependencies", []) if isinstance(manifest, dict) else []
                for d in deps if isinstance(deps, list) else []:
                    # Layer Forge resolves a dependency by id; a missing "type" still binds.
                    if (isinstance(d, dict) and isinstance(d.get("id"), str)
                            and d.get("type") in (None, "layer_definition")
                            and ("layer", d["id"]) in by_key):
                        edges[a.key].add(("layer", d["id"]))
            except (KeyError, ValueError, RecursionError):
                pass  # the layer intake reports the broken manifest
    order: list[ArtifactEntry] = []
    state: dict[tuple[str, str], int] = {}

    def visit(key: tuple[str, str]) -> None:
        if state.get(key) == 2:
            return
        if state.get(key) == 1:  # a manifest-level cycle: keep envelope order for it
            return
        state[key] = 1
        for dep in sorted(edges[key]):
            visit(dep)
        state[key] = 2
        order.append(by_key[key])

    for a in env.artifacts:
        visit(a.key)
    return order, edges


def _stage_one(art: ArtifactEntry, env: BundleEnvelope, zf: zipfile.ZipFile, *,
               tenant_id: str, actor: str, may_install_skills: bool,
               missing_deps: list[str]) -> ArtifactOutcome:
    common = dict(tenant_id=tenant_id, bundle_id=env.id, artifact_kind=art.kind,
                  artifact_id=art.id, artifact_version=art.version, actor=actor)
    try:
        emit("forge_bundle.artifact_intake_started", status=INTENDED_STATUS[art.kind], **common)
    except ForgeBundleAuditError:
        raise _Aborted(None) from None
    try:
        if missing_deps:
            raise _IntakeFailed(f"depends on {', '.join(missing_deps)} from this bundle, which did not import")
        if art.kind == "skill" and not may_install_skills:
            raise _IntakeFailed("installing a skill changes the host-wide skill store; only the install owner may do that")
        status, detail = _INTAKES[art.kind](art, env, zf, tenant_id)
    except Exception as exc:  # noqa: BLE001 — any intake failure is recorded, then reported
        reason = str(exc) if isinstance(exc, _IntakeFailed) else f"intake error ({type(exc).__name__})"
        outcome = ArtifactOutcome(art.kind, art.id, art.version, "failed", reason[:500])
        try:
            emit("forge_bundle.artifact_failed", phase="intake", error_class=type(exc).__name__, **common)
        except ForgeBundleAuditError:
            raise _Aborted(outcome) from None
        return outcome
    outcome = ArtifactOutcome(art.kind, art.id, art.version, status, detail)
    try:
        emit("forge_bundle.artifact_staged", status=status, **common)
    except ForgeBundleAuditError:
        raise _Aborted(outcome) from None
    return outcome


def _files(art: ArtifactEntry, zf: zipfile.ZipFile) -> dict[str, bytes]:
    """Payload files keyed by their name relative to the artifact's prefix."""
    return {f.path[len(art.prefix):]: zf.read(f.path) for f in art.files}


def _single(art: ArtifactEntry, zf: zipfile.ZipFile) -> tuple[str, bytes]:
    files = _files(art, zf)
    if len(files) != 1:
        raise _IntakeFailed(f"{art.kind} payload must be exactly one file, got {len(files)}")
    return next(iter(files.items()))


def _intake_skill(art: ArtifactEntry, env: BundleEnvelope, zf: zipfile.ZipFile, tenant_id: str) -> tuple[str, str]:
    from forge import paths as _forge_paths

    from core.skills.skill_installer import SkillInstaller

    _name, payload = _single(art, zf)
    sha = art.files[0].sha256
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / f"{art.id}_{art.version}.zip"
        zip_path.write_bytes(payload)
        installer = SkillInstaller(_forge_paths.corvin_home() / "skills_installed")
        ok, message = installer.install_skill(
            zip_path, sha, {"skill_id": art.id, "version": art.version, "dependencies": []})
    if not ok:
        # The installer's "Error: …" branch carries raw exception text (host paths).
        raise _IntakeFailed("skill installer error" if message.startswith("Error") else f"skill installer refused: {message[:200]}")
    return "installed", f"{art.id}@{art.version}"


def clean_layer_manifest(manifest: dict) -> dict:
    """Drop another install's registry state; shared by export and import."""
    return {k: v for k, v in manifest.items() if k not in LAYER_STATE_KEYS and not k.startswith("_")}


def _check_imported_layer(manifest: dict) -> None:
    gates = manifest.get("quality_gates", [])
    rules = manifest.get("enforcement_rules", [])
    if not isinstance(gates, list) or not isinstance(rules, list):
        raise _IntakeFailed("layer quality_gates / enforcement_rules must be lists")
    if len(gates) > MAX_IMPORTED_GATES:
        raise _IntakeFailed(f"an imported layer may declare at most {MAX_IMPORTED_GATES} quality gates")
    for g in gates:
        if not isinstance(g, dict) or not _GATE_ID_RE.match(str(g.get("gate_id", ""))):
            raise _IntakeFailed("layer gate ids must be short identifiers")
        if not _TEST_FILE_RE.match(str(g.get("test_path", ""))) or ".." in str(g.get("test_path")):
            raise _IntakeFailed("an imported layer's gate must name one test file under tests/, not a directory")
    for r in rules:
        if not isinstance(r, dict) or not _GATE_ID_RE.match(str(r.get("rule_id", ""))):
            raise _IntakeFailed("layer rule ids must be short identifiers")
    # Every value Layer Forge writes into the audit chain is an identifier (R2B-8).
    targets = manifest.get("targets", [])
    if not isinstance(targets, list) or not all(
            isinstance(t, dict) and _GATE_ID_RE.match(str(t.get("layer_id", ""))) for t in targets):
        raise _IntakeFailed("layer target ids must be short identifiers")
    deps = manifest.get("dependencies", [])
    if not isinstance(deps, list) or not all(
            isinstance(d, dict) and _GATE_ID_RE.match(str(d.get("id", ""))) for d in deps):
        raise _IntakeFailed("layer dependency ids must be short identifiers")


def _intake_layer(art: ArtifactEntry, env: BundleEnvelope, zf: zipfile.ZipFile, tenant_id: str) -> tuple[str, str]:
    from core.orchestration.layer_forge.orchestrator import LayerForgeOrchestrator

    files = _files(art, zf)
    if set(files) != {"manifest.json"}:
        raise _IntakeFailed("layer payload must be exactly manifest.json")
    try:
        manifest = json.loads(files["manifest.json"])
    except (ValueError, RecursionError):
        raise _IntakeFailed("layer manifest.json is not usable JSON") from None
    if not isinstance(manifest, dict):
        raise _IntakeFailed("layer manifest.json must be an object")
    if manifest.get("id") != art.id or manifest.get("version") != art.version:
        raise _IntakeFailed("layer manifest id/version differ from the envelope")
    manifest = clean_layer_manifest(manifest)
    _check_imported_layer(manifest)
    orch = LayerForgeOrchestrator(tenant_id, actor="bundle_import")
    result = orch.create_layer_definition(manifest)
    if result.status != "SUCCESS":
        if result.phase == "audit" and _stored_layer_matches(orch, manifest):
            # Written, then Layer Forge's own record failed: report what is on disk (R2B-1).
            return "forged", f"{art.id}@{art.version} (Layer Forge could not record every step)"
        raise _IntakeFailed(f"layer forge refused at the {result.phase} phase")
    detail = result.registry_key or f"{art.id}@{art.version}"
    if result.review_verdict is not None and result.review_verdict.status == "FLAGGED":
        detail += " (this install's review flagged it; promoting needs an override)"
    return "forged", detail


def _stored_layer_matches(orch, manifest: dict) -> bool:
    """The registry holds THIS bundle's definition (not one a concurrent writer put there)."""
    from core.orchestration.layer_forge.registry import LayerNotFoundError

    try:
        stored = orch.registry.get(manifest["id"], manifest["version"])
    except LayerNotFoundError:
        return False
    return clean_layer_manifest(stored) == manifest


def _intake_plugin(art: ArtifactEntry, env: BundleEnvelope, zf: zipfile.ZipFile, tenant_id: str) -> tuple[str, str]:
    from core.plugins.staging import StagingManager

    _name, payload = _single(art, zf)
    manager = StagingManager(tenant_id)
    tmp_dir = Path(tempfile.mkdtemp(prefix=".bundle-", dir=manager.staging_root))
    try:
        pkg = tmp_dir / f"{art.id}-{art.version}.zip"
        with open(pkg, "xb") as fh:
            fh.write(payload)
        ok, manifest, _errors = manager.validate_zip_file(pkg)
        if not ok:
            raise _IntakeFailed("not a plugin package (it needs a manifest.json with name, version and author)")
        upload_id = manager.compute_file_hash(pkg)[:16]
        existing = manager.get_staged_upload(upload_id)
        if existing is not None:
            if existing.get("status") == "pending_approval":
                return "pending_approval", upload_id  # already waiting; not re-staged
            raise _IntakeFailed("this exact package was staged before and has already been decided")
        manager.store_staged_upload(upload_id, pkg, manifest)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return "pending_approval", upload_id


def _intake_tool(art: ArtifactEntry, env: BundleEnvelope, zf: zipfile.ZipFile, tenant_id: str) -> tuple[str, str]:
    from .tool_quarantine import QuarantineError, ToolQuarantine

    files = _files(art, zf)
    if "spec.json" not in files or len(files) != 2:
        raise _IntakeFailed("tool payload must be spec.json plus exactly one implementation file")
    try:
        spec = json.loads(files.pop("spec.json"))
    except (ValueError, RecursionError):
        raise _IntakeFailed("tool spec.json is not usable JSON") from None
    if not isinstance(spec, dict):
        raise _IntakeFailed("tool spec.json must be an object")
    (_impl_name, impl_bytes), = files.items()
    try:
        entry, _created = ToolQuarantine(tenant_id).stage(
            tool_id=art.id, version=art.version, bundle_id=env.id, bundle_version=env.version,
            spec=spec, impl_bytes=impl_bytes)
    except QuarantineError as exc:
        raise _IntakeFailed(str(exc)) from None
    return "quarantined", entry.quarantine_id


_INTAKES = {
    "skill": _intake_skill,
    "layer": _intake_layer,
    "plugin": _intake_plugin,
    "tool": _intake_tool,
}
