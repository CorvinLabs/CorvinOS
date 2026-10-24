"""D4 (PLAN-0948): the learning daemon's regeneration consumer does real, audited work.

``DataHubLearningDaemon`` decides THAT a Data Hub skill should be regenerated (its source changed, or
the operator's feedback about it is net negative); this module is what happens next:

1. find the artifact the skill was registered from (``meta.artifact_id``) in the tenant's artifact store,
2. re-read its source through the same ``DataHubSkill`` (size limit, parser, ADR-0661 security scan -
   a source that now contains a secret is REFUSED, not regenerated),
3. regenerate through the existing ``UnifiedCreator`` (a smarter generator is a later plan),
4. record the intent on the tenant audit chain FIRST (``learning.regeneration``, stage ``applying``;
   the write must commit or nothing is touched), apply it - new body into the artifact store and
   ``MultiSkillRegistry.update_body`` (grades and meta preserved) - and record the outcome.

Records are content-free: ids, an outcome label, a reason enum, a row count and a body hash prefix.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core.deployment import audit_sink

from .creator import UnifiedCreator
from .datahub import DataHubSkill, IngestionError
from .models import CreationRequest, CreationType, DataIngestion, DataSourceType
from .register import SCOPE, _registry, skill_name_for

logger = logging.getLogger(__name__)

audit_sink.register_events({
    "learning.regeneration": (
        "skill_id", "artifact_id", "stage", "outcome", "reason", "sample_size", "body_hash", "generation",
    ),
})

REASONS = ("source_changed", "negative_feedback_consensus", "manual")


@dataclass(frozen=True)
class SourceRef:
    source_id: str
    skill_id: str
    artifact_id: str
    path: str
    sha256: str


@dataclass(frozen=True)
class RegenOutcome:
    skill_id: str
    outcome: str          # completed | refused | failed | partial | no_artifact
    detail: str = ""
    generation: int = 0


def _artifacts_dir(tenant_id: str) -> Path:
    from core.paths.tenant import tenant_home

    return tenant_home(tenant_id) / "global" / "datahub_artifacts"


def _read(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _write(tenant_id: str, artifact: dict) -> None:
    path = _artifacts_dir(tenant_id) / f"{artifact['artifact_id']}.json"
    tmp = path.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(artifact, f, indent=2)
    os.replace(tmp, path)


class ArtifactStoreUnreadable(OSError):
    """The artifact store could not be listed or one of its records could not be read."""


def live_skill_artifacts(tenant_id: str) -> list[dict]:
    """Artifacts that are registered skills and still live (not deleted).

    RAISES :class:`ArtifactStoreUnreadable` when the directory cannot be listed or any record in it
    cannot be read: the host evicts the state of sources missing from this list, and "could not
    look" must never read as "everything was deleted". ``Path.glob`` swallowed both errors and
    returned a short list (review R2-DH-1), so the listing is ``os.scandir``, which raises.
    """
    out = []
    directory = _artifacts_dir(tenant_id)
    try:
        with os.scandir(directory) as it:
            names = sorted(e.name for e in it if e.name.endswith(".json"))
    except FileNotFoundError:
        return out
    except OSError as exc:
        raise ArtifactStoreUnreadable(f"artifact store unreadable: {type(exc).__name__}") from exc
    for name in names:
        art = _read(directory / name)
        if art is None:
            raise ArtifactStoreUnreadable(f"artifact record unreadable: {name}")
        if not (art.get("status") != "deleted" and art.get("skill_name") and art.get("source")):
            continue
        # A record may only speak for the skill its own id names: a planted/edited record naming
        # ANOTHER artifact's skill would have that skill rebuilt from its source (review R2-DH-7).
        try:
            if art["skill_name"] != skill_name_for(art.get("artifact_id", "")):
                continue
        except ValueError:
            continue
        out.append(art)
    return out


def sources(tenant_id: str) -> list[SourceRef]:
    """The data sources of every live Data Hub skill - what the daemon watches for change."""
    refs = []
    for art in live_skill_artifacts(tenant_id):
        for sid in art.get("data_sources") or []:
            refs.append(SourceRef(sid, art["skill_name"], art["artifact_id"],
                                  art["source"].get("path", ""), art["source"].get("sha256", "")))
    return refs


def safe_source_path(tenant_id: str, stored: str) -> Optional[Path]:
    """The stored source path if it may still be read for ``tenant_id``, else None.

    Create resolved and checked the path once; the file can be swapped for a symlink afterwards
    (review 2026-10-12, DH-1: a skill rebuilt from ANOTHER tenant's file). Re-check on every read:
    no symlink, still resolving to exactly the stored path, and not inside another tenant's home.
    """
    from core.paths.tenant import tenant_home

    if not stored:
        return None
    p = Path(stored)
    try:
        if p.is_symlink() or p.resolve() != p:
            return None
        own = tenant_home(tenant_id).resolve()
        home = own.parent.parent
        rp = p.resolve()
        if rp.is_relative_to(home) and not rp.is_relative_to(own):
            return None
    except (OSError, ValueError):
        return None
    return p


#: Largest source a regeneration copies (the ingestion layer has its own, lower limit).
MAX_SOURCE_BYTES = 512 * 1024 * 1024


def _snapshot_source(path: Path, scratch: Path) -> tuple[Path, str]:
    """Open the source ONCE (no symlink, regular file, one link), copy it into ``scratch`` and hash
    the same bytes. Ingestion and regeneration read the copy, so what is hashed is what is learned
    from - re-opening the path for each step let a swap in between feed one file to the hash and
    another to the skill (review R2-DH-3/4). A hard link to another tenant's file passes every
    path check, so a second link is refused.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(str(path), flags)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise PermissionError("source is not a regular file")
        if st.st_nlink != 1:
            raise PermissionError("source has more than one hard link")
        if st.st_size > MAX_SOURCE_BYTES:
            raise PermissionError("source too large")
        h = hashlib.sha256()
        dst = scratch / ("source" + path.suffix)
        out = os.open(str(dst), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(out, "wb") as w:
            while True:
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    break
                h.update(chunk)
                w.write(chunk)
        return dst, h.hexdigest()
    finally:
        os.close(fd)


def current_sha256(path: str, tenant_id: Optional[str] = None) -> str:
    """Content hash of a source; ``""`` when it cannot (or, for ``tenant_id``, may not) be read."""
    if tenant_id is not None and safe_source_path(tenant_id, path) is None:
        return ""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _emit(tenant_id: str, details: dict[str, Any]) -> None:
    audit_sink.emit("learning.regeneration", details, tenant_id=tenant_id)


def regenerate_skill(tenant_id: str, skill_id: str, *, reason: str = "manual") -> RegenOutcome:
    """Regenerate one Data Hub skill from its source. Never raises (a failed regeneration is an
    outcome with a record, not a crashed host). Every terminal outcome is recorded."""
    reason = reason if reason in REASONS else "manual"
    base = {"skill_id": skill_id, "reason": reason}
    try:
        art = next((a for a in live_skill_artifacts(tenant_id) if a.get("skill_name") == skill_id), None)
    except ArtifactStoreUnreadable:
        _try_record(tenant_id, {**base, "stage": "finished", "outcome": "failed"})
        return RegenOutcome(skill_id, "failed", "artifact store unreadable")
    if art is None:
        _try_record(tenant_id, {**base, "stage": "finished", "outcome": "no_artifact"})
        return RegenOutcome(skill_id, "no_artifact")
    base["artifact_id"] = art["artifact_id"]
    src = art["source"]
    generation = int(art.get("generation", 1)) + 1

    def _record(stage: str, outcome: str, **extra: Any) -> None:
        _emit(tenant_id, {**base, "stage": stage, "outcome": outcome, "generation": generation, **extra})

    def _finish(outcome: str, detail: str, **extra: Any) -> RegenOutcome:
        _try_record(tenant_id, {**base, "stage": "finished", "outcome": outcome,
                                "generation": generation, **extra})
        return RegenOutcome(skill_id, outcome, detail, generation if outcome == "completed" else 0)

    # The registered skill must say it was made from THIS artifact (review R2-DH-7).
    reg = _registry(tenant_id)
    spec = reg.get_in_scope(skill_id, SCOPE)
    if spec is None or (spec.meta or {}).get("artifact_id") != art["artifact_id"]:
        return _finish("refused", "skill was not registered from this artifact")

    # 2. re-read the source - re-checked: it may have been swapped since create
    path = safe_source_path(tenant_id, src.get("path", ""))
    if path is None:
        return _finish("refused", "source path is no longer a plain file of this tenant")
    with tempfile.TemporaryDirectory(prefix="datahub-regen-") as scratch:
        try:
            snap, snap_sha = _snapshot_source(path, Path(scratch))
        except PermissionError as exc:
            return _finish("refused", f"source: {exc}")
        except OSError as exc:
            return _finish("failed", f"source: {type(exc).__name__}")
        return _regenerate_from(tenant_id, skill_id, art, src, snap, snap_sha, generation,
                                reg, spec, _record, _finish)


def _regenerate_from(tenant_id, skill_id, art, src, snap, snap_sha, generation, reg, spec,
                     _record, _finish) -> RegenOutcome:
    hub = DataHubSkill()
    try:
        hub.ingest(DataIngestion(source_type=DataSourceType(src["type"]), source_path=str(snap),
                                 sample_rows=int(src.get("sample_rows", 100)), infer_schema=True))
    except (IngestionError, OSError, ValueError) as exc:
        return _finish("failed", f"source: {type(exc).__name__}")
    if hub.security.get("secret", 0) > 0:
        return _finish("refused", "source now contains a secret")
    rows = len(hub.ingested_data or [])
    if rows == 0:
        return _finish("refused", "source has no rows")

    # 3. regenerate - from the same snapshot that was hashed and scanned
    result = UnifiedCreator().create(CreationRequest(
        data=DataIngestion(source_type=DataSourceType(src["type"]), source_path=str(snap),
                           sample_rows=rows, infer_schema=False),
        creation_type=CreationType.SKILL, name=art["name"], description=art["description"],
        complexity=src.get("complexity", "medium"), fail_closed=True,
        profile=hub.profile(include_values=bool(src.get("include_values")))))
    if not result.success:
        return _finish("failed", "validation", sample_size=rows)
    body_hash = hashlib.sha256(result.artifact_body.encode("utf-8")).hexdigest()[:16]

    # 4. audit-first, then apply BOTH halves or neither
    try:
        _record("applying", "pending", sample_size=rows, body_hash=body_hash)
    except audit_sink.AuditWriteFailed:
        return RegenOutcome(skill_id, "failed", "audit chain did not commit; nothing applied")
    old_body = reg.get_body(skill_id)
    old_grades = list(spec.grades or [])
    try:
        reg.update_body(skill_id, scope=SCOPE, body_md=result.artifact_body, quota_exempt=True)
    except Exception as exc:  # noqa: BLE001
        # update_body is two writes (body, then grades): a failure can leave either half changed
        # (review R2-DH-5). Restore and say so, or report partial.
        restored = _restore(reg, skill_id, old_body, old_grades)
        return _finish("failed" if restored else "partial",
                       f"apply: {type(exc).__name__}; registry {'restored' if restored else 'NOT restored'}",
                       sample_size=rows, body_hash=body_hash)
    try:
        updated = dict(art)
        updated.update({
            "body": result.artifact_body, "row_count": rows, "generation": generation,
            "regenerated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "status": "completed", "validation_errors": result.validation_errors,
            "source": {**src, "sha256": snap_sha},
        })
        _write(tenant_id, updated)
    except Exception as exc:  # noqa: BLE001 - roll the registry back: the two halves agree
        restored = _restore(reg, skill_id, old_body, old_grades)
        return _finish("failed" if restored else "partial",
                       f"artifact write: {type(exc).__name__}; registry {'restored' if restored else 'NOT restored'}",
                       sample_size=rows, body_hash=body_hash)
    try:
        _record("finished", "completed", sample_size=rows, body_hash=body_hash)
    except audit_sink.AuditWriteFailed:
        logger.warning("regeneration of %s applied; completion record did not commit", skill_id)
    return RegenOutcome(skill_id, "completed", generation=generation)


def _restore(reg, skill_id: str, old_body: Optional[str], old_grades: list) -> bool:
    """Put body AND grades back; True only when both read back equal to what they were."""
    if old_body is None:
        return False
    want = _strip_front_matter(old_body)
    try:
        if _strip_front_matter(reg.get_body(skill_id) or "") != want:
            reg.update_body(skill_id, scope=SCOPE, body_md=want, quota_exempt=True)
        cur = reg.get_in_scope(skill_id, SCOPE)
        if cur is None:
            return False
        if list(cur.grades or []) != old_grades:
            reg._registry(SCOPE).set_grades(skill_id, old_grades)  # noqa: SLF001 - no public setter
            cur = reg.get_in_scope(skill_id, SCOPE)
        return (_strip_front_matter(reg.get_body(skill_id) or "") == want
                and list(cur.grades or []) == old_grades)
    except Exception:  # noqa: BLE001
        return False


def _try_record(tenant_id: str, details: dict[str, Any]) -> None:
    try:
        _emit(tenant_id, details)
    except audit_sink.AuditWriteFailed:
        logger.warning("learning.regeneration record did not commit (%s)", details.get("outcome"))


def _strip_front_matter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:].lstrip("\n")
    return text


__all__ = ["ArtifactStoreUnreadable", "SourceRef", "RegenOutcome", "sources", "regenerate_skill", "live_skill_artifacts",
           "current_sha256", "safe_source_path", "REASONS"]
