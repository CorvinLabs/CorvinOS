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

import hashlib
import json
import os
import re
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
                # Pseudonymity: the subject is recorded as a sha256 prefix,
                # never raw (same rule as memory.recall_purged).
                import hashlib as _hl
                _w(_audit_path, "erasure.user_model_deleted",
                   details={"uid_hash": _hl.sha256(str(subject_id).encode("utf-8")).hexdigest()[:16],
                            "files_deleted": files_deleted, "layer": "L28.2"})
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


# ── Web-chat handler (voice archive + turn log) ─────────────────────


@dataclass
class WebChatHandler:
    """Purge the web-chat session's voice archive and its turn log.

    ADR-0194 Phase 1 made the console keep the synthesised speech for every
    assistant reply at ``<tenant>/sessions/<session_key>/voice/<hash>.<ext>``
    (plus ``<hash>-fNN.<ext>`` read-aloud segments) so a turn keeps a replayable
    player. That audio is a spoken rendering of the whole conversation — the
    most directly re-identifiable form the reply takes — and NOTHING in the
    chain erased it: ``voice/`` is a SIBLING of ``artifacts/``, so
    L33ArtifactHandler never saw it, and ACSTraceHandler only owns ``acs/``. An
    Art. 17 run therefore reported APPLIED across every layer and wrote a
    successful erasure receipt into the hash-chained audit log while the audio
    survived untouched on disk. Two comments in the console asserted the
    opposite ("erased with the session"), which is true only of the
    delete-session route (which rmtree's the whole workdir), not of the
    orchestrator.

    The turn log ``<global>/web_chat/sessions/<sid>.turns.jsonl`` is orphaned by
    the exact same gap — it lives outside the session dir entirely — so it is
    purged here too rather than left for a second follow-up handler.

    The session META file ``<sid>.json`` in the SAME directory (written by
    chat_runtime._meta_path / _write_meta) is purged too: it carries the
    session ``title``, which the console derives from the user's FIRST chat
    message — i.e. verbatim user content, PII by construction. The first cut
    of this handler removed the turn log but left the meta file sitting right
    next to it, so an Art. 17 run reported APPLIED while the opening question
    survived on disk (found 2026-07-17). ``<sid>.json.tmp`` — _write_meta's
    atomic-rename staging name, left behind by a crash/ENOSPC mid-write — is
    swept for the same reason.

    Two more workdir siblings of ``voice/`` hold user content and are purged
    with the same pattern (adversarial round, 2026-07-17):

    * ``<workdir>/attachments/`` — raw user uploads (routes/chat.py
      ``upload_attachments``), the most literal PII in the whole session;
    * ``<workdir>/compute_inbox/`` — ``*_result.json`` notifications whose
      ``description`` field carries the user's task text verbatim (plus the
      ``processed/`` mirror _drain_compute_inbox moves them into).

    KNOWN GAP (deliberately NOT handled here): the engine's own conversation
    transcripts under ``~/.claude/projects/<slug>/*.jsonl`` also contain the
    session's content, but they belong to the engine installation, not the
    tenant store — erasing them needs a subject→slug mapping and an
    engine-agnostic contract, i.e. its own ADR. Do not silently widen this
    handler to reach into engine homes.

    ``subject_id`` is the session key as the console names it (``web:<sid>``);
    the turn log is keyed on the bare ``<sid>``.

    Returns APPLIED if anything was removed, SKIPPED if absent/empty.
    """
    layer_id: str = "web-chat"
    tenant_id: str = "_default"

    def purge(self, subject_id: str, request_id: str) -> ErasureLayerResult:
        t0 = time.time()
        targets: list[Path] = []

        # Ask the WRITER's own resolver where the dir is rather than re-deriving
        # the sanitising rule — a reader that disagrees with the writer is
        # exactly how audio gets orphaned, and this handler exists because of
        # that class of bug. (An earlier cut here guessed `.replace(":", "_")`,
        # which misses fs_safe_component's other rules: it also strips trailing
        # dots and guards Windows device names, so `web:abc.` is written to
        # `web_abc` and the guess would have looked for `web_abc.` and reported
        # a clean SKIPPED while the audio survived the erasure.)
        # Erasure must be THOROUGH, so try every plausible spelling rather than
        # the one this host would write today: the raw key, the writer's own
        # resolved name, and the ':'->'_' form. A dir can exist under a spelling
        # this host wouldn't produce — e.g. an archive written by a Windows
        # install and then read on POSIX (where safe_session_subdir is a no-op),
        # or a legacy dir. Missing one means a clean SKIPPED while the audio
        # survives an Art. 17 erasure, which is the bug this handler exists for.
        sessions = _tenant_sessions(self.tenant_id)
        names = {subject_id, subject_id.replace(":", "_")}
        try:
            from forge.paths import safe_session_subdir  # noqa: PLC0415
            names.add(safe_session_subdir(sessions, subject_id).name)
        except Exception:  # noqa: BLE001 — forge may not be importable here
            pass
        # voice/ (synthesised speech), attachments/ (raw user uploads),
        # compute_inbox/ (task-result notifications carrying the user's task
        # text in `description`, incl. the processed/ mirror), cel-briefs/
        # (ADR-0278 Layer B — full rendered context brief text, PII) — all
        # workdir siblings of artifacts/ that no other handler owns.
        purge_dirs: list[Path] = []
        for name in names:
            for sub in ("voice", "attachments", "compute_inbox", "cel-briefs"):
                d = sessions / name / sub
                if d.is_dir():
                    purge_dirs.append(d)
                    targets.extend(p for p in d.rglob("*") if p.is_file())

        # Turn log + session meta: `web:<sid>` -> `<sid>.turns.jsonl` /
        # `<sid>.json` under the global store. The meta file's `title` is
        # derived from the user's first message (PII); the `.json.tmp`
        # spelling is _write_meta's crash-orphaned staging file. Same
        # thoroughness rule as the voice dir above: try both the raw sid and
        # its ':'->'_' form in case a key ever carried a colon into the bare
        # sid (defensive — the console strips the `web:` prefix today).
        sid = subject_id.split(":", 1)[1] if ":" in subject_id else subject_id
        store = _tenant_global(self.tenant_id) / "web_chat" / "sessions"
        for sid_spelling in {sid, sid.replace(":", "_")}:
            for fname in (f"{sid_spelling}.turns.jsonl",
                          f"{sid_spelling}.json",
                          f"{sid_spelling}.json.tmp"):
                candidate = store / fname
                if candidate.is_file():
                    targets.append(candidate)

        if not targets:
            return ErasureLayerResult(
                layer_id=self.layer_id, status=LayerStatus.SKIPPED, count=0,
                reason=(f"no web-chat user-content stores (voice/attachments/"
                        f"compute-inbox/turn-log/meta) for {subject_id}"),
                code=ReasonCode.STORE_ABSENT.value,
                duration_ms=int((time.time() - t0) * 1000),
            )

        n_files = 0
        n_bytes = 0
        for path in targets:
            try:
                n_bytes += path.stat().st_size
            except OSError:
                pass
            path.unlink(missing_ok=True)
            n_files += 1

        # Remove the emptied dirs, deepest-first so nested subdirs
        # (compute_inbox/processed/) fall before their parents.
        for d in purge_dirs:   # same dirs we purged from
            subdirs = sorted(
                (p for p in d.rglob("*") if p.is_dir()),
                key=lambda p: len(p.parts), reverse=True,
            )
            for sub in (*subdirs, d):
                try:
                    sub.rmdir()
                except OSError:
                    pass  # non-empty or absent — the files are what matter

        return ErasureLayerResult(
            layer_id=self.layer_id, status=LayerStatus.APPLIED, count=n_files,
            reason=(f"removed {n_files} web-chat file(s) (voice/attachments/"
                    f"compute-inbox/turn-log/meta, {n_bytes} bytes)"),
            code=ReasonCode.DELETED.value,
            duration_ms=int((time.time() - t0) * 1000),
        )


