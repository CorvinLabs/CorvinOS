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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core.deployment import audit_sink

from .creator import UnifiedCreator
from .datahub import DataHubSkill, IngestionError
from .models import CreationRequest, CreationType, DataIngestion, DataSourceType
from .register import SCOPE, _registry

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
    outcome: str          # completed | refused | failed | no_artifact | skipped
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


def live_skill_artifacts(tenant_id: str) -> list[dict]:
    """Artifacts that are registered skills and still live (not deleted)."""
    out = []
    try:
        for path in sorted(_artifacts_dir(tenant_id).glob("*.json")):
            art = _read(path)
            if art and art.get("status") != "deleted" and art.get("skill_name") and art.get("source"):
                out.append(art)
    except OSError:
        pass
    return out


def sources(tenant_id: str) -> list[SourceRef]:
    """The data sources of every live Data Hub skill - what the daemon watches for change."""
    refs = []
    for art in live_skill_artifacts(tenant_id):
        for sid in art.get("data_sources") or []:
            refs.append(SourceRef(sid, art["skill_name"], art["artifact_id"],
                                  art["source"].get("path", ""), art["source"].get("sha256", "")))
    return refs


def current_sha256(path: str) -> str:
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
    outcome with a record, not a crashed host)."""
    reason = reason if reason in REASONS else "manual"
    art = next((a for a in live_skill_artifacts(tenant_id) if a.get("skill_name") == skill_id), None)
    base = {"skill_id": skill_id, "reason": reason}
    if art is None:
        return RegenOutcome(skill_id, "no_artifact")
    base["artifact_id"] = art["artifact_id"]
    src = art["source"]
    generation = int(art.get("generation", 1)) + 1

    def _record(stage: str, outcome: str, **extra: Any) -> None:
        _emit(tenant_id, {**base, "stage": stage, "outcome": outcome, "generation": generation, **extra})

    # 2. re-read the source
    hub = DataHubSkill()
    try:
        hub.ingest(DataIngestion(source_type=DataSourceType(src["type"]), source_path=src["path"],
                                 sample_rows=int(src.get("sample_rows", 100)), infer_schema=True))
    except (IngestionError, OSError, ValueError) as exc:
        try:
            _record("finished", "failed", reason=reason)
        except audit_sink.AuditWriteFailed:
            pass
        return RegenOutcome(skill_id, "failed", f"source: {type(exc).__name__}")
    if hub.security.get("secret", 0) > 0:
        try:
            _record("finished", "refused")
        except audit_sink.AuditWriteFailed:
            pass
        return RegenOutcome(skill_id, "refused", "source now contains a secret")
    rows = len(hub.ingested_data or [])
    if rows == 0:
        return RegenOutcome(skill_id, "refused", "source has no rows")

    # 3. regenerate
    result = UnifiedCreator().create(CreationRequest(
        data=DataIngestion(source_type=DataSourceType(src["type"]), source_path=src["path"],
                           sample_rows=rows, infer_schema=False),
        creation_type=CreationType.SKILL, name=art["name"], description=art["description"],
        complexity=src.get("complexity", "medium"), fail_closed=True))
    if not result.success:
        return RegenOutcome(skill_id, "failed", "validation")
    body_hash = hashlib.sha256(result.artifact_body.encode("utf-8")).hexdigest()[:16]

    # 4. audit-first, then apply
    try:
        _record("applying", "pending", sample_size=rows, body_hash=body_hash)
    except audit_sink.AuditWriteFailed:
        return RegenOutcome(skill_id, "failed", "audit chain did not commit; nothing applied")
    try:
        _registry(tenant_id).update_body(skill_id, scope=SCOPE, body_md=result.artifact_body,
                                           quota_exempt=True)
        art.update({
            "body": result.artifact_body, "row_count": rows, "generation": generation,
            "regenerated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "status": "completed", "validation_errors": result.validation_errors,
        })
        art["source"] = {**src, "sha256": current_sha256(src["path"])}
        _write(tenant_id, art)
    except Exception as exc:  # noqa: BLE001 - recorded, not raised
        try:
            _record("finished", "failed", sample_size=rows, body_hash=body_hash)
        except audit_sink.AuditWriteFailed:
            pass
        return RegenOutcome(skill_id, "failed", f"apply: {type(exc).__name__}", generation)
    try:
        _record("finished", "completed", sample_size=rows, body_hash=body_hash)
    except audit_sink.AuditWriteFailed:
        logger.warning("regeneration of %s applied; completion record did not commit", skill_id)
    return RegenOutcome(skill_id, "completed", generation=generation)


__all__ = ["SourceRef", "RegenOutcome", "sources", "regenerate_skill", "live_skill_artifacts",
           "current_sha256", "REASONS"]
