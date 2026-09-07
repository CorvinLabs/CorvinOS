# Infinite Session Engine (ADR-0540–0545)

Persistence of task state across session boundaries: append-only, hash-chained
snapshots per task; signed session bridges; a keyed transaction log for
reverts; EMA drift detection; and a console dashboard that reads ONLY through
the same store.

**Status (2026-09-07, after round-4 adversarial hardening — ADR-0651):** the
engine, the API and the dashboard page are implemented and tested against the
real store, and the console task worker
(`task_worker_pool.py::_snapshot_task_turn`) is the production producer: it
chains content-free snapshots for every `/task` turn — one `running` record
right after the spawn and one terminal record on every outcome (completed,
failed, cancelled, denied, sanitiser-rejected, crashed).

**What this subsystem is:** tamper-evident, per-task **turn history** with an
audited, atomic revert.

**What it is NOT — read this before citing it:** it does **not** restore
session context after a restart. See § What session bridging does not do.

## Modules — `core/infinite_session/`

| Module | Role |
|---|---|
| `paths.py` | ONE place for id validation (`ID_PATTERN = ^[A-Za-z0-9_.-]{1,128}$`, no `..`), tenant roots (`tenant_root(tenant_id)` = `<corvin_home>/tenants/<tenant>/infinite_session/`, via `core.paths.tenant.corvin_home()`), `safe_child()` (resolve + `is_relative_to` before any open) and `core_audit()` (core hash-chained writer, fail-closed, allowlists registered via `security_events.register_event_allowlist`). |
| `snapshot_schema.py` | Frozen `Snapshot` (content hash, prev-hash link, PII gate, 50 MB cap, strict ids), `SnapshotMetadata` (+ `seq`). |
| `event_store.py` | **The only persistence layer.** `EventStore(tenant_id)` is tenant-bound; layout `snapshots/<task_id>/{index.json,<snapshot_id>.json}`; `write_snapshot` is audit-FIRST (`infinite_session.snapshot_created` on the core chain; no commit → nothing on disk), atomic (tmp → fsync → rename), locked (bounded, non-blocking — see § Producer), append-only, and refuses a wrong tenant, a duplicate id, or a `prev_snapshot_hash` ≠ chain head. It **signs** every snapshot (`sign_snapshot`, idempotent) and `verify_snapshot_chain` re-hashes AND re-MACs every snapshot — see § Chain integrity. `snapshot_task_state()` is the producer helper. |
| `crypto_binding.py` | HMAC-SHA256 over canonical JSON, one key per tenant at `<tenant_root>/keys/signing.key` (0600). `sign_payload/verify_payload` (dict), `hmac_bytes/verify_bytes` (raw). Verification never creates a key. |
| `session_bridger.py` | `SessionBridgeEvent` signed over ALL fields minus `signature`; `resume_from_bridge` verifies signature, tenant/task binding, and that the referenced snapshot exists with the same hash — then returns the recovered `state_dict`. Bridges must point at persisted snapshots. |
| `rollback_manager.py` | Tenant-bound WAL + JSONL log; every entry (COMMITTED, ROLLED_BACK **and** FAILED) carries `prev_mac`/`mac` = HMAC(tenant key). `recover_pending()` (at construction, grace window 60 s; `max_age_s=0` forces) closes abandoned WAL entries as ROLLED_BACK. The log lock is bounded, non-blocking — see § Producer. Audit callbacks carry state HASHES only. |
| `drift_detector.py` | `assess_series` / `assess_states` (pure, used by the API on every read), `check_drift` gate (persists `DriftAlert`), `create_revert_button` (rolls back the latest transaction of the alert's config path). `assess_states` scores ONLY `state["config"]` — see § Drift. |
| `audit_verification.py` | Per-task verification: snapshot chain + tenant isolation + every bridge signature + bridge→snapshot hash; results under `<tenant_root>/verification/`. |
| `ema_smoother.py`, `task_def_parser.py` | Unchanged helpers (EMA maths; JSON-LD task definition → topologically sorted `ExecutionPlan`). |

Every root honours `CORVIN_HOME` (tests set it to a temp dir); nothing uses
`Path.home() / ".corvin"`.

## Console API — `routes/infinite_session_api.py` (mounted under `/v1/console`)

| Route | Auth | Reads/writes |
|---|---|---|
| `GET /api/infinite-session/tasks` | `require_session` | `EventStore.list_tasks/list_snapshots`, drift via `assess_states` |
| `GET /api/infinite-session/task/{task_id}/history` | `require_session` | full chain + `chain_valid` + per-snapshot drift |
| `GET /api/infinite-session/task/{task_id}/context-diff?from_checkpoint&to_checkpoint` | `require_session` | two snapshots → additions/removals/modifications |
| `POST /api/infinite-session/task/{task_id}/revert` | **`require_csrf`** | appends a `rollback_recovery` snapshot with the target's state chained onto the head, **inside** the rollback transaction (see § Atomic revert); `console.action_performed` (`sid_fingerprint`, never user id). 404 unknown target, 409 already-at-head / unverifiable chain, 400 body/path mismatch, 422 bad id, **503 `transaction_lock_busy`** when the rollback log lock is wedged. Every error response means the chain is unchanged. A `200` may carry `error: "transaction_log_append_failed"` — the revert stands, only the log append failed. The free-text `reason` is never persisted. |
| `GET /api/infinite-session/health` | `require_session` | chain verification per task + rollback-log MAC chain |

A "checkpoint id" in this API **is** a snapshot id. Ids are validated at the
Pydantic/`Path`/`Query` layer AND inside the store. Tenant = `rec.tenant_id`
only; a store bound to that tenant refuses everything else.

Frontend: `web-next/src/pages/infinite-session-dashboard.tsx` (registered in
`panels/registry.tsx` + `NAV_GROUPS` as "Session Manager"). Sends
`X-CSRF-Token` from `useAuth().session.csrf_token` on revert. Uses only the
primitives under `components/ui/` (Badge `ok|warn|danger`, plain `<table>`).

## Producer — the console task worker

`snapshot_task_state(tenant_id, task_id, state, phase_id="turn")` chains a
new snapshot onto the task's head and writes it audit-first. The production
call site is `core/console/corvin_console/task_worker_pool.py::_snapshot_task_turn`,
invoked from `TaskWorkerPool._execute_task`. The payload is content-free by
construction — `status`, `exit_code`, `duration_ms`, `event_count`, an 8-char
`chat_key_prefix`, the `result_sha256` of the model output and a `reason_code`
from the closed set `TURN_REASON_CODES`; the output text, the instruction and
any refusal string are never snapshotted. A snapshot failure never fails the
task, but it is logged at ERROR with the task id (never silent).

**Every transition is recorded (ADR-0651 §5).** Until round 4 only the two
post-subprocess branches called the producer, so a turn blocked by the L44
acceptable-use gate, or one that crashed, was absent from `/tasks`,
`/history` and the dashboard — a hole exactly where something went wrong. The
call sites now are:

| When | `status` | `reason_code` |
|---|---|---|
| right after `create_subprocess_exec` | `running` | `""` |
| `rc == 0` | `completed` | `""` |
| `rc != 0` | `failed` / `cancelled` | `""` |
| payload file missing | `failed` | `payload-missing` |
| instruction sanitisation refused | `denied` | `instruction-sanitization-failed` |
| pre-spawn gate denied | `denied` | `pre-spawn-gate-denied` |
| invalid `chat_key` | `denied` | `invalid-chat-key` |
| `task.spawn_started` did not commit | `denied` | `audit-refused` |
| `asyncio.CancelledError` | `cancelled` | `cancelled` |
| generic handler | `failed` | `worker-exception` |

**Audit-first is FAIL-CLOSED (ADR-0651 §3).** `_task_audit_emit` returns a
boolean and `_execute_task` REFUSES to spawn when `task.spawn_started` did not
commit. It used to swallow the failure and spawn anyway, which mattered for
every tenant but one: the L16 writer compares `tenant_id` against the PROCESS
tenant (`forge.tenants._current_tenant_id()` reads `CORVIN_TENANT_ID` and has
no per-request context) while the store binds to `task.tenant_id`. An `acme`
task therefore spawned, streamed and reached COMPLETED with no spawn record on
any chain. **Consequence to know about:** until the forge writer accepts an
explicit/per-request tenant, tasks for any tenant other than the process tenant
are refused (FAILED, exit 125) instead of running unaudited.

That guarantee is availability, not just error handling, so the store's per-task
index lock is **non-blocking by construction**: `EventStore._task_lock` takes
`flock(LOCK_EX | LOCK_NB)` and retries until `LOCK_TIMEOUT_SECONDS` (2 s), then
raises `SnapshotLockBusy`, which every caller turns into a returned
`(False, "snapshot lock busy …")` / `([], …)` / `(0, …)`. It was a plain
`flock(LOCK_EX)` with no timeout, taken twice per producer call, on the
completion path BEFORE `_notify_task_done` — a wedged holder stranded a finished
task with no notification, and no `try/except` can catch a hang (2026-09-07,
R3-B5). Regression: `tests/skills/test_infinite_session_lock_nonblocking.py`. Proof through the real boundary:
`core/console/tests/test_task_worker_pool_argv.py::test_pool_writes_infinite_session_snapshot_for_finished_turn`
drives the real pool against a fake `claude` binary and reads the chain back
through `EventStore` and the console route.

`RollbackManager._lock` had the SAME defect and is fixed the same way
(`LOCK_EX | LOCK_NB`, `LOCK_TIMEOUT_SECONDS` = 2 s, `RollbackLockBusy`). It is
taken by `recover_pending()` — which runs in `__init__` — and by
`commit_transaction`, both on the **console HTTP revert path**, so a wedged
holder hung an *operator request*, not a background task. Conversions:
`__init__` logs and skips recovery (best-effort housekeeping, never a
precondition); `commit_transaction` / `rollback_transaction` return
`(False, "rollback log lock busy …")`; the route maps that prefix
(`LOCK_BUSY_REASON_PREFIX`) to **503 `transaction_lock_busy`** and audits
`action_failed`, so the request always answers. Regressions:
`tests/skills/test_infinite_session_lock_nonblocking.py::TestRollbackManagerNeverBlocks`
and, through the real router,
`tests/skills/test_infinite_session_phase_d.py::test_revert_refuses_503_when_rollback_log_lock_is_wedged`.

`TaskManager.record_event` (`task_manager.py`) remains the per-turn event log;
snapshots are per process, not per streamed `result` event.

## Chain integrity — the snapshot MAC (ADR-0651 §2)

`content_hash` is `SHA256(state_dict)` and nothing else. Every stored snapshot
therefore also carries

    chain_mac = HMAC-SHA256(<tenant signing key>, canonical_json(record minus chain_mac))

over the WHOLE record — `snapshot_id`, `tenant_id`, `task_id`, `phase_id`,
`snapshot_type`, `timestamp`, `state_dict`, `content_hash`,
`prev_snapshot_hash`, `base_commit`, `worktree_path`. The key is the same
per-tenant `CryptoBinding` key the rollback log uses, so an attacker who can
edit the file cannot re-sign it. Without this, a middle snapshot could be
deleted and its successor re-pointed at its predecessor with no hash to
recompute anywhere, and `verify_snapshot_chain` still returned `(True, "")`
while `/health` reported `healthy`.

`EventStore.sign_snapshot` is idempotent (the MAC never covers itself);
`write_snapshot` signs before the audit-first write, so an unusable key refuses
the snapshot rather than leaving an audit record for a file that was never
written. `CryptoBinding._ensure_key_exists` writes the key atomically
(`mkstemp` → fsync → `os.link`) because it is now on a hot path.

**Legacy chains are not migrated.** A snapshot with no `chain_mac` reports
`unsigned snapshot (written before the chain MAC, ADR-0651)`: `chain_valid:
false` on `/history`, `degraded` on `/health`, `409 chain_invalid` on `revert`.
Re-signing records written before the scheme existed would launder exactly the
tampering the MAC detects. Reads still work, so the operator can inspect the
old chain and then delete
`<corvin_home>/tenants/<tenant>/infinite_session/snapshots/` — it holds turn
telemetry, not recoverable context.

**Residual, stated plainly:** truncating the tail of `index.json` is still
locally undetectable. The external anchor is the core audit chain, which
carries one `infinite_session.snapshot_created` per snapshot with its `seq` and
`content_hash`.

## Atomic revert — ordering is the guarantee (ADR-0651 §1)

`RollbackManager.commit_transaction` accepts
`apply_change: () -> (ok, reason)`, executed **inside** the log lock, after the
WAL check and before the COMMITTED append. The console revert passes the
snapshot append as that callable.

The snapshot chain is append-only and cannot be undone; the transaction log
can be compensated. So the un-undoable step must be the LAST fallible one. It
used to be the first: the endpoint appended, then committed, so a busy lock
answered 503 with the chain head already reverted, the WAL sweep later logged
that permanent change as `rolled_back — "transaction never committed"`, and the
operator's retry appended a second recovery snapshot.

Return contract of `commit_transaction`:

| Result | Meaning |
|---|---|
| `(True, None)` | applied and logged |
| `(True, reason)` | **applied**; only the log append failed. The route answers 200 with `error: "transaction_log_append_failed"` |
| `(False, reason)` | nothing applied, nothing committed — a retry is safe |

## Drift — what is scored (ADR-0651 §4)

`assess_states` scores ONLY the numeric leaves under a snapshot's `config` key
(`drift_detector.DRIFT_SERIES_ROOT`), and scales each series by its own
observed magnitude (`scale_series`) before `EMASmoother`'s absolute 0.15 /
0.10 thresholds apply. A series already inside 0–1 is untouched, so existing
normalised config series behave exactly as before.

Turn telemetry is not configuration. Scoring every numeric leaf meant
`duration_ms` (thousands, legitimately 2–5x between turns) was a series: two
ordinary successful turns showed CRITICAL with magnitude 2082 and the
dashboard's "consider a revert" banner was permanently on. A task whose
snapshots carry no `config` now reads NORMAL with the message
`no drift-tracked metrics ('config')`.

`assess_series` and `check_drift` are unchanged: they take a caller-supplied
series and keep the absolute thresholds they were designed for.

## What session bridging does not do

`SessionBridger.create_bridge` / `resume_from_bridge` are **called by nothing
in production** — the only references outside the package and outside tests are
in `scripts/verify_infinite_session.py`. And the snapshot payload is turn
telemetry: no conversation, no plan, no file or worktree state, and by design
not even the answer. **A killed session cannot be resumed from what is stored.**

Round 4 added the `running` snapshot so a kill mid-turn leaves *evidence* that
a turn started and never terminated. That is not context. A real resume would
additionally require: a serialisable definition of session context; somewhere
to hold it that is compatible with this subsystem's content-freedom rule (the
payload may never carry prompts or answers, so the conversation cannot simply
be snapshotted here); a producer that writes it at meaningful points inside a
turn; and a consumer on the chat/worker start path that calls
`resume_from_bridge` and injects the result. None of that exists. See the
ADR-0541 amendment of 2026-09-07.

