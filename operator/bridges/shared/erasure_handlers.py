"""Per-layer ErasureHandler implementations (Layer 36 follow-up).

Replaces the StubHandler chain shipped in M4 (ADR-0045) with real
purge logic per layer. Each handler interprets the L36 ``subject_id``
as its layer-native identifier:

* **L28 recall** — ``subject_id`` = ``chat_key`` (the recall.db key).
* **L33 artifacts** — ``subject_id`` = ``session_key`` (typically
  ``"<bridge>:<chat_key>"``); unpinned session artifacts are purged,
  pinned artifacts left in place pending operator ACK.
* **L7 skill-forge** — ``subject_id`` interpreted by the operator's
  identity-mapping (subject → workspace path). Stub for now;
  full implementation pending a documented user-scope-skill manifest.
* **L24 data-snapshot** — ``subject_id`` interpreted by the data-policy
  ``identity_field``; stub for now pending policy-schema extension.

Operators wire real handlers into the orchestrator via
``corvin_erasure.register_handler()`` (M4.5) so the CLI + console
route (M4.6) pick them up automatically.

Subject_id resolution policy:

  The handlers here treat ``subject_id`` as the layer-native key.
  When a deployment has a separate ``subject_id → chat_key`` mapping
  (e.g. via an operator-owned identity store), the operator builds
  a *bespoke* L16IdentityMappingHandler that converts subject_id to
  the layer-native form and re-registers each downstream handler
  with the resolved key. The DSB-checklist § 2.5 documents this.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from erasure_orchestrator import (
    ErasureLayerResult,
    LayerStatus,
    ReasonCode,
)


# ── path resolution helpers ──────────────────────────────────────────


def _corvin_home() -> Path:
    env = os.environ.get("CORVIN_HOME") or os.environ.get("CORVIN_HOME")
    if env:
        return Path(env).expanduser()
    new = Path.home() / ".corvin"
    legacy = Path.home() / ".corvinOS"
    if new.is_dir():
        return new
    if legacy.is_dir():
        return legacy
    return new


def _tenant_global(tenant_id: str = "_default") -> Path:
    home = _corvin_home()
    new = home / "tenants" / tenant_id / "global"
    if new.is_dir() or not (home / "global").is_dir():
        return new
    return home / "global"  # legacy single-tenant layout


def _tenant_sessions(tenant_id: str = "_default") -> Path:
    home = _corvin_home()
    new = home / "tenants" / tenant_id / "sessions"
    if new.is_dir() or not (home / "sessions").is_dir():
        return new
    return home / "sessions"  # legacy single-tenant layout


def _tenant_workflow_runs(tenant_id: str = "_default") -> Path:
    """Resolve ``<tenant>/workflow_runs`` (ADR-0188 M5 paused-run checkpoints).

    Mirrors ``corvinOS.shared.paths.tenant_workflow_runs_dir`` (the resolver
    ``checkpoint.py`` itself uses) without importing across the repo-root
    boundary — same pattern as ``_tenant_global`` / ``_tenant_sessions``.
    """
    home = _corvin_home()
    new = home / "tenants" / tenant_id / "workflow_runs"
    if new.is_dir() or not (home / "workflow_runs").is_dir():
        return new
    return home / "workflow_runs"  # legacy single-tenant layout


# ── L28 recall handler ──────────────────────────────────────────────


@dataclass
class L28RecallHandler:
    """DELETE FROM turns WHERE chat_key = subject_id.

    The L28 recall.db indexes conversation turns by ``chat_key``;
    there is no separate user_id column. Operators with multiple
    pseudonymous identities per chat_key need to pre-resolve
    via an identity-mapping handler.

    Returns APPLIED when at least one row was deleted, SKIPPED when
    the chat_key wasn't in the table. Raises sqlite3.OperationalError
    on database failures (orchestrator captures + marks FAILED).
    """
    layer_id: str = "L28-recall"
    db_path: Path | None = None
    tenant_id: str = "_default"

    def _resolve_db(self) -> Path:
        if self.db_path is not None:
            return self.db_path
        return _tenant_global(self.tenant_id) / "memory" / "recall.db"

    def purge(self, subject_id: str, request_id: str) -> ErasureLayerResult:
        t0 = time.time()
        db = self._resolve_db()
        if not db.exists():
            return ErasureLayerResult(
                layer_id=self.layer_id,
                status=LayerStatus.SKIPPED,
                count=0,
                reason=f"recall.db not present at {db}",
                code=ReasonCode.STORE_ABSENT.value,
                duration_ms=int((time.time() - t0) * 1000),
            )

        with sqlite3.connect(str(db)) as conn:
            # Two DELETEs: turns_fts is automatically updated by the
            # delete trigger declared in conversation_recall.py.
            cur = conn.execute(
                "DELETE FROM turns WHERE chat_key = ?",
                (subject_id,),
            )
            n = cur.rowcount
            conn.commit()

        # Emit per-layer audit confirmation (GDPR Art. 17 / Art. 30 evidence).
        # subject_id is NEVER logged — only the count and layer_id.
        # Best-effort: a failed audit write must not block the erasure result.
        try:
            _forge_root = Path(__file__).resolve().parent.parent.parent / "forge"
            import sys as _sys
            if str(_forge_root) not in _sys.path:
                _sys.path.insert(0, str(_forge_root))
            from forge.security_events import write_event as _w  # type: ignore
            _audit_path = _tenant_global(self.tenant_id) / "forge" / "audit.jsonl"
            _w(
                _audit_path,
                "memory.recall_purged",
                severity="WARNING",
                details={
                    "layer": "L28.1",
                    "count": max(n, 0),
                    "status": "applied" if n > 0 else "skipped",
                },
            )
        except Exception:  # noqa: BLE001
            pass  # audit is best-effort; never block erasure

        return ErasureLayerResult(
            layer_id=self.layer_id,
            status=LayerStatus.APPLIED if n > 0 else LayerStatus.SKIPPED,
            count=max(n, 0),
            reason=(f"deleted {n} turn(s) for chat_key={subject_id!r}"
                    if n > 0 else
                    f"no turns matched chat_key={subject_id!r}"),
            code=(ReasonCode.DELETED.value if n > 0
                  else ReasonCode.STORE_EMPTY.value),
            duration_ms=int((time.time() - t0) * 1000),
        )


# ── L28.2 user-model handler ────────────────────────────────────────


@dataclass
class L28UserModelHandler:
    """Delete the user-model JSON file for each (channel, chat_key) pair
    that resolves from ``subject_id``.

    ``subject_id`` is treated as the bare ``chat_key`` (the same key used
    by ``L28RecallHandler``).  The handler walks every JSON file under
    ``<tenant>/global/memory/user_model/`` whose filename encodes a
    chat_key matching the subject.

    Returns APPLIED when at least one file was deleted, SKIPPED when no
    files matched, FAILED on an unhandled exception.

    Audit event: ``erasure.user_model_deleted`` (layer: L28.2) with
    ``subject_id`` and ``files_deleted`` count — no file paths, no model
    content.
    """
    layer_id: str = "L28.2-user-model"
    tenant_id: str = "_default"
    _audit_fn: Any = field(default=None, repr=False)

    def _user_model_dir(self) -> Path:
        return _tenant_global(self.tenant_id) / "memory" / "user_model"

    def _emit(self, subject_id: str, files_deleted: int) -> None:
        """Best-effort audit emit — never raises."""
        try:
            import sys as _sys
            import importlib as _il
            _shared = Path(__file__).resolve().parent
            if str(_shared) not in _sys.path:
                _sys.path.insert(0, str(_shared))
            _eo = _il.import_module("erasure_orchestrator")
            _audit = getattr(_eo, "_audit_writer", None)
            if _audit is None:
                # Try forge security_events directly
                _forge = Path(__file__).resolve().parent.parent.parent / "forge"
                if str(_forge) not in _sys.path:
                    _sys.path.insert(0, str(_forge))
                from forge.security_events import write_event as _w  # type: ignore
                _audit_path = _tenant_global(self.tenant_id) / "forge" / "audit.jsonl"
                _w(_audit_path, "erasure.user_model_deleted",
                   details={"subject_id": subject_id, "files_deleted": files_deleted,
                            "layer": "L28.2"})
                return
            # If orchestrator has a writer injected use it; otherwise silent
        except Exception:  # noqa: BLE001
            pass

    def purge(self, subject_id: str, request_id: str) -> ErasureLayerResult:
        t0 = time.time()
        try:
            from user_model import forget as _um_forget  # local import avoids circular at module load  # noqa: E402
        except ImportError:
            try:
                import importlib as _il
                _um_forget = _il.import_module("user_model").forget
            except Exception:
                return ErasureLayerResult(
                    layer_id=self.layer_id,
                    status=LayerStatus.FAILED,
                    count=0,
                    reason="user_model module not importable",
                    code=ReasonCode.STORE_ERROR.value,
                    duration_ms=int((time.time() - t0) * 1000),
                )

        udir = self._user_model_dir()
        if not udir.is_dir():
            return ErasureLayerResult(
                layer_id=self.layer_id,
                status=LayerStatus.SKIPPED,
                count=0,
                reason=f"user_model dir not present at {udir}",
                code=ReasonCode.STORE_ABSENT.value,
                duration_ms=int((time.time() - t0) * 1000),
            )

        # Walk every <channel>__<chat_key>.json; match on the chat_key component.
        # Filename encoding: _safe_token(channel) + "__" + _safe_token(chat_key)
        # subject_id = the raw chat_key (before safe-token); we compare both
        # the raw value and the safe-tokenised form.
        import re as _re
        _SAFE_RE = _re.compile(r"[^A-Za-z0-9._-]+")
        safe_subject = _SAFE_RE.sub("_", str(subject_id))[:128] or "_"

        files_deleted = 0
        try:
            for json_path in udir.glob("*.json"):
                stem = json_path.stem  # e.g. "discord__1234567890"
                if "__" not in stem:
                    continue
                # chat_key is everything after the last "__"
                _, chat_key_tok = stem.rsplit("__", 1)
                if chat_key_tok == safe_subject:
                    # Reconstruct channel from prefix
                    channel_tok = stem[: stem.rfind("__")]
                    # Call forget() to get audit + unlink
                    try:
                        _um_forget(channel_tok, subject_id,
                                   tenant_id=self.tenant_id)
                    except Exception:
                        # Fallback: direct unlink
                        try:
                            json_path.unlink(missing_ok=True)
                        except OSError:
                            pass
                    files_deleted += 1
        except Exception as exc:
            return ErasureLayerResult(
                layer_id=self.layer_id,
                status=LayerStatus.FAILED,
                count=files_deleted,
                reason=f"user_model purge error: {type(exc).__name__}: {str(exc)[:200]}",
                code=ReasonCode.STORE_ERROR.value,
                duration_ms=int((time.time() - t0) * 1000),
            )

        self._emit(subject_id, files_deleted)

        return ErasureLayerResult(
            layer_id=self.layer_id,
            status=LayerStatus.APPLIED if files_deleted > 0 else LayerStatus.SKIPPED,
            count=files_deleted,
            reason=(f"deleted {files_deleted} user-model file(s) for subject"
                    if files_deleted > 0
                    else f"no user-model files matched subject"),
            code=(ReasonCode.DELETED.value if files_deleted > 0
                  else ReasonCode.STORE_EMPTY.value),
            duration_ms=int((time.time() - t0) * 1000),
        )


# ── L33 artifact handler ────────────────────────────────────────────


@dataclass
class L33ArtifactHandler:
    """Purge unpinned session artifacts for ``session_key=subject_id``.

    Session artifacts live under ``<tenant>/sessions/<session_key>/
    artifacts/``. Pinned artifacts (promoted to ``<global>/artifacts/``)
    are NOT touched — they require operator ACK because pin == "I
    deliberately wanted to preserve this".

    Returns:
      APPLIED  if any files were removed.
      SKIPPED  if the session dir doesn't exist OR was empty.
      FAILED   only on disk errors (orchestrator captures the raise).

    For the pinned-acknowledgement workflow, the operator runs
    ``corvin-erasure run <subject_id> --notes "pinned ack ok"`` and
    then deletes the pinned set manually via the console route;
    automating the pinned purge requires a separate ACK flow we
    deliberately leave out here.
    """
    layer_id: str = "L33-artifacts"
    tenant_id: str = "_default"

    def purge(self, subject_id: str, request_id: str) -> ErasureLayerResult:
        t0 = time.time()
        session_dir = _tenant_sessions(self.tenant_id) / subject_id / "artifacts"
        if not session_dir.is_dir():
            return ErasureLayerResult(
                layer_id=self.layer_id,
                status=LayerStatus.SKIPPED,
                count=0,
                reason=f"no session artifacts at {session_dir}",
                code=ReasonCode.STORE_ABSENT.value,
                duration_ms=int((time.time() - t0) * 1000),
            )

        # Count + remove every file under the artifacts/ tree, then
        # remove the empty directories. The .manifest.jsonl + .manifest.lock
        # files are part of the artifact set and disappear with them.
        n_files = 0
        n_bytes = 0
        for path in session_dir.rglob("*"):
            if path.is_file():
                try:
                    n_bytes += path.stat().st_size
                except OSError:
                    pass
                path.unlink(missing_ok=True)
                n_files += 1
        # Remove empty directories bottom-up
        for path in sorted(session_dir.rglob("*"), reverse=True):
            if path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass
        # Top-level artifacts/ dir itself
        try:
            session_dir.rmdir()
        except OSError:
            pass

        if n_files == 0:
            return ErasureLayerResult(
                layer_id=self.layer_id,
                status=LayerStatus.SKIPPED,
                count=0,
                reason="session artifacts dir was empty",
                code=ReasonCode.STORE_EMPTY.value,
                duration_ms=int((time.time() - t0) * 1000),
            )

        return ErasureLayerResult(
            layer_id=self.layer_id,
            status=LayerStatus.APPLIED,
            count=n_files,
            reason=f"removed {n_files} file(s) ({n_bytes} bytes); "
                   f"pinned artifacts under global/ untouched",
            code=ReasonCode.DELETED.value,
            duration_ms=int((time.time() - t0) * 1000),
        )


# ── ACS / WDAT trace handler (ADR-0104/0109 + ADR-0127) ─────────────


@dataclass
class ACSTraceHandler:
    """Purge ACS run state + WDAT worker traces for ``session_key=subject_id``.

    GDPR Art. 17 + Art. 5 gap closure: ACS workers persist their instruction
    and output text (up to 8 KB each) to ``<tenant>/sessions/<session_key>/
    acs/runs/<run_id>/traces/<wid>.json[.enc]``, plus run state / manager
    decisions / datasource-context. With no ``CORVIN_WDAT_KEY`` these land in
    PLAINTEXT (mode 0600). They are real per-subject content and were not
    covered by any ErasureHandler — they survived ``corvin-erasure``. This
    handler deletes the entire ``acs/`` subtree for the subject's session.

    Returns APPLIED if anything was removed, SKIPPED if absent/empty.
    """
    layer_id: str = "ACS-traces"
    tenant_id: str = "_default"

    def purge(self, subject_id: str, request_id: str) -> ErasureLayerResult:
        t0 = time.time()
        acs_dir = _tenant_sessions(self.tenant_id) / subject_id / "acs"
        if not acs_dir.is_dir():
            return ErasureLayerResult(
                layer_id=self.layer_id, status=LayerStatus.SKIPPED, count=0,
                reason=f"no ACS traces at {acs_dir}",
                code=ReasonCode.STORE_ABSENT.value,
                duration_ms=int((time.time() - t0) * 1000),
            )
        n_files = 0
        n_bytes = 0
        for path in acs_dir.rglob("*"):
            if path.is_file():
                try:
                    n_bytes += path.stat().st_size
                except OSError:
                    pass
                path.unlink(missing_ok=True)
                n_files += 1
        for path in sorted(acs_dir.rglob("*"), reverse=True):
            if path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass
        try:
            acs_dir.rmdir()
        except OSError:
            pass
        if n_files == 0:
            return ErasureLayerResult(
                layer_id=self.layer_id, status=LayerStatus.SKIPPED, count=0,
                reason="ACS trace dir was empty",
                code=ReasonCode.STORE_EMPTY.value,
                duration_ms=int((time.time() - t0) * 1000),
            )
        return ErasureLayerResult(
            layer_id=self.layer_id, status=LayerStatus.APPLIED, count=n_files,
            reason=f"removed {n_files} ACS trace/run file(s) ({n_bytes} bytes)",
            code=ReasonCode.DELETED.value,
            duration_ms=int((time.time() - t0) * 1000),
        )


# ── L7 skill-forge handler (stub) ───────────────────────────────────


@dataclass
class L7SkillForgeHandler:
    """L7 skill-forge erasure.

    User-scope skills under ``<corvin_home>/<scope>/skill-forge/``
    are persona-aware. A full purge needs:

      1. Map subject_id → user-scope workspace path via the operator's
         identity mapping (the identity-mapping handler's job).
      2. Walk the workspace, remove every skill file, prune the
         slot-mirror copy under ``operator/skill-forge/skills/dyn/``.
      3. Emit the per-skill ``skill.removed`` audit event for each.

    This default implementation reports SKIPPED with a clear reason —
    operators ship a real handler in the L7 follow-up commit.
    """
    layer_id: str = "L7-skill-forge"
    tenant_id: str = "_default"
    reason: str = (
        "L7 user-scope skill purge not yet implemented. "
        "Operators with custom user-scope skills should ship a real handler "
        "that maps subject_id to the user-scope workspace path."
    )

    def purge(self, subject_id: str, request_id: str) -> ErasureLayerResult:
        return ErasureLayerResult(
            layer_id=self.layer_id,
            status=LayerStatus.SKIPPED,
            count=0,
            reason=self.reason,
            code=ReasonCode.NOT_APPLICABLE.value,
        )


# ── L24 data-snapshot handler (stub) ────────────────────────────────


@dataclass
class L24DataSnapshotHandler:
    """L24 large-data snapshot erasure.

    Data snapshots under ``<tenant>/global/data/`` carry metadata that
    *may* reference the subject. A full purge needs:

      1. Walk every snapshot manifest under ``data/``.
      2. Match snapshot metadata against the subject (operator-chosen
         match field — typically ``chat_key`` or a custom subject_id
         column in ``data_policy.yaml``).
      3. Unregister + remove matched snapshots.

    Snapshots are PII-redacted at creation per the L24 design, so the
    primary leak vector is the metadata table. Default handler reports
    SKIPPED with a documented reason; real handler ships with the L24
    follow-up commit.
    """
    layer_id: str = "L24-data-snapshot"
    tenant_id: str = "_default"
    reason: str = (
        "L24 snapshot purge not yet implemented. "
        "Operators with data-policy.yaml that carries subject identifiers "
        "should ship a real handler keyed off the configured identity_field."
    )

    def purge(self, subject_id: str, request_id: str) -> ErasureLayerResult:
        return ErasureLayerResult(
            layer_id=self.layer_id,
            status=LayerStatus.SKIPPED,
            count=0,
            reason=self.reason,
            code=ReasonCode.NOT_APPLICABLE.value,
        )


# ── Identity-mapping handler base class ──────────────────────────────


@dataclass
class IdentityMappingHandlerBase:
    """Base class for the operator's L16-identity-mapping handler.

    The corvin core does not own the subject_id → real-identity
    mapping — operators store it in their own DB / IAM / LDAP /
    spreadsheet. To plug it into L36, operators subclass this and
    implement ``purge``.

    The base class is registered by the orchestrator by default to
    record a visible TODO; operators override by registering their
    concrete handler via ``corvin_erasure.register_handler()``
    BEFORE the orchestrator picks it up.
    """
    layer_id: str = "L16-identity-mapping"
    reason: str = (
        "no concrete identity-mapping handler registered — audit chain "
        "pseudonyms cannot be unwound without this. Operators must "
        "subclass IdentityMappingHandlerBase and register the concrete "
        "handler via corvin_erasure.register_handler() before "
        "running corvin-erasure."
    )

    def purge(self, subject_id: str, request_id: str) -> ErasureLayerResult:
        return ErasureLayerResult(
            layer_id=self.layer_id,
            status=LayerStatus.SKIPPED,
            count=0,
            reason=self.reason,
            code=ReasonCode.NOT_APPLICABLE.value,
        )


# ── L39 social participation handler ────────────────────────────────


@dataclass
class L39SocialParticipationHandler:
    """GDPR Art. 17 erasure for L39 CorvinFed social data.

    Deletes ``posts.db`` (own + received posts), ``registry.db``
    (social graph), ``actor_keypair.json`` (Ed25519 private key),
    and ``actor.json`` (public actor document) for the given tenant.

    The ``subject_id`` is interpreted as the tenant_id. If the subject
    is leaving the federation, pass ``social.participation`` as the
    data class; the handler purges the entire social dir.

    Consent state (``consent.json``) is NOT deleted here — the
    ``social_consent.leave()`` call owns that before triggering erasure.
    """
    layer_id: str = "L39-social"
    tenant_id: str = "_default"

    def _social_dir(self) -> "Path":
        return _tenant_global(self.tenant_id) / "social"

    def purge(self, subject_id: str, request_id: str) -> "ErasureLayerResult":
        t0 = time.time()
        social_dir = self._social_dir()

        if not social_dir.is_dir():
            return ErasureLayerResult(
                layer_id=self.layer_id,
                status=LayerStatus.SKIPPED,
                count=0,
                reason="social dir not present",
                code=ReasonCode.STORE_ABSENT.value,
                duration_ms=int((time.time() - t0) * 1000),
            )

        targets = [
            "posts.db",
            "registry.db",
            "actor_keypair.json",
            "actor.json",
        ]
        removed = 0
        for name in targets:
            p = social_dir / name
            if p.exists():
                p.unlink()
                removed += 1

        # Remove empty social dir itself
        try:
            social_dir.rmdir()
        except OSError:
            pass

        return ErasureLayerResult(
            layer_id=self.layer_id,
            status=LayerStatus.APPLIED if removed > 0 else LayerStatus.SKIPPED,
            count=removed,
            reason=f"removed {removed} social artifact(s) for tenant {self.tenant_id!r}",
            code=(ReasonCode.DELETED.value if removed > 0
                  else ReasonCode.STORE_EMPTY.value),
            duration_ms=int((time.time() - t0) * 1000),
        )


# ── L41 Grant handler ─────────────────────────────────────────────────


@dataclass
class L41GrantHandler:
    """GDPR Art. 17 erasure for L41 Social Capability Grants.

    On erasure of ``subject_id``:
    - Delete all grants where ``grantee_actor`` maps to the subject.
    - Delete all grants where ``grantor_actor`` maps to the subject
      (if the subject is the local tenant, this wipes the full grant store).

    ``subject_id`` is treated as the actor_id (e.g. ``@alice@other.instance``).
    For local-tenant erasure the caller must pass the tenant's actor_id.

    Grant audit events are NOT deleted — pseudonymisation via ``grant_id``
    is the ADR-0054 Art. 17 mechanism, consistent with L16 tamper-evidence.
    """

    layer_id: str = "L41-grants"
    tenant_id: str = "_default"

    def _grant_db_path(self) -> "Path":
        return _tenant_global(self.tenant_id) / "grants" / "grants.db"

    def purge(self, subject_id: str, request_id: str) -> "ErasureLayerResult":
        from grant_store import GrantStore  # local import avoids circular at module load

        t0 = time.time()
        db_path = self._grant_db_path()

        if not db_path.exists():
            return ErasureLayerResult(
                layer_id=self.layer_id,
                status=LayerStatus.SKIPPED,
                count=0,
                reason="grant store not present",
                code=ReasonCode.STORE_ABSENT.value,
                duration_ms=int((time.time() - t0) * 1000),
            )

        try:
            store = GrantStore(db_path)
            removed = store.purge_for_actor(subject_id)
        except Exception as exc:
            return ErasureLayerResult(
                layer_id=self.layer_id,
                status=LayerStatus.FAILED,
                count=0,
                reason=f"grant store purge failed: {type(exc).__name__}",
                code=ReasonCode.STORE_ERROR.value,
                duration_ms=int((time.time() - t0) * 1000),
            )

        return ErasureLayerResult(
            layer_id=self.layer_id,
            status=LayerStatus.APPLIED if removed > 0 else LayerStatus.SKIPPED,
            count=removed,
            reason=f"removed {removed} grant(s) for subject",
            code=(ReasonCode.DELETED.value if removed > 0
                  else ReasonCode.STORE_EMPTY.value),
            duration_ms=int((time.time() - t0) * 1000),
        )


# ── L42 Org handler ──────────────────────────────────────────────────


@dataclass
class L42OrgHandler:
    """GDPR Art. 17 erasure for L42 CorvinOrg organisation data.

    On erasure of ``subject_id`` (treated as actor_id):

    * **As grantee / member**: remove the subject from all local orgs' member
      lists; revoke all endorsements where agent_actor_id == subject.
    * **As org itself** (subject_id == org handle or org actor_id): dissolve the
      entire org directory (hard delete). Emits ``org.dissolved`` audit event
      BEFORE rmtree (audit-first invariant, ADR-0055 §compliance).

    Org audit events are NOT deleted — pseudonymisation via ``org_handle``
    prefixes is the Art. 17 mechanism, consistent with L16 tamper-evidence.
    """

    layer_id: str = "L42-org"
    tenant_id: str = "_default"

    def purge(self, subject_id: str, request_id: str) -> "ErasureLayerResult":
        from org_store import OrgStore, list_org_handles, org_dir  # local import
        from audit import audit_event  # local import

        t0 = time.time()
        try:
            handles = list_org_handles(self.tenant_id)
        except Exception:
            handles = []

        if not handles:
            return ErasureLayerResult(
                layer_id=self.layer_id,
                status=LayerStatus.SKIPPED,
                count=0,
                reason="no orgs present for tenant",
                code=ReasonCode.STORE_ABSENT.value,
                duration_ms=int((time.time() - t0) * 1000),
            )

        total = 0
        failed = False

        for handle in handles:
            try:
                store = OrgStore(handle, self.tenant_id)
                actor_doc = store.get_actor() if store.actor_exists() else {}
                org_actor_id = actor_doc.get("id", "")

                # Case 1: subject IS this org (actor_id match or handle match)
                if subject_id in (handle, org_actor_id):
                    audit_event(
                        "org.dissolved",
                        details={
                            "org_handle": handle,
                            "reason": "erasure_request",
                            "request_id_prefix": request_id[:16],
                        },
                    )
                    total += store.dissolve()
                    continue

                # Case 2: subject is a member of this org
                if store.remove_member(subject_id):
                    total += 1

                # Case 3: subject is an affiliated agent — revoke endorsements
                for end in store.list_endorsements(include_revoked=False):
                    if end.get("agent_actor_id") == subject_id:
                        audit_event(
                            "org.agent_deaffiliated",
                            details={
                                "org_handle": handle,
                                "agent_prefix": subject_id[:16],
                                "endorsement_id": end.get("endorsement_id", ""),
                            },
                        )
                        store.revoke_endorsement(end["endorsement_id"])
                        total += 1

            except Exception:
                failed = True

        status = (
            LayerStatus.FAILED
            if failed and total == 0
            else LayerStatus.APPLIED
            if total > 0
            else LayerStatus.SKIPPED
        )
        code = (
            ReasonCode.STORE_ERROR.value
            if status == LayerStatus.FAILED
            else ReasonCode.DELETED.value
            if status == LayerStatus.APPLIED
            else ReasonCode.STORE_EMPTY.value
        )
        return ErasureLayerResult(
            layer_id=self.layer_id,
            status=status,
            count=total,
            reason=f"org erasure: {total} record(s) removed across {len(handles)} org(s)",
            code=code,
            duration_ms=int((time.time() - t0) * 1000),
        )


# ── workflow-checkpoint handler (ADR-0188 M5) ───────────────────────


@dataclass
class WorkflowCheckpointHandler:
    """GDPR Art. 17 erasure for paused Task-Engine workflow checkpoints.

    A human-in-the-loop workflow run (``ask_human``/``answer`` under
    ``orchestration.engine: chat``) is checkpointed to
    ``<tenant>/workflow_runs/<run_id>.json`` (see
    ``core/workflows/corvin_workflows/checkpoint.py::save()``) while it
    waits for a reply. That JSON file stores the raw ``chat_id`` /
    ``approver`` plus the *entire* ``inputs``/``state`` dict verbatim —
    arbitrary user-submitted content (e.g. an expense amount, an IT-ticket
    body). There is no TTL; the only prior removal path was the run
    reaching a terminal state (``checkpoint.delete()``, called by the
    runner on completion). A paused run can sit indefinitely, so without
    this handler an erasure request would silently miss it.

    ``subject_id`` is treated as the raw ``chat_id``/``approver`` string
    the bridge passed into ``checkpoint.save()`` — the same identifier
    ``L28RecallHandler`` matches against ``chat_key``.

    Matches and deletes both the canonical ``<run_id>.json`` checkpoint
    and any ``<run_id>.json.claimed`` sidecar left by an in-flight
    ``checkpoint.claim()`` (a resume racing the erasure request).
    """
    layer_id: str = "L-workflow-checkpoints"
    tenant_id: str = "_default"

    def _runs_dir(self) -> Path:
        return _tenant_workflow_runs(self.tenant_id)

    def purge(self, subject_id: str, request_id: str) -> ErasureLayerResult:
        t0 = time.time()
        runs_dir = self._runs_dir()
        if not runs_dir.is_dir():
            return ErasureLayerResult(
                layer_id=self.layer_id,
                status=LayerStatus.SKIPPED,
                count=0,
                reason=f"no workflow_runs dir at {runs_dir}",
                code=ReasonCode.STORE_ABSENT.value,
                duration_ms=int((time.time() - t0) * 1000),
            )

        removed = 0
        try:
            candidates = list(runs_dir.glob("*.json")) + list(
                runs_dir.glob("*.json.claimed")
            )
            for checkpoint_path in candidates:
                try:
                    payload = json.loads(
                        checkpoint_path.read_text(encoding="utf-8")
                    )
                except (json.JSONDecodeError, OSError):
                    continue
                if (
                    payload.get("chat_id") == subject_id
                    or payload.get("approver") == subject_id
                ):
                    checkpoint_path.unlink(missing_ok=True)
                    removed += 1
        except Exception as exc:
            return ErasureLayerResult(
                layer_id=self.layer_id,
                status=LayerStatus.FAILED,
                count=removed,
                reason=(
                    f"workflow checkpoint purge error: "
                    f"{type(exc).__name__}: {str(exc)[:200]}"
                ),
                code=ReasonCode.STORE_ERROR.value,
                duration_ms=int((time.time() - t0) * 1000),
            )

        if removed == 0:
            return ErasureLayerResult(
                layer_id=self.layer_id,
                status=LayerStatus.SKIPPED,
                count=0,
                reason="no paused workflow checkpoints matched subject",
                code=ReasonCode.STORE_EMPTY.value,
                duration_ms=int((time.time() - t0) * 1000),
            )

        return ErasureLayerResult(
            layer_id=self.layer_id,
            status=LayerStatus.APPLIED,
            count=removed,
            reason=f"removed {removed} paused workflow checkpoint(s) for subject",
            code=ReasonCode.DELETED.value,
            duration_ms=int((time.time() - t0) * 1000),
        )


# ── default chain factory ────────────────────────────────────────────


def real_handler_chain(tenant_id: str = "_default") -> list:
    """Return the default chain of *real* per-layer handlers for
    ``corvin_erasure.register_handler``.

    Includes:
      * L28RecallHandler — fully implemented (SQL delete)
      * L28UserModelHandler — fully implemented (FS purge, ADR-0072 V-001)
      * L33ArtifactHandler — fully implemented (FS purge)
      * L39SocialParticipationHandler — fully implemented (FS purge)
      * L41GrantHandler — fully implemented (SQL delete, ADR-0054)
      * L42OrgHandler — fully implemented (FS purge, ADR-0055)
      * WorkflowCheckpointHandler — fully implemented (FS purge, ADR-0188 M5)
      * L7SkillForgeHandler — documented stub (operator overrides)
      * L24DataSnapshotHandler — documented stub (operator overrides)
      * IdentityMappingHandlerBase — documented stub (operator subclasses)
    """
    # ADR-0163: ULO objectives must be deleted on GDPR Art. 17 erasure.
    try:
        from ulo import ULOErasureHandler as _ULOErasureHandler  # type: ignore[import-not-found]
        _ulo_handler = _ULOErasureHandler(tenant_id=tenant_id)
    except ImportError:
        _ulo_handler = None

    chain = [
        L28RecallHandler(tenant_id=tenant_id),
        L28UserModelHandler(tenant_id=tenant_id),
        L33ArtifactHandler(tenant_id=tenant_id),
        ACSTraceHandler(tenant_id=tenant_id),
        L39SocialParticipationHandler(tenant_id=tenant_id),
        L41GrantHandler(tenant_id=tenant_id),
        L42OrgHandler(tenant_id=tenant_id),
        WorkflowCheckpointHandler(tenant_id=tenant_id),
        L7SkillForgeHandler(tenant_id=tenant_id),
        L24DataSnapshotHandler(tenant_id=tenant_id),
        IdentityMappingHandlerBase(),
    ]
    if _ulo_handler is not None:
        chain.append(_ulo_handler)
    return chain