# ── L7 skill-forge handler ──────────────────────────────────────────


@dataclass
class L7SkillForgeHandler:
    """GDPR Art. 17 erasure for user-scope skills (R4-F4).

    Until 2026-09-07 this returned ``SKIPPED / not_applicable`` without looking
    at the filesystem, while ``COVERED_DIRS`` claimed ``skill-forge`` and
    ``skills`` were covered — so a skill file named after the subject, or a
    registry entry naming them as its author, survived an erasure the
    orchestrator then reported as ``completed``. A claim backed by a permanent
    no-op is worse than no claim: it converts a documented gap into a passing
    coverage assertion.

    Roots: ``<tenant>/skill-forge``, ``<tenant>/skills``,
    ``<tenant>/global/skill-forge``, ``<tenant>/global/skills`` and
    ``<tenant>/_shared`` (the migrated ``~/.claude/skills`` copy). Attribution
    is the documented generic rule — see :func:`_purge_path`. ``skills_registry.json``
    and ``registry.yaml``-adjacent JSON registries are rewritten entry-wise, so
    one author's skills go without taking another's with them.
    """
    layer_id: str = "L7-skill-forge"
    tenant_id: str = "_default"

    def _roots(self) -> list[Path]:
        home = _tenant_home(self.tenant_id)
        return [home / "skill-forge", home / "skills", home / "_shared",
                _tenant_global(self.tenant_id) / "skill-forge",
                _tenant_global(self.tenant_id) / "skills"]

    def purge(self, subject_id: str, request_id: str) -> ErasureLayerResult:
        return _purge_roots(self.layer_id, self._roots(), subject_id,
                            store="user-scope skill", item="skill artefact")


# ── L24 data-snapshot handler ───────────────────────────────────────


@dataclass
class L24DataSnapshotHandler:
    """GDPR Art. 17 erasure for L24 large-data snapshots (R4-F4).

    Same defect as :class:`L7SkillForgeHandler`: ``COVERED_DIRS`` claimed
    ``global/data`` while this returned ``SKIPPED / not_applicable`` without a
    filesystem call, so a snapshot manifest naming the subject survived a
    ``completed`` erasure.

    Snapshots are PII-redacted at creation per the L24 design, so the leak
    vector is the manifest metadata — which is exactly what the generic
    attribution rule (:func:`_purge_path`) reaches: a manifest whose identity
    field is the subject, a snapshot directory named after them, or a registry
    entry keyed on them.
    """
    layer_id: str = "L24-data-snapshot"
    tenant_id: str = "_default"

    def _roots(self) -> list[Path]:
        return [_tenant_global(self.tenant_id) / "data"]

    def purge(self, subject_id: str, request_id: str) -> ErasureLayerResult:
        return _purge_roots(self.layer_id, self._roots(), subject_id,
                            store="data snapshot", item="snapshot record")


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


def _tenant_home(tenant_id: str = "_default") -> Path:
    return _corvin_home() / "tenants" / tenant_id


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


#: Keys under which a stored record names its data subject. A file/record is
#: attributed to ``subject_id`` when any of these equals it (exact match).
#:
#: R2-A7: this list IS the attribution rule, so a key missing from it is a
#: record that survives an Art. 17 erasure while the orchestrator reports
#: COMPLETED — the worst possible combination, because the operator has a
#: signed record saying the data is gone. The review found the subject sitting
#: under ``speaker`` in transcript-shaped stores and surviving. Widened to every
#: spelling the repo's writers actually use for "the person this is about";
#: adding a key here is cheap and the failure mode of omitting one is silent.
_SUBJECT_KEYS: tuple[str, ...] = (
    "user_id", "uid", "subject_id", "chat_key", "chat_id", "session_id",
    "source_session_id", "dest_session_id", "requester", "approver", "owner",
    # R2-A7 additions — transcript / authorship / actor spellings.
    "speaker", "author", "actor", "actor_id", "sender", "from_user",
    "created_by", "requested_by", "participant", "user", "username",
    "account_id", "owner_id", "session_key", "subject",
)


#: Filename of the infinite-session snapshot index (mirrors
#: ``core.infinite_session.event_store.INDEX_FILE`` without importing across the
#: repo-root boundary, the same way ``_tenant_global`` mirrors the resolver).
INDEX_FILE = "index.json"

#: Audit event marking a LAWFUL discontinuity in a snapshot chain: an Art. 17
#: erasure removed entries, so the ``seq`` sequence has a gap on purpose.
CHAIN_SEAM_EVENT = "erasure.chain_seam"


def _record_chain_seam(*, tenant_id: str, request_id: str, task_id: str,
                       dropped: list) -> None:
    """Record that an erasure created a seq gap in a snapshot chain (R2-A7).

    Without this the gap is reported by ``verify_snapshot_chain`` as
    "Chain gap at seq N" — the same message a tamper produces. Content-free:
    counts and sequence numbers only, never the subject id (that is exactly the
    identifier the erasure removed, so writing it into an append-only chain
    would undo the erasure).
    """
    try:
        from audit import audit_event  # type: ignore[import-not-found]
    except ImportError:
        return
    try:
        seqs = sorted(int(s) for s in dropped)
    except (TypeError, ValueError):
        seqs = []
    try:
        audit_event(
            CHAIN_SEAM_EVENT,
            details={
                "request_id": request_id,
                "task_id": str(task_id)[:128],
                "removed_count": len(dropped),
                "first_seq": seqs[0] if seqs else 0,
                "last_seq": seqs[-1] if seqs else 0,
                "reason": "gdpr_art17_erasure",
                "tenant_id": tenant_id,
            },
            tenant_id=tenant_id,
        )
    except Exception:  # noqa: BLE001 — the erasure must not fail on its own record
        pass


