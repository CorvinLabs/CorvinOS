# Forge runs — one record, an event log and a read API (ADR-2253)

**Source:** `core/console/corvin_console/forge_runs.py` · API `routes/forge_runs_api.py` · routes that start runs:
`routes/forge_creator.py` (tool, plugin), `routes/skill_creator_api.py` (skill).
**Tests:** `core/console/tests/test_forge_run_{ownership,retention,events}_e2e.py`, `test_forge_runs_api_e2e.py`.

Skill, tool and plugin generation are minutes-long engine runs. Layer Forge operations are runs too (below).

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

## Layer Forge: one run per operation (D1)

PLAN, CREATE and promote are three independent requests (a CREATE needs no PLAN; a promote can come days later or repeat),
so no run spans them: each is its own run (`kind: layer`, `operation: plan | create | promote`), linked by
`artifact_ref` = `{kind: layer, id: "<id>@<version>"}`. Phases: PLAN `plan`; CREATE `validate, test, enforce, review, create`;
promote `promote`. `LayerForgeOrchestrator(observer=…)` is an additive callback (`phase` and `gate` events; it cannot change a
verdict, a failing observer is ignored); every quality-gate, enforcement and review verdict becomes a `gate` event.
The synchronous routes keep their response shapes; `run_id` is one additive field (also in a 4xx/503 `detail`).
**An audit failure is reported in the operation's own phase** (`plan`, `create` or `promote`) with
`failure_code: audit_unavailable` — the orchestrator's own `phase: audit` is not one of the run's `phases`, and a stepper
cannot mark a phase it does not know. An unknown definition on promote is a 404 and records **no** run (garbage ids must not
fill the history). Layer runs are exempt from the generation cap (they are not engine generations): they neither count
against `MAX_RUNNING_PER_TENANT` nor are refused by it. `forge_runs.InlineRun` is the synchronous counterpart of `spawn`
(no thread, heartbeat or resume). The Layer panel attaches to the running layer run while its request is going (the id only
arrives with the response) and shows the feed.

## Snapshots and diff (D3)

`forge_runs.snapshot(run_id, label, files)` stores the files of one moment content-addressed under
`<run>/snapshots/blobs/<sha256>` with an append-only `index.jsonl` (`n`, `label`, `ts`, `files{path: sha}`); identical content
is stored once; only the owner writes; an absolute, `..`-bearing or over-long path is not stored; a file over
`MAX_SNAPSHOT_FILE_BYTES` (128 KiB) is cut **with a visible marker**; over `MAX_SNAPSHOTS_PER_RUN` (40) a snapshot is skipped
and an event says so. Emit points: the tool's sandbox-test loop (`iteration k`, plus an `iteration` event with passed/total),
the skill's LDD loop (`iteration k`, plus an `iteration` event with the loss), a refine's starting body (`previous version`),
the plugin's staged files (`staged`). Both orchestrators got an additive `iteration_cb` (default `None`, a failing callback
is logged and ignored, the loop is unchanged). **Tools have no "previous version"**: the tool contract refuses a second tool
of an existing name, so there is nothing to refine.
`GET /forge/runs/{id}/snapshots` lists labels and file names (never content);
`GET /forge/runs/{id}/diff?from=&to=` returns a unified diff per file between two snapshots (an index or a label; default: the
last two); 404 when the run has fewer than two.

## Measured on real runs (T-0146, claude-opus-5, 2026-10-12)

| Run | Duration | Events | Log bytes | Largest event | Longest silence between progress events |
|---|---|---|---|---|---|
| tool (failed at review) | 91 s | 5 | 1 160 | 331 B | 35 s |
| plugin | 48 s | 36 | 9 276 | 305 B | 35 s |
| skill (LDD, 1 iteration) | 429 s | 29 | 7 963 | 317 B | **162 s** |

Events are 230–320 bytes; the worst measured log is 9 KB against a 512 KiB cap (55×) and 36 events against 2 000. Artifacts:
a skill body ≈ 3.7 KB, a tool implementation ≈ 3 KB, a plugin 9 files / 27 KB (largest 8.3 KB) — far under the 128 KiB file cap.
The 162 s silence is why the heartbeat is a separate thread: a run that is only silent in `progress` terms would have been
declared orphaned by `ORPHAN_AFTER_S` = 90 s had progress been its only signal. What the orchestrators can hand over mid-run:
the LDD loss per iteration and the tool's test counts per iteration (now emitted); review verdicts and per-case test results
arrive only when the phase ends.

## Retention (D8)

`prune(tenant)`: a finished record goes with its run directory after 14 days; a record whose owner is alive is never
touched; a dead owner's `running` record is first marked `failed: interrupted` and removed one TTL later; symlinks and
foreign names are ignored. It runs when a run starts, when runs are listed, and once at import in a background thread
(`prune_all`, every host imports the module) so a quiet tenant is pruned too.

## API (read-only, session-gated, tenant-bound)

`GET /v1/console/forge/runs?kind=&status=&limit=` · `GET /forge/runs/{id}` · `GET /forge/runs/{id}/events?after=&limit=` · `GET /forge/runs/{id}/snapshots` · `GET /forge/runs/{id}/diff?from=&to=`.
Another tenant's id and an unknown id are both 404. Filters are closed vocabularies (422 otherwise).

## Audit

`forge_run.started` (`tenant_id, run_id, run_kind`) and `forge_run.finished` (`+ status, artifact_kind, artifact_id,
event_count, truncated`) are on the tenant chain, registered in `EVENT_SEVERITY` and `_EVENT_ALLOWLIST`. No request, file
name or message. `console.action_failed` gained an optional `run_id`.

## Synchronous skill runs

`POST /skill-creator/generate` with `async: false` runs the same `work` function and leaves the same truthful record
(real failing phase, error, engine, `run_id` on the response and both audit records).
