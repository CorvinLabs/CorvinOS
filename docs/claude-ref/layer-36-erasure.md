# Layer 36 — GDPR Art. 17 Erasure Orchestrator (ref doc)

Companion to the short CLAUDE.md section.

→ **ADR:** Corvin-ADR: decisions/0045-L36-erasure-orchestrator.md
→ **Module:** `operator/bridges/shared/erasure_orchestrator.py`
→ **Tests:** `operator/bridges/shared/test_erasure_orchestrator.py`

---

## What this layer does

Coordinates GDPR Art. 17 erasure across every layer that holds
subject data. One call (`corvin-erasure <subject_id>` or
`ErasureOrchestrator.execute()`) reaches L7, L24, L28, L33, and the
identity-mapping store. Each layer reports independently; failures
are captured and audited per-layer; aggregate status describes the
overall outcome.

| Layer | What it holds | Erasure action |
|---|---|---|
| L7 skill-forge | User-scope skills referencing subject | Purge skill files + slot mirror |
| L24 data-snapshot | Snapshots that referenced subject in metadata | Purge snapshots |
| L28 recall | FTS5 row per chat turn | `DELETE FROM turns WHERE user_id = ?` |
| L28.2 user-model | Distilled JSON at `<tenant>/global/memory/user_model/*.json` | File deletion |
| L33 artifacts | Files in `<session>/artifacts/` and `<global>/artifacts/` | Unpinned: purge; pinned: operator-ACK required |
| Web chat (ADR-0194) | Voice archive at `<session>/voice/<hash>.<ext>` (+ `-fNN` read-aloud segments) — synthesised speech of every assistant reply — user uploads at `<session>/attachments/`, background-task results at `<session>/compute_inbox/` (carry the user's task text as `description`), the turn log at `<global>/web_chat/sessions/<sid>.turns.jsonl`, and the session meta file `<sid>.json` beside it (+ `.json.tmp` crash leftovers), whose `title` is LLM-derived from the user's first message | `WebChatHandler` deletes all of them. Note `voice/`, `attachments/` and `compute_inbox/` are SIBLINGS of `artifacts/` and the turn log + meta live outside the session dir, so none are reachable by `L33ArtifactHandler`. Known gap (needs its own ADR): engine-side transcripts under `~/.claude/projects/<slug>/*.jsonl` |
| Workflow checkpoints (ADR-0188 M5) | Paused Task-Engine runs at `<tenant>/workflow_runs/<run_id>.json` (raw `chat_id`/`approver` + full `inputs`/`state`) | `WorkflowCheckpointHandler` deletes any checkpoint (+ `.claimed` sidecar) whose `chat_id`/`approver` matches subject_id |
| L16 identity-mapping | subject_id → real-world identity link | Delete the mapping (audit chain preserved per EDPB) |
| Learning (ADR-0314, F-A9) | `<tenant>/learning/events/*.jsonl` partitions (via `EventStore.erase_user_events`: atomic rewrite + counts-only tombstone) and any `learning/**/*.jsonl` side store whose records name the subject | `LearningEventHandler` |
| Infinite session (F-A9) | `<tenant>/infinite_session/**` (snapshots, rollback WAL/log, drift, bridges, verification logs) and `<tenant>/sessions/<subject>/checkpoints` — attributed by directory name or by a `session_id`/`user_id`/`chat_key`… field | `InfiniteSessionHandler` |

**Coverage guard (F-A9):** `erasure_handlers.COVERED_DIRS` maps every handler to
the tenant-home directories it claims; `NON_PERSONAL_DIRS` lists the
content-free ones (the hash chain, key material, config). `tests/security/
test_erasure_coverage_guard.py` boots the reachable writers into a temp home and
fails on any created directory that neither claims — a new persistent store
cannot ship without an Art. 17 path.

**CCC `/erase` (F-A9):** `corvin_console.chat_router._route_erasure` runs the
REAL `ErasureOrchestrator` (real chain + stub backfill, exactly like
`corvin-erasure run`) in a worker thread; the result carries the request id and
per-layer statuses (never the subject id). The "queued" placeholder is gone.

---

## Design tension (resolved in ADR)

Art. 17 says "erase". Art. 17(3)(b) carves out audit logs. The L16
hash chain is tamper-evident; rewriting sealed segments would break
chain integrity. **Resolution: pseudonymous subject_id everywhere.**
The audit chain stores `subject_id="user_42"` — an opaque pseudonym.
The Art. 17 mechanism is to delete the identity-mapping (the
`subject_id → "alice@example.com"` link) and the content stores.
Without the mapping, the chain's pseudonyms are no longer traceable
to a person.

This matches EDPB guidance: pseudonymisation is a sufficient Art. 17
measure when full deletion conflicts with Art. 30 / 32 obligations.

---

## Core API

```python
from erasure_orchestrator import (
    ErasureRequest,
    ErasureLayerResult,
    ErasureResult,
    ErasureOrchestrator,
    ErasureHandler,      # Protocol
    LayerStatus,         # enum: APPLIED | SKIPPED | FAILED
    OverallStatus,       # enum: COMPLETED | PARTIAL | FAILED
    StubHandler,
    builtin_stub_chain,
    validate_subject_id,
    make_forge_audit_writer,
)
```

### Constructing the orchestrator

```python
from pathlib import Path
from erasure_orchestrator import (
    ErasureOrchestrator, ErasureRequest, make_forge_audit_writer,
)

orch = ErasureOrchestrator(
    trail_dir=Path(corvin_home) / "tenants" / tid / "global" / "erasure",
    audit_writer=make_forge_audit_writer(audit_path),
)
# Register real per-layer handlers as they ship:
orch.register_handler(L28RecallHandler(...))
orch.register_handler(L33ArtifactsHandler(...))
# Until real handlers exist, the builtin chain provides audit-visible stubs:
for stub in builtin_stub_chain():
    orch.register_handler(stub)
```

### Running an erasure

```python
req = ErasureRequest(
    subject_id="user_42",
    requester="dpo@example.com",
    scope="all",
    notes="erasure request received 2026-05-19 via support ticket",
)
result = orch.execute(req)

if result.overall_status == OverallStatus.FAILED:
    log.critical("erasure failed entirely: %s", result.to_dict())
elif result.overall_status == OverallStatus.PARTIAL:
    log.warning("erasure partial: %d applied, %d failed",
                result.applied_count, result.failed_count)
```

`ErasureRequest.notes` is preserved on the trail file but **not**
in the audit chain — the audit allow-list refuses free-form fields.

---

## subject_id shape

Enforced regex: `^[A-Za-z0-9.:_\-]{1,128}$`

Accepts: `user_42`, `User-42`, `user.42`, `discord:12345`, hex hashes.
Rejects: `alice@example.com`, `Alice Smith`, `../etc/passwd`,
control chars, anything > 128 chars.

The colon is admitted so `bridge:chat_key` style identifiers (e.g.
`discord:12345`) work directly as subject identifiers — the
`@`-sign check still rejects email-shaped input.

This is the structural defence against accidental PII landing in
the `subject_id` field of audit events. Operators who pass raw
email get a `ValueError` and must hash / alias first.

---

## ErasureHandler contract

```python
class ErasureHandler(Protocol):
    layer_id: str

    def purge(self, subject_id: str, request_id: str) -> ErasureLayerResult: ...
```

Invariants:

* **MUST NOT raise** on common cases (subject not found, layer
  disabled). Return `SKIPPED` instead.
* **MAY raise** on infrastructure failures (DB unreachable,
  permission denied). The orchestrator captures the traceback and
  marks `FAILED`.
* **MUST return** an `ErasureLayerResult`. Wrong-type return is
  coerced to `FAILED` with `reason="handler returned <type>,
  expected ErasureLayerResult"`.
* **SHOULD set** `layer_id` matching the registered handler's
  `layer_id`. Mismatch is corrected by the orchestrator (registered
  id wins) with a corrective note appended to `reason`.

Example handler:

```python
@dataclass
class L28RecallHandler:
    layer_id: str = "L28-recall"
    db_path: Path

    def purge(self, subject_id: str, request_id: str) -> ErasureLayerResult:
        t0 = time.time()
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.execute(
                "DELETE FROM turns WHERE user_id = ?", (subject_id,),
            )
            n = cur.rowcount
            conn.commit()
            conn.close()
        except sqlite3.OperationalError as e:
            raise  # orchestrator marks FAILED with traceback
        return ErasureLayerResult(
            layer_id=self.layer_id,
            status=LayerStatus.APPLIED if n > 0 else LayerStatus.SKIPPED,
            count=n,
            duration_ms=int((time.time() - t0) * 1000),
        )
```

---

## Aggregate-status logic

```
all APPLIED | SKIPPED              → COMPLETED
mix of APPLIED/SKIPPED + FAILED    → PARTIAL
all FAILED OR no handlers          → FAILED
```

PARTIAL is the most interesting state: the DPO sees per-handler
detail in `result.per_layer` and decides retry vs accept.

---

## Audit contract

Five event types on the L16 hash chain:

```jsonc
// erasure.requested (WARNING) — emitted FIRST
{
  "event_type": "erasure.requested", "severity": "WARNING",
  "details": {
    "request_id": "er-abc123def456",
    "subject_id": "user_42",
    "requester": "dpo@example.com",
    "scope": "all"
  }
}

// erasure.applied (INFO) — per layer
{
  "event_type": "erasure.applied", "severity": "INFO",
  "details": {
    "request_id": "er-abc123def456",
    "subject_id": "user_42",
    "layer_id": "L28-recall",
    "status": "applied",
    "count": 47,
    "code": "deleted",
    "duration_ms": 12
  }
}

// erasure.skipped (INFO) — per layer when no data found
{
  "event_type": "erasure.skipped", "severity": "INFO",
  "details": {
    "request_id": "er-abc123def456",
    "subject_id": "user_42",
    "layer_id": "L24-data-snapshot",
    "status": "skipped",
    "count": 0,
    "code": "store_empty"
  }
}

// erasure.failed (CRITICAL) — per layer on handler exception
{
  "event_type": "erasure.failed", "severity": "CRITICAL",
  "details": {
    "request_id": "er-abc123def456",
    "subject_id": "user_42",
    "layer_id": "L33-artifacts",
    "status": "failed",
    "code": "store_error",
    "error_type": "OperationalError"
  }
}

// erasure.completed (WARNING) — emitted LAST
{
  "event_type": "erasure.completed", "severity": "WARNING",
  "details": {
    "request_id": "er-abc123def456",
    "subject_id": "user_42",
    "overall_status": "partial",
    "applied_count": 4,
    "failed_count": 1
  }
}
```

Allow-list keys: `request_id`, `subject_id`, `requester`, `scope`,
`layer_id`, `status`, `count`, `code`, `error_type`, `duration_ms`,
`overall_status`, `applied_count`, `failed_count`. The audit chain carries a
controlled `code` (`deleted` / `store_absent` / `store_empty` /
`not_applicable` / `store_error` / `handler_contract_error`) and a bare
`error_type` class name — **never** the free-form `reason` (which may contain
absolute paths or exception text). The full descriptive `reason` lives only in
the mode-0600 trail file. A `_emit`-boundary scrubber rejects, fail-closed, any
audit value containing a path separator or exception-shaped text. **Never** notes,
free-form descriptors, layer-internal state, PII content.

---

## Trail persistence

`<tenant>/global/erasure/<request_id>.json` per request. Atomic
rename + flock to avoid partial writes under concurrent DPO calls.
Mode 0600.

Contains full request + per-layer results in JSON form. A DPO can
audit "what was actually deleted for subject X" weeks later without
needing to re-walk the audit chain.

---

## Attribution and the snapshot seam (R2-A7, 2026-09-07)

**`_SUBJECT_KEYS` IS the attribution rule.** A record naming the subject under a
key that is not in that tuple survives the purge while the orchestrator reports
`COMPLETED` — the worst combination, because the operator ends up holding a
signed statement that the data is gone. The review found the subject under
`speaker` in transcript-shaped stores; the tuple now also covers `author`,
`actor`/`actor_id`, `sender`, `from_user`, `created_by`, `requested_by`,
`participant`, `user`, `username`, `account_id`, `owner_id`, `session_key` and
`subject`. Adding a key is cheap; omitting one fails silently.

**Erasing a snapshot is a lawful chain discontinuity.** The infinite-session
index (`<task_id>/index.json`) is an ordered `SnapshotMetadata` list whose `seq`
must be contiguous — `EventStore.verify_snapshot_chain` reports a hole as
"Chain gap at seq N", which is what a tamper looks like too. So on erasure the
index is rewritten entry-wise (the old filter read `m["path"]`; the metadata key
is `file_path`, so it never dropped anything and the index kept naming deleted
snapshots) and the gap is recorded as `erasure.chain_seam` with the removed
sequence numbers. The seam record is content-free and deliberately does NOT
carry the subject id — writing the erased identifier into an append-only chain
would undo the erasure it documents.

**A FILENAME is attribution too (R3, 2026-09-07).** Attribution used to have two
routes: a DIRECTORY named exactly after the subject, or a JSON/JSONL PAYLOAD
naming it under a `_SUBJECT_KEYS` key. A file whose only mention of the subject is
its own name — `<subject>.json`, `snapshot_<subject>.jsonl`,
`<subject>-profile.json` — matched neither and survived a run the orchestrator
then reported `COMPLETED`. `_name_names_subject()` is the third route and is
**token-bounded, never a bare substring**: the id must be the whole name, the
whole stem, or a run delimited by `._-:@ ` — because over-matching deletes ANOTHER
subject's data, which is its own Art. 5 breach (`u1` must not take `u12.json`).

**Every entry-wise rewrite is crash-atomic.** JSONL line filters and the
infinite-session `index.json` rewrite went through `tmp.write_text()` +
`os.replace()`. The rename is atomic; the DATA was never flushed, so a crash
between write and writeback left the new name pointing at a truncated file — and a
half-rewritten snapshot index is indistinguishable from tampering
(`verify_snapshot_chain` reports a chain gap either way). The staging name was
also FIXED (`index.json.erasing`), so two concurrent erasures clobbered each
other. `_atomic_replace_text()` is now the single writer: pid-unique tmp → write →
`fsync` the file → `os.replace` → `fsync` the directory, with the tmp removed on
any failure so the original always stands.

**The coverage guard boots the real writers.** `tests/security/
test_erasure_coverage_guard.py` used to hand-seed `mkdir`s mirroring what the
writers were believed to do, so it could only confirm its author's own picture —
which is why four live stores went uncovered. It now drives the production code
paths (the workflows route helpers, `CheckpointManager.save`, `RollbackManager`,
the browser and datasource path resolvers) and fails on any directory under the
tenant home that no handler claims.

## Built-in stubs

`StubHandler` returns `SKIPPED` with a reason describing which real
handler is not yet registered. `builtin_stub_chain()` covers the
five expected layers (L28-recall, L33, L7, L24, L16-identity-mapping).
Note: `L28UserModelHandler` is now fully implemented and registered in
`real_handler_chain()` — it is NOT covered by the stub chain.

Use case: deploy L36 *before* the per-layer real handlers ship.
Operator who runs `corvin-erasure user_42` gets a `COMPLETED`
result with `SKIPPED` entries and clear audit events naming
each unregistered handler. The gap is **visible** rather than
silent.

---

## Wiring status

* **M4 (done):** Module + tests + ADR + ref doc. Standalone; opt-in.
* **M4.5 (done):** `corvin-erasure <subject_id>` CLI thin wrapper —
  this is the supported DPO entry point. (The former admin-UI route
  `/v1/admin/tenants/{tid}/erasure` was removed with the admin plugin.)
* **Per-layer handlers (partial — done where feasible):**
  `operator/bridges/shared/erasure_handlers.py` ships:
  * `WorkflowChatHandler` (R2-A7) — `<tenant>/workflows/`: the authoring
    transcript `<wid>.chat.jsonl` (verbatim user turns), the run logs under
    `<wid>/runs/`, and the whole workflow when its `.meta.json` names the
    subject as author/owner
  * `BrowserSessionHandler` (R2-A7) — `<tenant>/browser/sessions/<session_id>/`,
    the per-session Chromium user-data dir (cookies, local storage, cached
    pages); attributed by directory name, which is the session id
  * `VibeCheckpointHandler` (R2-A7) — `<tenant>/vibe/checkpoints/*.json`
  * `DatasourceConnectionHandler` (R2-A7) — `<tenant>/datasource_connections/*.json`
  * `L28RecallHandler` (full SQL DELETE)
  * `L28UserModelHandler` (full FS purge, ADR-0072 V-001) — deletes distilled user model JSON files for the subject
  * `L33ArtifactHandler` (full FS purge of unpinned session artifacts)
  * `WebChatHandler` (full FS purge of the ADR-0194 per-turn voice archive
    under `<session>/voice/` plus the turn log under
    `<global>/web_chat/sessions/`). Added 2026-07-16 after an adversarial
    review found the archive was erased by NO handler: `voice/` is a sibling
    of `artifacts/`, so an Art. 17 run reported APPLIED/SKIPPED across all
    twelve layers — writing a successful erasure receipt into the hash-chained
    audit log — while the synthesised speech of the entire conversation
    survived on disk. Reproduced before fixing.
  * `WorkflowCheckpointHandler` (full FS purge of paused Task-Engine
    checkpoints under `<tenant>/workflow_runs/`, matched on
    `chat_id`/`approver`, ADR-0188 M5)
  * `L7SkillForgeHandler` + `L24DataSnapshotHandler` as documented
    stubs (operator subclasses / replaces)
  * `IdentityMappingHandlerBase` for the operator-owned subject_id ↔
    identity mapping

  The CLI registers `real_handler_chain()` automatically; `--use-stubs`
  keeps the M4-shipped stub-only mode.
* **Future:** real implementations for L7 + L24 + the
  identity-mapping subclass land alongside the respective layer's
  per-tenant schema work.

### subject_id shape

Regex: `^[A-Za-z0-9.:_\-]{1,128}$`. Admits
`<bridge>:<chat_key>` style identifiers directly (the colon was
added so L28RecallHandler / L33ArtifactHandler can consume
operator-typed `discord:12345` without a separate mapping step).
Still rejects email shape (`@`), spaces, slashes, `..` — the
structural defence against accidental PII in audit details holds.

---

## Must NOT do

* Don't redact sealed audit segments — pseudonymisation is the Art. 17
  mechanism, not deletion.
* Don't put `ErasureRequest.notes` or any free-form descriptor in
  audit `details` — allow-list rejects at emission.
* Don't accept raw email or name as `subject_id` — regex enforces
  pseudonymous shape.
* Don't make `erasure.failed` advisory; CRITICAL severity with
  overall_status carrying the consequence.
* Don't run an erasure handler across tenants — ADR-0007 silo.
* Don't make the trail file world-readable — mode 0600 enforced.
* Don't auto-retry on `FAILED` — DPO decides; orchestrator records.
* Don't `import anthropic` (CI lint).

---

## Tests

```bash
python3 operator/bridges/shared/test_erasure_orchestrator.py
```

33 tests covering:

* `validate_subject_id` (pseudonym accept, PII shape reject,
  length cap, non-string).
* `ErasureRequest` (auto request_id, requester required, scope
  required, subject_id validation).
* Happy path two handlers (overall COMPLETED, audit emission
  order, severities, trail file mode 0600).
* Failure paths (raising handler caught + CRITICAL audit; all
  failed → FAILED; no handlers → FAILED; bad return coerced;
  mis-attributed layer_id corrected).
* Skipped status (INFO event).
* Duplicate handler registration raises.
* Audit allow-list (smuggled keys rejected; `notes` field NOT in
  audit despite being in request).
* `_aggregate_status` (every combination).
* `StubHandler` + `builtin_stub_chain` covers expected layers.
* `L28UserModelHandler` (APPLIED when model file exists, SKIPPED when absent, verified in chain).
* CI lint (no `import anthropic`).