def _atomic_replace_text(target: Path, text: str) -> None:
    """Crash-atomic rewrite of ``target`` (R3 follow-up).

    Every entry-wise rewrite in this module (JSONL line filters, the
    infinite-session ``index.json``) was ``tmp.write_text(...)`` + ``os.replace``.
    ``os.replace`` makes the NAME swap atomic, but nothing had flushed the tmp
    file's bytes: a crash between the write and the writeback leaves the new name
    pointing at a truncated or zero-length file, and an erasure that half-rewrote
    a snapshot index is indistinguishable from tampering (``verify_snapshot_chain``
    reports a chain gap either way). The tmp name was also FIXED
    (``index.json.erasing``), so two erasures running at once clobbered each
    other's staging file.

    Sequence: unique tmp → write → ``fsync`` the file → ``os.replace`` → ``fsync``
    the DIRECTORY (so the rename itself is durable). The directory fsync is
    best-effort: it fails on filesystems that refuse an O_RDONLY fsync of a
    directory, and there the rename durability is the filesystem's own guarantee.
    """
    tmp = target.with_name(f"{target.name}.{os.getpid()}.erasing")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    try:
        dfd = os.open(str(target.parent), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:  # pragma: no cover - platform dependent
        pass


#: Characters that separate an identifier from the rest of a filename. A subject
#: id may itself contain ``.``, ``:``, ``_`` and ``-`` (``_SUBJECT_ID_RE``), so a
#: bare substring test would let ``u1`` match ``u12``. The id must sit between
#: these separators — or be the whole stem — to count.
_NAME_SEPARATORS = "._-:@ "


def _name_names_subject(name: str, subject_id: str) -> bool:
    """True when a FILE OR DIRECTORY NAME attributes it to ``subject_id``.

    The follow-up this closes: erasure attributed a path either by a DIRECTORY
    named exactly after the subject or by a JSON/JSONL PAYLOAD naming it under a
    known identity key. A file whose only mention of the subject is its own NAME
    — ``<subject>.json``, ``snapshot_<subject>.jsonl``, ``<subject>-profile.json``
    — matched neither, so it survived an erasure the orchestrator then reported
    as COMPLETED. A filename is personal data exactly like a payload field.

    Token-bounded, never a bare substring: the id must be the whole name, the
    whole stem, or a run delimited by :data:`_NAME_SEPARATORS`. Over-matching
    here deletes ANOTHER subject's data, which is its own Art. 5 breach.
    """
    if not subject_id or not name:
        return False
    if name == subject_id:
        return True
    idx = 0
    while True:
        idx = name.find(subject_id, idx)
        if idx < 0:
            return False
        before_ok = idx == 0 or name[idx - 1] in _NAME_SEPARATORS
        after = idx + len(subject_id)
        after_ok = after == len(name) or name[after] in _NAME_SEPARATORS
        if before_ok and after_ok:
            return True
        idx += 1


def _mentions_subject(obj: Any, subject_id: str, depth: int = 0) -> bool:
    if depth > 8:
        return False
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _SUBJECT_KEYS and isinstance(v, str) and v == subject_id:
                return True
            if _mentions_subject(v, subject_id, depth + 1):
                return True
    elif isinstance(obj, list):
        return any(_mentions_subject(v, subject_id, depth + 1) for v in obj)
    return False


@dataclass
class LearningEventHandler:
    """GDPR Art. 17 erasure for the ADR-0314 learning layer (F-A9).

    Covers ``<tenant>/learning/`` — the date-partitioned learning event store
    (``EventStore.erase_user_events``: every partition is rewritten atomically
    and receives a counts-only tombstone; the erasure is audited on the core
    chain) plus any JSONL side store under ``learning/`` whose records name the
    subject. ``core/learning/erasure_handler.py`` /
    ``gdpr_erasure_coordinator.py`` existed but were reachable from NO erasure
    entry point (zero importers) — this handler is the bridge into the
    orchestrator's chain.
    """
    tenant_id: str = "_default"
    layer_id: str = "L-learning"

    def purge(self, subject_id: str, request_id: str) -> ErasureLayerResult:
        t0 = time.time()
        root = _tenant_home(self.tenant_id) / "learning"
        if not root.is_dir():
            return ErasureLayerResult(
                layer_id=self.layer_id, status=LayerStatus.SKIPPED, count=0,
                reason="learning store absent", code=ReasonCode.STORE_ABSENT.value,
                duration_ms=int((time.time() - t0) * 1000),
            )
        erased = 0
        try:
            import asyncio
            import sys as _sys
            _repo = Path(__file__).resolve().parents[3]
            if str(_repo) not in _sys.path:
                _sys.path.append(str(_repo))
            from core.learning.event_persistence import EventStore  # type: ignore

            store = EventStore(self.tenant_id)
            coro = store.erase_user_events(tenant_id=self.tenant_id, user_id=subject_id)
            try:
                asyncio.get_running_loop()
                running = True
            except RuntimeError:
                running = False
            if running:
                import concurrent.futures as _cf
                with _cf.ThreadPoolExecutor(max_workers=1) as ex:
                    erased += int(ex.submit(asyncio.run, coro).result())
            else:
                erased += int(asyncio.run(coro))
            # Side stores (decisions / outcomes / profiles / live experiments):
            # JSONL records naming the subject are removed in place (atomic).
            events_dir = root / "events"
            for f in sorted(root.rglob("*.jsonl")):
                if events_dir in f.parents:
                    continue  # handled by EventStore above (tombstoned)
                kept: list[str] = []
                removed = 0
                for line in f.read_text(encoding="utf-8").splitlines():
                    rec = None
                    if line.strip():
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            rec = None
                    if rec is not None and _mentions_subject(rec, subject_id):
                        removed += 1
                        continue
                    kept.append(line)
                if removed:
                    _atomic_replace_text(f, "\n".join(kept) + ("\n" if kept else ""))
                    erased += removed
        except Exception as exc:  # noqa: BLE001 — genuine infra failure → FAILED
            return ErasureLayerResult(
                layer_id=self.layer_id, status=LayerStatus.FAILED, count=erased,
                reason=f"learning purge error: {type(exc).__name__}: {str(exc)[:200]}",
                code=ReasonCode.STORE_ERROR.value,
                duration_ms=int((time.time() - t0) * 1000),
            )
        if erased == 0:
            return ErasureLayerResult(
                layer_id=self.layer_id, status=LayerStatus.SKIPPED, count=0,
                reason="no learning records matched subject", code=ReasonCode.STORE_EMPTY.value,
                duration_ms=int((time.time() - t0) * 1000),
            )
        return ErasureLayerResult(
            layer_id=self.layer_id, status=LayerStatus.APPLIED, count=erased,
            reason=f"erased {erased} learning record(s) for subject",
            code=ReasonCode.DELETED.value,
            duration_ms=int((time.time() - t0) * 1000),
        )


@dataclass
class InfiniteSessionHandler:
    """GDPR Art. 17 erasure for infinite-session state (F-A9).

    Covers ``<tenant>/infinite_session/`` (phase snapshots, rollback WAL/log,
    drift history, session bridges, verification logs) and ``<tenant>/sessions/
    <subject>/checkpoints``. State is keyed by ``task_id``, not by person, so a
    file is attributed to the subject when (a) its task/session directory is
    named after the subject, (b) its own FILENAME names the subject
    (:func:`_name_names_subject` — a file called ``<subject>.json`` or
    ``snapshot_<subject>.jsonl`` is that person's data even when its payload
    never repeats the id, and it used to survive a COMPLETED erasure), or (c) its
    JSON payload names the subject under a known identity key (``session_id``,
    ``user_id``, ``chat_key`` …).
    """
    tenant_id: str = "_default"
    layer_id: str = "L-infinite-session"

    def _roots(self) -> list[Path]:
        home = _tenant_home(self.tenant_id)
        return [home / "infinite_session", home / "sessions" / self.tenant_id / "checkpoints"]

    def purge(self, subject_id: str, request_id: str) -> ErasureLayerResult:
        t0 = time.time()
        home = _tenant_home(self.tenant_id)
        roots = [home / "infinite_session", home / "sessions"]
        if not any(r.is_dir() for r in roots):
            return ErasureLayerResult(
                layer_id=self.layer_id, status=LayerStatus.SKIPPED, count=0,
                reason="infinite-session store absent", code=ReasonCode.STORE_ABSENT.value,
                duration_ms=int((time.time() - t0) * 1000),
            )
        removed = 0
        try:
            import shutil
            # (a) directories named after the subject (task_id / session_id == subject)
            for r in roots:
                if not r.is_dir():
                    continue
                for d in list(r.rglob("*")):
                    if not _name_names_subject(d.name, subject_id):
                        continue
                    if d.is_dir():
                        shutil.rmtree(d, ignore_errors=False)
                        removed += 1
                    elif d.is_file() and d.name != INDEX_FILE:
                        # A file whose NAME names the subject is that subject's
                        # data even when its payload does not repeat the id.
                        d.unlink()
                        removed += 1
            # (b) JSON/JSONL files under infinite_session/ whose payload names the subject
            isr = home / "infinite_session"
            if isr.is_dir():
                for f in sorted(isr.rglob("*")):
                    if not f.is_file() or f.suffix not in (".json", ".jsonl"):
                        continue
                    # The snapshot index names every snapshot of the task, so it
                    # mentions the subject and this sweep would DELETE it —
                    # taking the surviving subjects' entries with it. It has its
                    # own pass below, which rewrites it entry-wise and records
                    # the resulting seq seam.
                    if f.name == INDEX_FILE:
                        continue
                    if f.suffix == ".json":
                        if _mentions_subject(_load_json(f), subject_id):
                            f.unlink()
                            removed += 1
                        continue
                    kept: list[str] = []
                    hit = 0
                    for line in f.read_text(encoding="utf-8").splitlines():
                        rec = None
                        if line.strip():
                            try:
                                rec = json.loads(line)
                            except json.JSONDecodeError:
                                rec = None
                        if rec is not None and _mentions_subject(rec, subject_id):
                            hit += 1
                            continue
                        kept.append(line)
                    if hit:
                        _atomic_replace_text(f, "\n".join(kept) + ("\n" if kept else ""))
                        removed += hit
                # R2-A7: index.json is the snapshot CHAIN's index — an ordered
                # list of SnapshotMetadata with a contiguous ``seq`` that
                # ``EventStore.verify_snapshot_chain`` checks. Two defects here:
                #
                #  * it filtered on ``m["path"]``, but the metadata key is
                #    ``file_path`` — so nothing was ever dropped and the index
                #    kept naming snapshots that had just been deleted. Every
                #    later read of that task failed on a missing file, and the
                #    subject's task/phase ids stayed on disk IN the index, which
                #    is itself personal data the erasure was meant to remove.
                #  * even done correctly, removing entries leaves a ``seq`` gap,
                #    which verify reports as "Chain gap" — indistinguishable
                #    from tampering. An erasure is a LAWFUL discontinuity, so it
                #    is recorded as a seam rather than left to look like damage.
                for idx in isr.rglob(INDEX_FILE):
                    data = _load_json(idx)
                    if not isinstance(data, list):
                        continue
                    live = []
                    dropped_seqs: list[int] = []
                    for m in data:
                        if not isinstance(m, dict):
                            live.append(m)
                            continue
                        fp = m.get("file_path") or m.get("path")
                        gone = isinstance(fp, str) and fp and not Path(fp).exists()
                        if gone or _mentions_subject(m, subject_id):
                            try:
                                dropped_seqs.append(int(m.get("seq", 0)))
                            except (TypeError, ValueError):
                                dropped_seqs.append(0)
                            continue
                        live.append(m)
                    if not dropped_seqs:
                        continue
                    _atomic_replace_text(idx, json.dumps(live))
                    removed += len(dropped_seqs)
                    _record_chain_seam(
                        tenant_id=self.tenant_id, request_id=request_id,
                        task_id=idx.parent.name, dropped=dropped_seqs,
                    )
        except Exception as exc:  # noqa: BLE001
            return ErasureLayerResult(
                layer_id=self.layer_id, status=LayerStatus.FAILED, count=removed,
                reason=f"infinite-session purge error: {type(exc).__name__}: {str(exc)[:200]}",
                code=ReasonCode.STORE_ERROR.value,
                duration_ms=int((time.time() - t0) * 1000),
            )
        if removed == 0:
            return ErasureLayerResult(
                layer_id=self.layer_id, status=LayerStatus.SKIPPED, count=0,
                reason="no infinite-session state matched subject", code=ReasonCode.STORE_EMPTY.value,
                duration_ms=int((time.time() - t0) * 1000),
            )
        return ErasureLayerResult(
            layer_id=self.layer_id, status=LayerStatus.APPLIED, count=removed,
            reason=f"removed {removed} infinite-session item(s) for subject",
            code=ReasonCode.DELETED.value,
            duration_ms=int((time.time() - t0) * 1000),
        )


# ── R2-A7: stores that had NO Art. 17 path at all ────────────────────────────
#
# Each of these is a live, writer-created directory under the tenant home that
# no handler claimed. The orchestrator therefore ran to COMPLETED — a signed
# statement that the subject's data is gone — with the data still on disk. That
# is worse than an outright failure, which at least tells the operator to act.


def _record_names_subject(rec: Any, subject_id: str) -> bool:
    """True when the record's OWN top-level identity field is the subject.

    Distinct from :func:`_mentions_subject`, which is true for a mention at any
    depth. The difference decides whether a stored document IS the subject's
    record (delete the whole file) or merely CONTAINS one among several
    (filter the container — deleting it would erase other people's data, which
    is its own Art. 5 breach).
    """
    if not isinstance(rec, dict):
        return False
    return any(k in _SUBJECT_KEYS and isinstance(v, str) and v == subject_id
               for k, v in rec.items())


def _purge_json_document(f: Path, subject_id: str) -> int:
    """Purge one ``.json`` file. Returns the number of records removed.

    Container-aware (R4-F1): a whole-file delete is only correct when the file
    IS the subject's record. An aggregate document — a list of runs, a
    ``{id: record}`` registry, a SCIM user directory — holds many subjects, and
    deleting it to erase one person destroys the others' data. Those are
    rewritten entry-wise instead, atomically.
    """
    data = _load_json(f)
    if isinstance(data, list):
        kept = [e for e in data if not _mentions_subject(e, subject_id)]
        n = len(data) - len(kept)
        if not n:
            return 0
        if kept:
            _atomic_replace_text(f, json.dumps(kept, ensure_ascii=False) + "\n")
        else:
            f.unlink()
        return n
    if isinstance(data, dict):
        if _record_names_subject(data, subject_id):
            f.unlink()
            return 1
        drop = [k for k, v in data.items()
                if isinstance(v, (dict, list)) and _mentions_subject(v, subject_id)]
        if drop:
            if len(drop) == len(data):
                f.unlink()
                return 1
            for k in drop:
                data.pop(k, None)
            _atomic_replace_text(f, json.dumps(data, ensure_ascii=False) + "\n")
            return len(drop)
        if _mentions_subject(data, subject_id):
            f.unlink()
            return 1
    return 0


def _purge_jsonl_file(f: Path, subject_id: str) -> int:
    """Drop every JSONL line naming the subject; keep the rest. Atomic."""
    kept: list[str] = []
    hit = 0
    try:
        lines = f.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return 0
    for line in lines:
        rec = None
        if line.strip():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                rec = None
        if rec is not None and _mentions_subject(rec, subject_id):
            hit += 1
            continue
        kept.append(line)
    if hit:
        _atomic_replace_text(f, "\n".join(kept) + ("\n" if kept else ""))
    return hit


def _purge_path(target: Path, subject_id: str, *, match_dir_name: bool = True) -> int:
    """Purge one tenant-home ENTRY — a directory tree or a single file.

    The three attribution routes are the documented Art. 17 rule for every
    generic store: a DIRECTORY named after the subject, a FILE named after the
    subject (:func:`_name_names_subject` — token-bounded, never a bare
    substring, and now for ANY suffix: ``<subject>.md`` and ``<subject>.db``
    are that person's data exactly as ``<subject>.json`` is), and a JSON/JSONL
    PAYLOAD naming the subject under a known identity key
    (:data:`_SUBJECT_KEYS`). Content that carries NONE of those is NOT guessed
    at — a substring hunt over free text deletes other people's records — and a
    store where no route can ever apply belongs in
    :data:`UNATTRIBUTABLE_DIRS`, where the orchestrator reports it as
    ``not_erasable`` instead of counting it silently as completed.
    """
    import shutil

    if target.is_file():
        if _name_names_subject(target.name, subject_id):
            target.unlink()
            return 1
        if target.suffix == ".json":
            return _purge_json_document(target, subject_id)
        if target.suffix == ".jsonl":
            return _purge_jsonl_file(target, subject_id)
        return 0
    if not target.is_dir():
        return 0

    removed = 0
    if match_dir_name:
        for d in list(target.rglob("*")):
            if d.is_dir() and _name_names_subject(d.name, subject_id):
                shutil.rmtree(d, ignore_errors=False)
                removed += 1
    for f in sorted(target.rglob("*")):
        if not f.is_file():
            continue
        if _name_names_subject(f.name, subject_id):
            f.unlink()
            removed += 1
            continue
        if f.suffix == ".json":
            removed += _purge_json_document(f, subject_id)
        elif f.suffix == ".jsonl":
            removed += _purge_jsonl_file(f, subject_id)
    return removed


def _purge_json_tree(root: Path, subject_id: str, *,
                     match_dir_name: bool = True) -> int:
    """Backwards-compatible alias of :func:`_purge_path` for a directory root."""
    return _purge_path(root, subject_id, match_dir_name=match_dir_name)


def _result(layer_id: str, t0: float, removed: int, *, absent: bool,
            absent_reason: str, empty_reason: str, applied_reason: str
            ) -> ErasureLayerResult:
    """The three-way SKIPPED/SKIPPED/APPLIED shape every handler here returns."""
    ms = int((time.time() - t0) * 1000)
    if absent:
        return ErasureLayerResult(
            layer_id=layer_id, status=LayerStatus.SKIPPED, count=0,
            reason=absent_reason, code=ReasonCode.STORE_ABSENT.value, duration_ms=ms,
        )
    if removed == 0:
        return ErasureLayerResult(
            layer_id=layer_id, status=LayerStatus.SKIPPED, count=0,
            reason=empty_reason, code=ReasonCode.STORE_EMPTY.value, duration_ms=ms,
        )
    return ErasureLayerResult(
        layer_id=layer_id, status=LayerStatus.APPLIED, count=removed,
        reason=applied_reason.format(n=removed), code=ReasonCode.DELETED.value,
        duration_ms=ms,
    )


def _entry_path(tenant_id: str, entry: str) -> Path:
    """Resolve a registry entry name (``"files"`` / ``"global/acs"``) to a path.

    The registries below name tenant-home ENTRIES, not absolute paths, because
    the coverage guard compares them against what the writers create under a
    throwaway home — the same vocabulary on both sides or the comparison is
    meaningless.
    """
    if entry.startswith("global/"):
        return _tenant_global(tenant_id) / entry[len("global/"):]
    return _tenant_home(tenant_id) / entry


def _purge_roots(layer_id: str, roots: "list[Path]", subject_id: str, *,
                 store: str, item: str, match_dir_name: bool = True
                 ) -> ErasureLayerResult:
    """Run the documented attribution rule over several roots as ONE layer."""
    t0 = time.time()
    present = [r for r in roots if r.exists()]
    if not present:
        return _result(layer_id, t0, 0, absent=True,
                       absent_reason=f"{store} store absent",
                       empty_reason="", applied_reason="")
    removed = 0
    try:
        for r in present:
            removed += _purge_path(r, subject_id, match_dir_name=match_dir_name)
    except Exception as exc:  # noqa: BLE001
        return ErasureLayerResult(
            layer_id=layer_id, status=LayerStatus.FAILED, count=removed,
            reason=f"{store} purge error: {type(exc).__name__}: {str(exc)[:200]}",
            code=ReasonCode.STORE_ERROR.value,
            duration_ms=int((time.time() - t0) * 1000),
        )
    return _result(layer_id, t0, removed, absent=False, absent_reason="",
                   empty_reason=f"no {item} matched subject",
                   applied_reason=f"removed {{n}} {item}(s) for subject")


@dataclass
class WorkflowChatHandler:
    """GDPR Art. 17 erasure for workflow authoring transcripts (R2-A7).

    ``core/console/corvin_console/routes/workflows.py`` appends every turn of
    the workflow-authoring conversation to
    ``<tenant>/workflows/<wid>.chat.jsonl`` verbatim — ``{role, content, ts}``,
    user-authored free text, no TTL, no erasure path. The run logs beside them
    (``<wid>/runs/<rid>.jsonl``) carry step output the same way.

    Attribution is line-wise via :data:`_SUBJECT_KEYS` (which is why ``speaker``
    was added to it), plus whole-file removal when the workflow's own metadata
    names the subject as its author/owner. A transcript line carrying no
    identity key is NOT guessed at — the honest answer is to leave it and let
    the count say what was removed, rather than deleting another operator's
    workflow on a substring hunch.
    """
    tenant_id: str = "_default"
    layer_id: str = "L-workflows"

    def purge(self, subject_id: str, request_id: str) -> ErasureLayerResult:
        t0 = time.time()
        root = _tenant_home(self.tenant_id) / "workflows"
        if not root.is_dir():
            return _result(self.layer_id, t0, 0, absent=True,
                           absent_reason="workflows store absent",
                           empty_reason="", applied_reason="")
        removed = 0
        try:
            # A workflow whose metadata names the subject goes whole: its
            # transcript, its runs and its YAML are that person's content.
            for meta in sorted(root.glob("*.meta.json")):
                if not _mentions_subject(_load_json(meta), subject_id):
                    continue
                wid = meta.name[: -len(".meta.json")]
                for extra in (root / f"{wid}.chat.jsonl", root / f"{wid}.awp.yaml",
                              meta):
                    if extra.exists():
                        extra.unlink()
                        removed += 1
                run_dir = root / wid
                if run_dir.is_dir():
                    import shutil
                    shutil.rmtree(run_dir, ignore_errors=True)
                    removed += 1
            removed += _purge_json_tree(root, subject_id)
        except Exception as exc:  # noqa: BLE001
            return ErasureLayerResult(
                layer_id=self.layer_id, status=LayerStatus.FAILED, count=removed,
                reason=f"workflow purge error: {type(exc).__name__}: {str(exc)[:200]}",
                code=ReasonCode.STORE_ERROR.value,
                duration_ms=int((time.time() - t0) * 1000),
            )
        return _result(self.layer_id, t0, removed, absent=False, absent_reason="",
                       empty_reason="no workflow content matched subject",
                       applied_reason="removed {n} workflow item(s) for subject")


@dataclass
class BrowserSessionHandler:
    """GDPR Art. 17 erasure for browser-automation profiles (R2-A7).

    ``BrowserSession`` gives each live session its own Chromium user-data dir at
    ``<tenant>/browser/sessions/<session_id>/`` — cookies, local storage, cached
    pages, saved credentials: among the most sensitive personal data the product
    ever writes, and it outlives the session (a console restart drops the live
    session, not the directory on disk).

    ``subject_id`` is the session id, so the directory NAME is the attribution —
    the same shape ``InfiniteSessionHandler`` uses.
    """
    tenant_id: str = "_default"
    layer_id: str = "L-browser"

    def purge(self, subject_id: str, request_id: str) -> ErasureLayerResult:
        t0 = time.time()
        root = _tenant_home(self.tenant_id) / "browser"
        if not root.is_dir():
            return _result(self.layer_id, t0, 0, absent=True,
                           absent_reason="browser store absent",
                           empty_reason="", applied_reason="")
        removed = 0
        try:
            import shutil
            for d in list(root.rglob(subject_id)):
                if d.is_dir() and d.name == subject_id:
                    shutil.rmtree(d, ignore_errors=False)
                    removed += 1
            removed += _purge_json_tree(root, subject_id, match_dir_name=False)
        except Exception as exc:  # noqa: BLE001
            return ErasureLayerResult(
                layer_id=self.layer_id, status=LayerStatus.FAILED, count=removed,
                reason=f"browser purge error: {type(exc).__name__}: {str(exc)[:200]}",
                code=ReasonCode.STORE_ERROR.value,
                duration_ms=int((time.time() - t0) * 1000),
            )
        return _result(self.layer_id, t0, removed, absent=False, absent_reason="",
                       empty_reason="no browser session matched subject",
                       applied_reason="removed {n} browser session profile(s)")


@dataclass
class VibeCheckpointHandler:
    """GDPR Art. 17 erasure for Vibe task-graph checkpoints (R2-A7).

    ``core/vibe_engineering/checkpoint_manager.py`` writes
    ``<tenant>/vibe/checkpoints/<checkpoint_id>.json``, each carrying the
    ``session_id`` and ``task_id`` it was taken for plus the captured task
    state. Tenant-scoped since 2026-09-07, but with no erasure path: the
    checkpoints simply accumulated.
    """
    tenant_id: str = "_default"
    layer_id: str = "L-vibe-checkpoints"

    def purge(self, subject_id: str, request_id: str) -> ErasureLayerResult:
        t0 = time.time()
        root = _tenant_home(self.tenant_id) / "vibe"
        if not root.is_dir():
            return _result(self.layer_id, t0, 0, absent=True,
                           absent_reason="vibe store absent",
                           empty_reason="", applied_reason="")
        try:
            removed = _purge_json_tree(root, subject_id)
        except Exception as exc:  # noqa: BLE001
            return ErasureLayerResult(
                layer_id=self.layer_id, status=LayerStatus.FAILED, count=0,
                reason=f"vibe purge error: {type(exc).__name__}: {str(exc)[:200]}",
                code=ReasonCode.STORE_ERROR.value,
                duration_ms=int((time.time() - t0) * 1000),
            )
        return _result(self.layer_id, t0, removed, absent=False, absent_reason="",
                       empty_reason="no vibe checkpoint matched subject",
                       applied_reason="removed {n} vibe checkpoint(s) for subject")


@dataclass
class DatasourceConnectionHandler:
    """GDPR Art. 17 erasure for DSI connection manifests (R2-A7).

    ``<tenant>/datasource_connections/<name>.json`` (mode 0600) holds the
    manifest an operator registered: adapter, source config, region, and the
    vault key naming the credential. A manifest created for one person's data
    source names that person; it had no erasure path.
    """
    tenant_id: str = "_default"
    layer_id: str = "L-datasource-connections"

    def purge(self, subject_id: str, request_id: str) -> ErasureLayerResult:
        t0 = time.time()
        root = _tenant_home(self.tenant_id) / "datasource_connections"
        if not root.is_dir():
            return _result(self.layer_id, t0, 0, absent=True,
                           absent_reason="datasource connection store absent",
                           empty_reason="", applied_reason="")
        try:
            removed = _purge_json_tree(root, subject_id, match_dir_name=False)
        except Exception as exc:  # noqa: BLE001
            return ErasureLayerResult(
                layer_id=self.layer_id, status=LayerStatus.FAILED, count=0,
                reason=f"datasource purge error: {type(exc).__name__}: {str(exc)[:200]}",
                code=ReasonCode.STORE_ERROR.value,
                duration_ms=int((time.time() - t0) * 1000),
            )
        return _result(self.layer_id, t0, removed, absent=False, absent_reason="",
                       empty_reason="no datasource connection matched subject",
                       applied_reason="removed {n} datasource connection(s)")


# ── R4-F1: stores the round-2 guard could not see ────────────────────────────
#
# The round-2 guard enumerated only the directories created by the five writers
# its author imported, so two live personal-data stores — the CEL anchor store
# (the operator's own conversation text) and the tenant-global ACS run index
# (which names the subject's session) — never appeared in its universe and the
# orchestrator reported ``completed`` with both intact. The guard now derives
# its universe from the repo source (see
# ``tests/security/test_erasure_coverage_guard.py``), and every store it finds
# is classified below.


def _cel_safe_key(session_key: str) -> str:
    """Mirror ``operator/context_engineering/anchor.py::_safe_key``.

    The CEL anchor store is ``cel_anchors/<safe_key(session_key)>.jsonl`` — the
    session key with every character outside ``[A-Za-z0-9_.-]`` replaced by
    ``_``. An Art. 17 subject_id may legally contain ``:`` (``web:<sid>``), so
    matching the raw id against the filename finds nothing; the transform has to
    be applied on this side too. Duplicated rather than imported for the same
    reason ``_tenant_global`` duplicates the resolver: this module is loaded
    from the bridges sys.path, where ``operator.context_engineering`` is not
    importable.
    """
    s = re.sub(r"[^A-Za-z0-9_.-]", "_", str(session_key or "")).strip("_")
    if not s:
        return "_nosession"
    if len(s) > 100:
        digest = hashlib.sha1(str(session_key).encode("utf-8")).hexdigest()[:12]
        s = s[:80] + "_" + digest
    return s


@dataclass
class CELAnchorHandler:
    """GDPR Art. 17 erasure for the CEL load-bearing-anchor store (R4-F1).

    ``operator/context_engineering/anchor.py::_store_path`` writes
    ``<tenant>/cel_anchors/<safe_key>.jsonl`` on every turn where the
    ``cel_load_bearing_anchor`` flag is on (it is, in the live capability
    snapshot). Each line is ``{"id", "kind", "text", "added_at", "hash"}`` and
    ``text`` is the user's VERBATIM sentence — the review's reproduction found
    ``"my phone is 0170-… and I live in …"`` still on disk after a ``completed``
    erasure.

    Attribution is the file NAME: the store is per-session and the entries carry
    no identity field of their own, so both the raw subject_id and its
    ``_safe_key`` transform are matched. Documented explicitly because it is the
    ONLY route here — there is nothing in a fact entry to key on.
    """
    tenant_id: str = "_default"
    layer_id: str = "L-cel-anchors"

    def purge(self, subject_id: str, request_id: str) -> ErasureLayerResult:
        t0 = time.time()
        root = _tenant_home(self.tenant_id) / "cel_anchors"
        if not root.is_dir():
            return _result(self.layer_id, t0, 0, absent=True,
                           absent_reason="CEL anchor store absent",
                           empty_reason="", applied_reason="")
        removed = 0
        try:
            safe = _cel_safe_key(subject_id)
            for f in sorted(root.glob("*.jsonl")):
                if f.stem == safe or _name_names_subject(f.name, subject_id):
                    f.unlink()
                    removed += 1
            removed += _purge_path(root, subject_id)
        except Exception as exc:  # noqa: BLE001
            return ErasureLayerResult(
                layer_id=self.layer_id, status=LayerStatus.FAILED, count=removed,
                reason=f"CEL anchor purge error: {type(exc).__name__}: {str(exc)[:200]}",
                code=ReasonCode.STORE_ERROR.value,
                duration_ms=int((time.time() - t0) * 1000),
            )
        return _result(self.layer_id, t0, removed, absent=False, absent_reason="",
                       empty_reason="no CEL anchor store matched subject",
                       applied_reason="removed {n} CEL anchor store(s) for subject")


def _path_value_names_subject(obj: Any, subject_id: str, depth: int = 0) -> bool:
    """True when any string in ``obj`` is a PATH with the subject as a segment.

    ``<tenant>/global/acs/runs/<rid>/manifest.json`` attributes its run through
    ``"run_dir": ".../sessions/web:<sid>/acs/runs/<rid>"`` — a filesystem path,
    not an identity field, so :func:`_mentions_subject` walks straight past it.
    Segment-bounded (``split("/")``), never a bare substring.
    """
    if depth > 8:
        return False
    if isinstance(obj, str):
        if "/" not in obj:
            return False
        return any(_name_names_subject(seg, subject_id)
                   for seg in obj.split("/") if seg)
    if isinstance(obj, dict):
        return any(_path_value_names_subject(v, subject_id, depth + 1)
                   for v in obj.values())
    if isinstance(obj, list):
        return any(_path_value_names_subject(v, subject_id, depth + 1) for v in obj)
    return False


@dataclass
class ACSGlobalIndexHandler:
    """GDPR Art. 17 erasure for the tenant-global ACS run index (R4-F1).

    ``chat_runtime.py`` and ``acs_engine_adapter.py`` mirror every ACS run into
    ``<tenant>/global/acs/runs/<run_id>/manifest.json``. :class:`ACSTraceHandler`
    erases the SESSION-scoped run data this index points at; the index itself was
    unclaimed, so after an erasure it still named ``sessions/<subject>/acs/…``
    — and the quota-fallback branch additionally writes the delegate's whole
    ``output/`` working tree under the same run directory, which survived
    untouched.

    A run is the subject's when its manifest names them under an identity key OR
    when any path value in it has the subject as a segment
    (:func:`_path_value_names_subject`) — the shape the live install actually has.
    """
    tenant_id: str = "_default"
    layer_id: str = "L-acs-index"

    def purge(self, subject_id: str, request_id: str) -> ErasureLayerResult:
        t0 = time.time()
        root = _tenant_global(self.tenant_id) / "acs"
        if not root.is_dir():
            return _result(self.layer_id, t0, 0, absent=True,
                           absent_reason="global ACS index absent",
                           empty_reason="", applied_reason="")
        removed = 0
        try:
            import shutil
            runs = root / "runs"
            if runs.is_dir():
                for run_dir in sorted(p for p in runs.iterdir() if p.is_dir()):
                    docs = [_load_json(f) for f in run_dir.glob("*.json")]
                    if any(d is not None and (_mentions_subject(d, subject_id)
                                              or _path_value_names_subject(d, subject_id))
                           for d in docs):
                        shutil.rmtree(run_dir, ignore_errors=False)
                        removed += 1
            removed += _purge_path(root, subject_id)
        except Exception as exc:  # noqa: BLE001
            return ErasureLayerResult(
                layer_id=self.layer_id, status=LayerStatus.FAILED, count=removed,
                reason=f"ACS index purge error: {type(exc).__name__}: {str(exc)[:200]}",
                code=ReasonCode.STORE_ERROR.value,
                duration_ms=int((time.time() - t0) * 1000),
            )
        return _result(self.layer_id, t0, removed, absent=False, absent_reason="",
                       empty_reason="no ACS run matched subject",
                       applied_reason="removed {n} ACS run record(s) for subject")


@dataclass
class TenantStoreHandler:
    """Data-driven Art. 17 handler for a declared group of tenant-home stores.

    One class instead of fifteen near-identical ones: every store below is
    erased by the SAME documented attribution rule (:func:`_purge_path`), so the
    only thing that differs is which entries the layer claims. Grouping keeps the
    per-layer trail readable — a reader sees ``L-user-content skipped
    store_empty`` rather than ten separate lines — while the coverage guard still
    proves each individual entry erases a planted subject file.
    """
    layer_id: str
    entries: tuple
    store: str
    item: str
    tenant_id: str = "_default"

    def purge(self, subject_id: str, request_id: str) -> ErasureLayerResult:
        roots = [_entry_path(self.tenant_id, e) for e in self.entries]
        return _purge_roots(self.layer_id, roots, subject_id,
                            store=self.store, item=self.item)


@dataclass
class UnattributableStoreHandler:
    """Report — never silently pass — stores that CANNOT be erased per subject.

    ``UNATTRIBUTABLE_DIRS`` holds live stores that carry personal data but no
    per-subject attribution of any kind: no directory name, no file name, no
    identity field. An Art. 17 request cannot select one person's records out of
    them, and a substring hunt over free text would delete other people's.

    The failure mode this exists to prevent is the one the review found: the
    orchestrator returning ``completed`` — a signed statement that the data is
    gone — while the store stands. When such a store is present and non-empty
    this layer returns ``SKIPPED`` with the controlled code ``not_erasable``, so
    the operator sees it in the trail and on the audit chain and can act
    (retention / TTL / a real attribution key) instead of believing the erasure
    was total.
    """
    tenant_id: str = "_default"
    layer_id: str = "L-unattributable-stores"

    def purge(self, subject_id: str, request_id: str) -> ErasureLayerResult:
        t0 = time.time()
        standing: list[str] = []
        for entry in sorted(UNATTRIBUTABLE_DIRS):
            path = _entry_path(self.tenant_id, entry)
            try:
                if path.is_dir() and any(path.rglob("*")):
                    standing.append(entry)
                elif path.is_file() and path.stat().st_size > 0:
                    standing.append(entry)
            except OSError:
                continue
        ms = int((time.time() - t0) * 1000)
        if not standing:
            return ErasureLayerResult(
                layer_id=self.layer_id, status=LayerStatus.SKIPPED, count=0,
                reason="no unattributable store holds data",
                code=ReasonCode.STORE_ABSENT.value, duration_ms=ms,
            )
        return ErasureLayerResult(
            layer_id=self.layer_id, status=LayerStatus.SKIPPED, count=0,
            reason=("stores hold personal data with NO per-subject attribution and "
                    "were NOT erased: " + ", ".join(standing)),
            code=ReasonCode.NOT_ERASABLE.value, duration_ms=ms,
        )


#: Coverage map: which tenant-home ENTRY each real handler claims.
#:
#: ``tests/security/test_erasure_coverage_guard.py`` derives the set of entries
#: the product's writers create from the REPO SOURCE (an AST scan for
#: ``tenant_home()/…``, ``tenant_global_dir()/…``, ``corvin_home()/"tenants"/…``)
#: and from booting the writers it can reach, then fails on any entry that
#: appears in neither this map nor :data:`NON_PERSONAL_DIRS` nor
#: :data:`UNATTRIBUTABLE_DIRS`. That universe is mechanical: unlike the round-2
#: version it cannot be satisfied by a hand-written list of imports.
#:
#: An entry here is a PROMISE, checked by
#: ``TestEveryCoveredEntryActuallyErases``: a subject-named record planted in
#: that entry must be gone after the real chain runs. A claim backed by a
#: permanent ``SKIPPED / not_applicable`` stub — which is what ``skill-forge``,
#: ``skills`` and ``global/data`` were until 2026-09-07 — now fails the guard.
#:
#: What the promise covers is the documented attribution rule of
#: :func:`_purge_path` (directory name · file name · JSON/JSONL identity field),
#: NOT "every byte in the directory": free text that names nobody is left alone
#: on purpose, because guessing deletes other subjects' data.
COVERED_DIRS: dict[str, frozenset[str]] = {
    "L28-recall":            frozenset({"global/memory"}),
    "L28.2-user-model":      frozenset({"global/memory"}),
    "L33-artifacts":         frozenset({"sessions"}),
    "ACS-traces":            frozenset({"sessions"}),
    "web-chat":              frozenset({"sessions", "global/web_chat"}),
    "L39-social":            frozenset({"global/social"}),
    "L41-grants":            frozenset({"global/grants"}),
    "L42-org":               frozenset({"global/orgs"}),
    "L-workflow-checkpoints": frozenset({"workflow_runs"}),
    "L163-ulo":              frozenset({"global/ulo"}),
    "L-learning":            frozenset({"learning"}),
    "L-infinite-session":    frozenset({"infinite_session", "sessions"}),
    # R2-A7 — stores that had no Art. 17 path at all until 2026-09-07.
    "L-workflows":               frozenset({"workflows"}),
    "L-browser":                 frozenset({"browser"}),
    "L-vibe-checkpoints":        frozenset({"vibe"}),
    "L-datasource-connections":  frozenset({"datasource_connections"}),
    # R4-F4 — were claimed by permanent no-op stubs; now real purges.
    "L7-skill-forge":        frozenset({"skill-forge", "skills", "_shared",
                                        "global/skill-forge", "global/skills"}),
    "L24-data-snapshot":     frozenset({"global/data"}),
    # R4-F1 — the two stores the round-2 guard could not see.
    "L-cel-anchors":         frozenset({"cel_anchors"}),
    "L-acs-index":           frozenset({"global/acs"}),
    # R4-F1 — the remaining live stores the review listed for triage.
    "L-tenant-memory":       frozenset({"memory"}),
    "L-bridge-runtime":      frozenset({"bridges"}),
    "L-user-content":        frozenset({"files", "artifacts", "global/artifacts",
                                        "packages", "backups", "idea-pipeline",
                                        "plugin-builder", "measurement-week",
                                        "git_sync_repo", "cross_device"}),
    "L-compute-runs":        frozenset({"compute", "global/compute"}),
    "L-gateway-runs":        frozenset({"global/gateway"}),
    "L-rag":                 frozenset({"global/rag", "global/rag_hub"}),
    "L-flows":               frozenset({"global/flows", "global/workflows"}),
    "L-space":               frozenset({"global/space"}),
    "L-incidents":           frozenset({"global/incidents"}),
    "L-identity-directory":  frozenset({"global/scim"}),
    "L-activity-log":        frozenset({"global/chat_activity.jsonl"}),
    "L-voice":               frozenset({"voice"}),
    # R4-F1/F4: the format-specific handlers above own the sqlite tables and the
    # file shapes they were written for; this layer runs the generic attribution
    # rule over the SAME roots, so a record those narrower rules do not look at
    # — the exact shape R4-F4 found surviving under a claimed directory — cannot
    # survive a "completed" erasure either. ``experiments`` was claimed by
    # ``L-learning``, whose handler only ever opens ``<tenant>/learning``.
    "L-store-sweep":         frozenset({"learning", "experiments", "global/memory",
                                        "global/grants", "global/orgs",
                                        "global/social", "global/web_chat",
                                        "workflow_runs"}),
    "L-tenant-registries":   frozenset({
        "agents", "extensions", "custom-layers", "github-dlq",
        "global/concepts", "global/connectors", "global/console_panels",
        "global/datasources", "global/engines", "global/bridges",
        "global/mcp-tools", "global/proactive_ratelimit",
        "global/proactive_quiet_hours", "global/tde",
        "global/browser_attach_consent.json", "global/ce_stage_grades.json",
        "global/connectors.json", "global/connector_vault.json",
        "global/custom_layers.json", "global/delegation_budget.json",
    }),
}

#: Entries a writer may create under the tenant home that hold NO personal data
#: by construction (content-free chains, key material, config, generated code).
#: Exempting an entry here is a claim about its CONTENT — state the reason, and
#: state it honestly: this list is the only way out of the coverage guard.
NON_PERSONAL_DIRS: dict[str, str] = {
    "global/forge":    "hash-chained audit trail — content-free by the ADR-0129 floor, immutable (GDPR Art. 17(3)(b))",
    "audit.jsonl":     "core hash chain — content-free, immutable",
    "global/audit.jsonl": "tenant hash chain — content-free, immutable",
    "keys":            "instance/crypto key material, no subject data",
    "global/agent":    "BYOK instance keypair, no subject data",
    "global/erasure":  "erasure trail files (0600) — the record OF erasure",
    "forge":           "generated tools (code), no subject data",
    "plugins":         "plugin state/config, no subject data",
    "global/tenant.corvin.yaml": "operator config",
    "tenant.corvin.yaml": "operator config",
    "global/audit_layers": "ADR-0124 audit-layer configuration, no subject data",
    "global/auth":     "OIDC issuer trust (issuer URLs + JWKS) — relying-party config, no subject records",
    "cowork":          "persona definitions/cache — operator config, no subject data",
    "datasource_adapters": "exported AWP datasource adapter code, no subject data",
    "compute_backends": "exported AWP compute backend code, no subject data",
    "federated-models": "model artefacts (weights/metadata), no subject data",
    "global/compute_settings.json": "operator compute limits, no subject data",
    "global/imagegen-disclosure.json": "EU AI Act disclosure state (per-tenant flag), no subject data",
    "global/model_catalog_cache.json": "upstream model catalogue cache, no subject data",
    "global/features.json": "console feature-flag overlay — operator config, no subject data",
    ".wf_create.lock": "advisory lock file, no content",
}

#: Entries that DO hold personal data but carry no per-subject attribution.
#: :class:`UnattributableStoreHandler` reports each one that is present and
#: non-empty as ``SKIPPED / not_erasable`` so the orchestrator never says
#: "completed" over a store it did not touch. Moving an entry here is an
#: admission, not an exemption — it belongs in COVERED_DIRS the moment the
#: writer gains an identity field or a subject-derived filename.
UNATTRIBUTABLE_DIRS: dict[str, str] = {
    "global/acs_tmp": (
        "acs_runtime.py writes the assembled system prompt to an mkstemp-named "
        ".corvin-sysprompt-*.txt before spawning the engine. Plain text, no "
        "identity field, no subject-derived name, and stale files survive the "
        "spawn — attribution is impossible; the fix is a TTL sweep at the writer."
    ),
}


#: ``(layer_id, store-name, item-name)`` for every claim group served by the
#: generic :class:`TenantStoreHandler`. The entries themselves come from
#: :data:`COVERED_DIRS`, so the map and the chain cannot drift apart — a claim
#: without a handler (or a handler without a claim) fails the coverage guard.
_GENERIC_STORE_LAYERS: tuple[tuple[str, str, str], ...] = (
    ("L-tenant-memory",      "tenant memory",        "memory record"),
    ("L-bridge-runtime",     "bridge runtime",       "bridge runtime file"),
    ("L-user-content",       "user content",         "user content item"),
    ("L-compute-runs",       "compute run",          "compute run record"),
    ("L-gateway-runs",       "gateway run",          "gateway run record"),
    ("L-rag",                "RAG index",            "RAG record"),
    ("L-flows",              "flow",                 "flow record"),
    ("L-space",              "space profile",        "space record"),
    ("L-incidents",          "incident",             "incident record"),
    ("L-identity-directory", "identity directory",   "directory entry"),
    ("L-activity-log",       "chat activity log",    "activity entry"),
    ("L-voice",              "voice runtime",        "voice record"),
    ("L-tenant-registries",  "tenant registry",      "registry entry"),
    ("L-store-sweep",        "layer store",          "stored record"),
)


# ── default chain factory ────────────────────────────────────────────


def real_handler_chain(tenant_id: str = "_default") -> list:
    """Return the default chain of *real* per-layer handlers for
    ``corvin_erasure.register_handler``.

    Includes:
      * L28RecallHandler — fully implemented (SQL delete)
      * L28UserModelHandler — fully implemented (FS purge, ADR-0072 V-001)
      * L33ArtifactHandler — fully implemented (FS purge)
      * WebChatHandler — fully implemented (FS purge: ADR-0194 voice archive + turn log)
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
        WebChatHandler(tenant_id=tenant_id),
        L39SocialParticipationHandler(tenant_id=tenant_id),
        L41GrantHandler(tenant_id=tenant_id),
        L42OrgHandler(tenant_id=tenant_id),
        WorkflowCheckpointHandler(tenant_id=tenant_id),
        LearningEventHandler(tenant_id=tenant_id),      # F-A9: ADR-0314 learning store
        InfiniteSessionHandler(tenant_id=tenant_id),    # F-A9: session state + checkpoints
        # R2-A7: live stores that reported COMPLETED with survivors.
        WorkflowChatHandler(tenant_id=tenant_id),           # workflows/*.chat.jsonl
        BrowserSessionHandler(tenant_id=tenant_id),         # browser/sessions/<id>/
        VibeCheckpointHandler(tenant_id=tenant_id),         # vibe/checkpoints/*.json
        DatasourceConnectionHandler(tenant_id=tenant_id),   # datasource_connections/
        L7SkillForgeHandler(tenant_id=tenant_id),          # R4-F4: was a no-op stub
        L24DataSnapshotHandler(tenant_id=tenant_id),        # R4-F4: was a no-op stub
        # R4-F1: stores the round-2 guard's writer list could not see.
        CELAnchorHandler(tenant_id=tenant_id),              # cel_anchors/
        ACSGlobalIndexHandler(tenant_id=tenant_id),         # global/acs/runs/
        IdentityMappingHandlerBase(),
    ]
    # R4-F1: the remaining live tenant-home stores, all erased by the same
    # documented attribution rule — one data-driven handler per claim group.
    chain.extend(
        TenantStoreHandler(layer_id=lid, entries=tuple(sorted(COVERED_DIRS[lid])),
                           store=store, item=item, tenant_id=tenant_id)
        for lid, store, item in _GENERIC_STORE_LAYERS
    )
    chain.append(UnattributableStoreHandler(tenant_id=tenant_id))
    if _ulo_handler is not None:
        chain.append(_ulo_handler)
    return chain
