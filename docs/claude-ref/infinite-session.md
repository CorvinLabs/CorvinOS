# Infinite Session Engine (ADR-0540–0545)

Persistence of task state across session boundaries: append-only, hash-chained
snapshots per task; signed session bridges; a keyed transaction log for
reverts; EMA drift detection; and a console dashboard that reads ONLY through
the same store.

**Status (2026-09-07, after adversarial hardening):** the engine, the API and
the dashboard page are implemented and tested against the real store. The
engine has **no production producer yet** — nothing in the console writes a
snapshot when a task turn finishes (see § Producer): the console task worker
(`task_worker_pool.py::_snapshot_task_turn`) chains one content-free snapshot
per finished `/task` turn (completed, failed and cancelled alike).

## Modules — `core/infinite_session/`

| Module | Role |
|---|---|
| `paths.py` | ONE place for id validation (`ID_PATTERN = ^[A-Za-z0-9_.-]{1,128}$`, no `..`), tenant roots (`tenant_root(tenant_id)` = `<corvin_home>/tenants/<tenant>/infinite_session/`, via `core.paths.tenant.corvin_home()`), `safe_child()` (resolve + `is_relative_to` before any open) and `core_audit()` (core hash-chained writer, fail-closed, allowlists registered via `security_events.register_event_allowlist`). |
| `snapshot_schema.py` | Frozen `Snapshot` (content hash, prev-hash link, PII gate, 50 MB cap, strict ids), `SnapshotMetadata` (+ `seq`). |
| `event_store.py` | **The only persistence layer.** `EventStore(tenant_id)` is tenant-bound; layout `snapshots/<task_id>/{index.json,<snapshot_id>.json}`; `write_snapshot` is audit-FIRST (`infinite_session.snapshot_created` on the core chain; no commit → nothing on disk), atomic (tmp → fsync → rename), locked, append-only, and refuses a wrong tenant, a duplicate id, or a `prev_snapshot_hash` ≠ chain head. `verify_snapshot_chain` re-hashes every snapshot. `snapshot_task_state()` is the producer helper. |
| `crypto_binding.py` | HMAC-SHA256 over canonical JSON, one key per tenant at `<tenant_root>/keys/signing.key` (0600). `sign_payload/verify_payload` (dict), `hmac_bytes/verify_bytes` (raw). Verification never creates a key. |
| `session_bridger.py` | `SessionBridgeEvent` signed over ALL fields minus `signature`; `resume_from_bridge` verifies signature, tenant/task binding, and that the referenced snapshot exists with the same hash — then returns the recovered `state_dict`. Bridges must point at persisted snapshots. |
| `rollback_manager.py` | Tenant-bound WAL + JSONL log; every entry (COMMITTED, ROLLED_BACK **and** FAILED) carries `prev_mac`/`mac` = HMAC(tenant key). `recover_pending()` (at construction, grace window 60 s; `max_age_s=0` forces) closes abandoned WAL entries as ROLLED_BACK. Audit callbacks carry state HASHES only. |
| `drift_detector.py` | `assess_series` / `assess_states` (pure, used by the API on every read), `check_drift` gate (persists `DriftAlert`), `create_revert_button` (rolls back the latest transaction of the alert's config path). |
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
| `POST /api/infinite-session/task/{task_id}/revert` | **`require_csrf`** | appends a `rollback_recovery` snapshot with the target's state chained onto the head; `RollbackManager` begin/commit; `console.action_performed` (`sid_fingerprint`, never user id). 404 unknown target, 409 already-at-head / broken chain, 400 body/path mismatch, 422 bad id. The free-text `reason` is never persisted. |
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
invoked from `TaskWorkerPool._execute_task` in both the `rc == 0` and the
failed/cancelled branch, right after the `task.spawn_terminal` audit event.
The payload is content-free by construction — `status`, `exit_code`,
`duration_ms`, `event_count`, an 8-char `chat_key_prefix` and the
`result_sha256` of the model output; the output text itself is never
snapshotted. A snapshot failure never fails the task, but it is logged at
ERROR with the task id (never silent). Proof through the real boundary:
`core/console/tests/test_task_worker_pool_argv.py::test_pool_writes_infinite_session_snapshot_for_finished_turn`
drives the real pool against a fake `claude` binary and reads the chain back
through `EventStore` and the console route.

`TaskManager.record_event` (`task_manager.py`) remains the per-turn event log;
snapshots are per process, not per streamed `result` event.

## Tests

- `tests/skills/test_infinite_session_phase_a.py` — schema, parser, EventStore, adversarial (traversal, tenant, tamper, concurrency), plan→snapshots→restart→recover.
- `tests/skills/test_infinite_session_phase_b.py` — crypto (0600 keys, canonical signing, rotation), bridger (whole-event tamper matrix, snapshot swap), verifier.
- `tests/skills/test_infinite_session_phase_c.py` — rollback (keyed chain, FAILED entries chained, forged-key rejection, WAL replay, concurrency), EMA, drift, revert button.
- `tests/skills/test_infinite_session_phase_d.py` — all five routes over the real router via TestClient (auth deps overridden only): traversal, cross-tenant, CSRF, audit content-freeness, tampered chain → 500/degraded.
- `tests/skills/test_infinite_session_live_llm.py` — `@pytest.mark.live`, `CLAUDE_LIVE_E2E=1`: two real `claude -p --model haiku` turns → chained snapshots on the core chain → new store instance recovers the head.

Run: `.venv/bin/python -m pytest -q -o addopts="" -p no:cacheprovider tests/skills/test_infinite_session_phase_*.py`

## Ops

- `scripts/verify_infinite_session.py` — real round trip (write → chain → restart → recover) under a temp `CORVIN_HOME`; exit 1 on any failure.
- `deploy/infinite_session_deploy.sh` — canary state machine; `build` runs the phase tests + the verify script; health metrics are read from `INFINITE_SESSION_METRICS_FILE` (JSON) and the script refuses to promote without one (no simulated values).
- Config: none. Compliance mechanisms (audit-first, tenant binding, id validation) have no switch.
