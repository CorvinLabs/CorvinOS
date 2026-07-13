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
| Workflow checkpoints (ADR-0188 M5) | Paused Task-Engine runs at `<tenant>/workflow_runs/<run_id>.json` (raw `chat_id`/`approver` + full `inputs`/`state`) | `WorkflowCheckpointHandler` deletes any checkpoint (+ `.claimed` sidecar) whose `chat_id`/`approver` matches subject_id |
| L16 identity-mapping | subject_id → real-world identity link | Delete the mapping (audit chain preserved per EDPB) |

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
  * `L28RecallHandler` (full SQL DELETE)
  * `L28UserModelHandler` (full FS purge, ADR-0072 V-001) — deletes distilled user model JSON files for the subject
  * `L33ArtifactHandler` (full FS purge of unpinned session artifacts)
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
