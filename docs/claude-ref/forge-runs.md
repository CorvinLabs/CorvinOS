# Forge runs — one record, an event log and a read API (ADR-2253)

**Source:** `core/console/corvin_console/forge_runs.py` · API `routes/forge_runs_api.py` · routes that start runs:
`routes/forge_creator.py` (tool, plugin), `routes/skill_creator_api.py` (skill).
**Tests:** `core/console/tests/test_forge_run_{ownership,retention,events}_e2e.py`, `test_forge_runs_api_e2e.py`.

Skill, tool and plugin generation are minutes-long engine runs. Layer Forge has no run yet (planned, T-0151).

## The record

One JSON file per run, `<tenant>/global/forge_runs/run-<12 hex>.json` (0600, atomic replace). Fields: `kind, status,
phase, phases[], progress, message, engine, model, started_at, ended_at, artifact_ref{kind,id}, request_digest, error,
failure_code, owner, heartbeat_at, boot_id, resumes, interrupted` plus kind-specific results (`tool`, `plugin`, `skill`).
`model` is what the engine **reported**, else `unknown` — never a guess. `request_digest` is a 16-char hash of the
request, never the text. `resume` holds what a restart needs to rebuild the work (it contains the request and drafts) and
**never leaves the store**: clients only see `PUBLIC_RUN_FIELDS`.

## Ownership across hosts (D6)

The console and the gateway are two processes sharing these files. The owning writer refreshes `heartbeat_at` every
`HEARTBEAT_S` (10 s) and on every update. A record under a foreign `boot_id` is **orphaned only when its heartbeat is
older than `ORPHAN_AFTER_S`** (90 s — an initial value, see T-0146); a fresher one belongs to a live peer: it is returned
read-only, never cached, never resumed, never failed. `MAX_RUNNING_PER_TENANT` counts live owned runs from disk across
hosts. An over-cap resume fails the record with `failure_code: resume_over_cap` (a status GET is never 429); starting a
new run over the cap is 429.

## get_run is side-effect free until the caller is authorised (D9, D10)

`get_run(run_id, tenant, kinds=…)` checks tenant and kind **before** any resume (the tool/plugin status route passes
`{tool, plugin}`, `retry_tool` `{tool}`, the skill route `{skill}`). A resume is atomic: a resumer that raises leaves
`failed` / `resume_failed`, never `running`. `peek_run` is the read-only variant the run API uses.

## The event log (D2)

`<tenant>/global/forge_runs/<run_id>/events.jsonl`. One writer per process (owner only), `O_APPEND|O_NOFOLLOW`, 0600.
`{seq, ts, phase, level, type, message, data}`; `type ∈ progress | iteration | file_written | test_result | finding |
gate | intake | error | truncated`; `data` is scalars only; free text is bounded and **withheld whole** when it carries an
email/phone shape (the audit PII scan). Prompts and engine output are never stored. Caps (`MAX_EVENTS_PER_RUN` 2000,
`MAX_EVENT_BYTES_PER_RUN` 512 KiB — initial values) count what they drop and end with one `truncated` event, never an
`error`. Today progress is live; tests, written files, findings and iterations are emitted from the result at the end.

## Retention (D8)

`prune(tenant)`: a finished record goes with its run directory after 14 days; a record whose owner is alive is never
touched; a dead owner's `running` record is first marked `failed: interrupted` and removed one TTL later; symlinks and
foreign names are ignored. It runs when a run starts, when runs are listed, and once at import in a background thread
(`prune_all`, every host imports the module) so a quiet tenant is pruned too.

## API (read-only, session-gated, tenant-bound)

`GET /v1/console/forge/runs?kind=&status=&limit=` · `GET /forge/runs/{id}` · `GET /forge/runs/{id}/events?after=&limit=`.
Another tenant's id and an unknown id are both 404. Filters are closed vocabularies (422 otherwise).

## Audit

`forge_run.started` (`tenant_id, run_id, run_kind`) and `forge_run.finished` (`+ status, artifact_kind, artifact_id,
event_count, truncated`) are on the tenant chain, registered in `EVENT_SEVERITY` and `_EVENT_ALLOWLIST`. No request, file
name or message. `console.action_failed` gained an optional `run_id`.

## Synchronous skill runs

`POST /skill-creator/generate` with `async: false` runs the same `work` function and leaves the same truthful record
(real failing phase, error, engine, `run_id` on the response and both audit records).