## Tests

- `tests/skills/test_infinite_session_phase_a.py` — schema, parser, EventStore, adversarial (traversal, tenant, tamper, concurrency), plan→snapshots→restart→recover.
- `tests/skills/test_infinite_session_phase_b.py` — crypto (0600 keys, canonical signing, rotation), bridger (whole-event tamper matrix, snapshot swap), verifier.
- `tests/skills/test_infinite_session_phase_c.py` — rollback (keyed chain, FAILED entries chained, forged-key rejection, WAL replay, concurrency), EMA, drift, revert button.
- `tests/skills/test_infinite_session_phase_d.py` — all five routes over the real router via TestClient (auth deps overridden only): traversal, cross-tenant, CSRF, audit content-freeness, tampered chain → 500/degraded, wedged rollback lock → 503 **with the chain proven unchanged**, retry-after-503 applies exactly once, a refused append leaves a FAILED (never COMMITTED) transaction, mid-chain excision → `chain_valid: false` + `degraded`, unsigned legacy chain does not silently verify, real producer payload → NORMAL drift while a real `config` series → CRITICAL.
- `core/console/tests/test_task_worker_pool_infinite_session.py` — the REAL pool driving a fake `claude` binary: a foreign tenant's spawn is refused when its audit write is (and no `task.spawn_started` reaches any chain), the process tenant still spawns and yields `running` → `completed`, a gate-denied turn leaves a content-free terminal snapshot, a missing payload does too, and `reason_code` is a closed set. Includes the `live` real-`claude` E2E through the pool.
- `tests/skills/test_infinite_session_live_llm.py` — `@pytest.mark.live`, `CLAUDE_LIVE_E2E=1`: two real `claude -p --model haiku` turns → chained snapshots on the core chain → new store instance recovers the head.

Run: `.venv/bin/python -m pytest -q -o addopts="" -p no:cacheprovider tests/skills/test_infinite_session_phase_*.py`

## Ops

- `scripts/verify_infinite_session.py` — real round trip (write → chain → restart → recover) under a temp `CORVIN_HOME`; exit 1 on any failure.
- `deploy/infinite_session_deploy.sh` — canary state machine; `build` runs the phase tests + the verify script; health metrics are read from `INFINITE_SESSION_METRICS_FILE` (JSON) and the script refuses to promote without one (no simulated values).
- Config: none. Compliance mechanisms (audit-first, tenant binding, id validation) have no switch.
