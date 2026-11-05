# ACP Learning Loop (ADR-0613) and the learning-system register (ADR-2281)

**Status:** as the register says (below) — the writer side of the loop is state A (event
store, outcome sink, feedback gate); the delegation router reads the learned config only in a
cleared Phase 2, so the router loop is state B · **ADRs:** ADR-0613, ADR-2281 (Corvin-Knowledge)
· depends on ADR-0314, ADR-0532, ADR-0549, ADR-0555, ADR-2092

Until 2026-09-06 the ADR-0314 "learning loop" was a **log, not a loop**: Skills
were booted and unit-tested, but the L5/L10 entry points had zero production
callers, no task outcome was ever recorded, the optimizer modules had no
importer outside tests, the learning store bypassed the audit chain, and the
Learning Dashboard answered with hard-coded mock data. This page describes the
mechanisms; **which system is live is stated only by the register**.

## The register (ADR-2281) — single statement of state

`core/learning/learning_systems.yaml` lists every learning system with its production
callers (`path:line:needle`, every line re-read 2026-10-29 on the integrated tree), the code that
reads its output (`readers`, same form), its tests and its state: **A** live and read — a caller runs it
AND a reader is named (`readers`, or a `signal_contract` consumer that is not `none`) ·
**B** live but shadow / no reader yet, with an `intake` naming when its output may act ·
**D** dead, retired by the named decision (ADR-2282) until its last module is deleted. Nothing
here, in CLAUDE.md or elsewhere may call a system "wired", "live" or "complete" unless its
register entry is A. Three CI checks hold it (all in `run-all-tests.sh`):

| Check | File | Fails on |
|---|---|---|
| Register | `tests/learning/test_learning_systems_register.py` | a caller, reader or contract `consumer` whose file does not exist or whose needle (≥ 4 characters, starting with an identifier: the called function, `module.attr`, `call(arg, "literal"`) is not CODE in the statement of the cited line and at most 1 line from it — the file is tokenized, so a needle inside a comment, a docstring or another string does not count. A caller must be the statement's call, an argument of a call (a registration) or a decorated definition; an import NEVER counts, and a call nested in another call's arguments counts only on its own line. A reader or consumer must USE the value: called, tested in `if`/`while`/`return`/`for`, passed to a call that is not a route registration, or assigned to a plain name (not `_`, not an attribute or subscript store such as `self._x = v`) that occurs later in the file — anywhere in it, not only in the same function (review c120 r3_s3-1..3, r4_s3-4; the remaining limits — code that never runs, a reused name — are listed in the test's module header). Every ref must be production code: one into a test file (`tests/`, `test_*`, `*_test`, `conftest`, `*.test.*`) fails (r4_s3-3). In a JS/TS/TSX file, comments (also a JSX `{/* <X /> */}`) and string/template literals are blanked before the `needle(` / `<Needle` match (r4_s3-2). Negative controls on copies: the L18 `<LearningOverview>` reader inside a JSX comment, a block comment, a string and a template literal; an L06 entry backed only by a test file's call; every ref shifted by ±2 and ±3 lines, three needles swapped to a REAL neighbouring call (`register_live_turn`, `meta.get`, `monitoring_route`), five calls deleted while their import survives 1-3 lines above, an import line cited as caller, the L06 injection gate replaced by a dead tuple, the L12 readiness gate bypassed with the name left as a bare expression, the L19 router registration named as a reader, the L05 reader and the L19 scheduler start turned into comments, the L15 guidance call deleted while `out["guidance"]` stays one line below, a needle that exists nowhere, the needle `t`. Also a missing test file, an A without caller or without a reader, an A that still states an `intake` or whose recorded readiness (`measured`) is not ready on every surface (so promoting L05 or L12 fails while the ADR-2092 gates do not hold), an entry with `promoted_from` (L08, L19) that lost its contract, a B without `intake`, a B without a valid `signal_contract` (ADR-2303, below; `proxy_of`, `target`, `consumer` must be strings — YAML `no`/`false` loads as a bool and fails), a D without a resolving ADR id or with every module gone, an unclassified unreachable `core/vibe_engineering` module, and a row of the table below that differs from the register |
| Reachability ratchet | `scripts/learning_reachability.py` + `scripts/learning_reachability_baseline.json`, `tests/learning/test_learning_reachability_ratchet.py` | a NEW unreachable module in the learning scopes (same graph as `zero_caller_sweep`), and a STALE baseline entry (module reachable again or deleted). Shrink-only. A re-export in `core/learning/__init__.py` is not reachability (`REEXPORT_BLIND_INITS`, review 2026-11-05 LRN-19): `import core.learning.x` reaches none of the package's re-exports and `from core.learning import Name` only the module `Name` comes from; `import core.learning` / `import *` still follow the whole `__init__`, a module-path string (`soft_dependencies`) does not. Measured 2026-11-05: no `core/learning` module was reachable only through the `__init__`; applying the rule to every scoped package would expose 17 more (`core/vibe_engineering`, `core/skills/feedback`, the DoD verifier) - a per-module decision, not done here. A module-level sweep cannot see a dead FUNCTION (e.g. `integrate_feedback_outcome`) |
| Call-site pins | `tests/learning/test_learning_call_site_pins.py` | L06 injection gate / follow-up grade and L10 distill scheduling / injection through `adapter.process_one`; L14 `emit_*_confidence` through `POST /v1/console/forge-bundles/import`. Each was shown red with its call site cut |

<!-- register-table:start (kept equal to core/learning/learning_systems.yaml by the register test) -->
| Id | System | State |
|---|---|---|
| L01 | Learning host and Data Hub daemon | A |
| L02 | Anomaly monitor (learning host, every 300 s) | A |
| L03 | Event store and outcome sink | A |
| L04 | Feedback gate (throttle, reality check, trust weight) | A |
| L05 | Delegation-router config loop (os.delegation_router learned config) | B |
| L06 | Skill grades, injection gate, promotion | A |
| L07 | CEL turn grades | B |
| L08 | Model selector learner (descriptive only, shown on the Models page) | A |
| L09 | Savings meter (Free-tier cost.optimize allowance) | A |
| L10 | User model distillation (L28, opt-in per chat) | A |
| L11 | Shadow OS skills (workflow optimizer, flow guard, security orchestrator, context adapter) | B |
| L12 | Routing ledger and rollback judge | B |
| L13 | Flow-guard learning route | B |
| L14 | Layer Forge learning integration (emit_*_confidence / *_outcome) | B |
| L15 | KB learning (kb guidance run, guidance skills) | A |
| L16 | Compute search strategies (grid, random, bayes) and HAC attribution | A |
| L17 | Learning and maturity panels (report health, not learning) | A |
| L18 | Video learning feedback API (ratings, per-variant statistics, advice) | A |
| L19 | Autonomous Forge (skill canary autopilot, on by default) | A |
| H03 | Plugin-declared learning loops (manifest discovery) | A |
| R19–R19 | Brain hub leftover kept by ADR-2282: `subsystems/notification_daemon.py` (still imported by the unreachable `core/skill_forge/skill_forge_v2.py`); R01–R18 were removed with their deleted modules | D |
<!-- register-table:end -->

**Signal contract (ADR-2303, PLAN-0966 WP1, 2026-10-26).** Every B entry carries a
`signal_contract` (A may): `signal`, `proxy_of`, `validity`, `consumer`, `target`, `guard`,
`control`, `rollback`; a system with several independent signals (L11) carries one contract per
skill (`skill`). The register test enforces the form: a B entry without a contract fails; a proxy
(`proxy_of` not `none`) with a `target` other than `none` fails — a proxy may guard, it is never
optimised for; `consumer` is `none` or a `path:line:needle` that resolves like a caller;
`validity` is a mapping of numbers or `unmeasured`; an optional `writes` names the stored parameter
a signal moves — a signal that is not a proxy and `writes` a parameter must name it as its
`target`, so a proxy that moves a stored value cannot be relabelled "not a proxy" to pass.
Today **no entry names a target.** L05 (the router config loop) carries one contract per writer,
and both are proxies of routing correctness that `writes: confidence_threshold` with
`target: none`: the operator star rating of `POST /v1/console/feedback/skill` (+/-0.05 per
rating, trust-weighted, on an arbitrary `subject_id`, joined to no routing decision) and the
epoch test of `POST /v1/console/learning/feedback` (the tenant's last 10 task outcomes,
tenant-wide, not attributed to the router; ADR-2305). Their consumer is the gated Phase 2 path
(`delegation_policy.py`, `confidence_threshold`), which reads nothing in shadow; before the
threshold may act it needs a signal that is not a proxy (ADR-2305 Part 2) — a decision, not this
register. Only L12 and L19 name validity numbers. L12: the ADR-2092 gates (500 judged decisions, join rate 0.95,
7-day console soak): its signal — the routing ledger joined to the OUTCOME — is not a proxy but
learns no parameter; its consumer is the readiness gate (`dual_write.py`,
`readiness.cached_evaluate`). L19: the ADR-2094 loss rule (`min_outcomes: 3`, `window_days: 14` —
`autonomous.py` `LOSS_MIN_OUTCOMES` / `LOSS_WINDOW_S`: at least 3 grades in 14 days). L07 (`turn completed`, proxy of output quality), L08 (0.85 / 0.2 and
0.95 / 0.3, proxy of answer quality, consumer the Models page) are proxies with `target: none`; L11
learns nothing (`target: none`). L13's only input is an operator's flow feedback, which may only
add or raise a DENY rule of the route's own in-memory FlowGuard (audit-first since ADR-2283) — no
production gate reads it. L19 (A since 2026-10-27; the entry said "autopilot off by default", but
`autonomous.autopilot_settings` defaults it ON and the route module starts its scheduler at import)
reads organic follow-up grades (a proxy of skill output quality; loss = mean < 0.5 over ≥ 3 grades
in 14 days) to fork a candidate and run its canary gate; promotion stays an operator decision,
`target: none`. The autopilot scans USER-scope skills only: its roots are the tenant registries
`<corvin_home>/tenants/<tid>/skill-forge` (`autonomous_forge_routes._tenant_roots`) and
`autonomous.skills_overview` reads `registry_for(root).list()`, so a grade of a session- or
project-scope skill never yields a loss signal, fork or canary. The other validity bounds are `unmeasured` until PLAN-0966 WP4 measures them. The contract checks form, not truth, and grants no permission to learn.
Proof of the proxy labels: `test_router_threshold_writers_are_registered_as_proxies` (with
negative controls on register copies).

Findings of 2026-10-25: **L10** (now A) — both adapter call sites ran, but
`user_model.distill()` queried `conversation_recall.recall('')`, an empty FTS5 phrase that
matched nothing, so no model was ever distilled. `recall()` now answers an empty query that
names a chat with that chat's newest turns (oldest first; without a chat it still returns
nothing), and the pin `test_l10_a_real_turn_yields_a_distilled_model` passes through
`adapter.process_one`. **L08** (A since 2026-10-27) — the selector learner's only inputs are
proxies (0.85 / 0.2 "bridge turn ended without / with an error", 0.95 / 0.3 per finished task) and
the selection never reads it; its operator-facing reader is the Models page (`GET /v1/engine/config`
→ each task card), which since 2026-10-27 labels the number "no-error score (proxy)" instead of
"NN% confident" (review c120: twelve error-free turns read as "76% confident"). A finished task
credits the learner of the task's OWN tenant (`task.input["tenant_id"]`, exactly like the OUTCOME
sink); `record_event`'s `tenant_id` argument defaults to `_default` and no closer passes it, so until
2026-11-05 every tenant's outcomes moved `_default`'s statistics (LRN-4). A task without a tenant is
dropped. The 19 unreachable `core/vibe_engineering` modules are classified
not-learning in the register (`classified_not_learning`), none is retired by ADR-2281.

## The loop

```
real turn ──► delegation_policy.route_and_record()   (EXACTLY once per OS turn, bridge AND console)
                 │  reached via resolve_delegation_route (turn stays native) or
                 │  resolve_worker_engine (delegation-worthy), or directly for a turn
                 │  the triage never saw (bridge flags off / bare /delegate; console
                 │  delegation off / throttled / /use-engine claude_code)
                 ├─ bundled rule decides; os.delegation_router ADVISES (confirm | de-escalate to native)
                 ├─ Phase gate (monitoring/dual_write.effective_phase): shadow unless
                 │  CORVIN_ACP_PHASE=phase2_dual_write AND Skill booted AND no rollback AND
                 │  readiness ready for the surface; phase2_real is always refused
                 ├─ dual_write only: clamp (native-only de-escalation, never over /delegate);
                 │  a Skill-changed route is served only if "routing.phase2_decision" COMMITTED
                 └─ one content-free row in <tenant>/learning/routing/routing_ledger.jsonl
                        • skill.executed → core audit chain, decision {engine, bundled_engine, shadow, confidence}

turn ends ──► delegation_policy.record_turn_outcome()   (bridge process_one / console _os_emit_completed)
                 • outcome row joined by turn_id: engine that SERVED + ok + latency
                 • NOT-JUDGED turns ("ok": null, "excluded": cancelled | orphaned | no_engine):
                   counted (summarize()["not_judged"]), out of the join rate, every success
                   rate and the rollback judge
                 • a judged Skill-served turn runs the rollback judge (≥100 samples/side, >2 % drop ⇒ trip;
                   pending marker first, then routing.rollback_triggered, then the trip state;
                   window = rows since the last reset; lock unavailable ⇒ held tripped)
                 • console: a cleared Phase 2 Skill route onto an unavailable OS engine is
                   refused by the engine guard (503) AND recorded — decision + ok:false
                   for the engine the Skill chose (chat_runtime.record_unavailable_skill_route)
boot reaper ──► TaskManager.reap_stale_running("orphaned_on_restart")
                 └─ console turn linked by its task.routing event → outcome excluded "orphaned"

task ends ──► TaskManager.record_event(task.completed | task.failed)
                 └─► core.learning.outcome_sink.emit_task_outcome()
                        • OUTCOME event (task_id, status, exit_code, duration_ms, engine, task_type)
                        • tenant = the task's OWN metadata (create_task(tenant_id=…)), never env

operator ──► POST /v1/console/learning/feedback   {task_id, outcome_quality, would_repeat, reason}
                 • GDPR Art. 6 consent gate FIRST: scope "learning_feedback", deny-by-default;
                   no consent ⇒ 403, audited as console.action_denied
                   (action learning.feedback, reason consent_required); nothing else runs
                 • FEEDBACK event (closed enums only; the free text never enters a chain),
                   written SYNCHRONOUSLY through the ADR-0534 trust gate (throttle → reality check
                   → trust weight; EventEmitter.write_sync). Rejected ⇒ 422
                   {error: feedback_rejected, reason, audit_ref}, console action_denied
                   (reason feedback_rejected:<reason>); not recorded, optimizer never runs.
                   Not committed ⇒ 503 learning_event_not_recorded. Until 2026-10-25 the event was
                   only queued: a gate rejection was lost on the worker AFTER "recorded" was
                   answered and the optimizer still ran on the hypotheses.
                 • FeedbackInterpreter → ConfigHypothesis[]   (deterministic rules, ADR-0549)
                 • SkillAdapter.run_optimizer_epoch(hyp, recent_outcomes(tenant, 10))
                        hyp.delta scaled by the gate's trust weight (admitted_trust_weight;
                        unknown weight ⇒ 503, no epoch)
                        50-epoch baseline → hypothesis phase → accept iff Δsuccess ≥ MDE (0.05)
                        accepted → new config VERSION (snapshot persisted) + CONFIG_UPDATED event
                                   + console audit "skill_config_updated:hypothesis_accepted"
                        label (ADR-2305 Part 1): an acceptance is NOT causal evidence — the
                                   reason and the version's change_reason ALWAYS end in
                                   ":unverified_causality" until the causal test (Part 2) exists
                        frozen skill (ADR-2304) → no epoch, optimizer_reason "learning_frozen"

next turn ──► DelegationRouterSkill.execute() reads load_skill_config(tenant)
                 a LEARNED confidence_threshold (only once a version exists) is the acceptance
                 bar for de-escalation advice in Phase 2; default 0.75. The Skill never
                 escalates — the one-tier escalation it used to apply was removed (ADR-2092).
```

## Components

| Piece | File | Contract |
|---|---|---|
| L5 routing call site | `corvin_operator/bridges/shared/delegation_policy.py::route_and_record` (Skill via `_consult_router`), reached from `resolve_delegation_route` / `resolve_worker_engine` when the caller passes the turn (`sink`/`turn_id`) — a call without it is a pure query (`record=False`, the console engine guard): no row, no audit and no Skill execution in any phase (no `skill.executed`); in dual_write it applies the Skill's advice as a pure function (`delegation_router.advise_from_input`) and the same clamp, so it answers what the turn serves when the turn's `routing.phase2_decision` commits — a turn whose record fails to commit serves the bundled engine (audit-first), which the query cannot foresee | exactly ONE decision per turn per surface; runs AFTER the bundled rule + extension-point hook; degrades to the bundled engine on any failure; skips un-booted processes. The bridge boots the registry itself since 2026-09-28 (`adapter._boot_acp_skills`, ADR-2092 G0) — before that every bridge turn was skipped. "Booted" is per TENANT (review DLG-10/-11, 2026-11-05): the Skill counts as available (`skill_available`) only when `SkillsRegistry.serves("os.delegation_router", tenant)` holds — registered AND the tenant admitted to the registry AND the router not auto-/manually disabled for that tenant. A console turn of any tenant other than the registry's boot tenant, and every turn after the router auto-disabled itself (3 failures), is therefore `shadow` with reason `skill_not_booted` (audited as `routing.phase2_refused` when Phase 2 is requested; a carried `dual_write` decision is restricted the same way), executes nothing and breaks the console soak — before, the gate reported the Skill available, every such turn executed it into a "Tenant isolation violation" error record and the ledger said `skill: null` with no reason. No host admits further tenants (`add_tenant` has no production caller, deliberately): the registry's audit and learning backends are bound to the boot tenant and the chain chokepoint (F-A6) refuses a record tagged with any tenant other than the process tenant, so admitting `acme` in a `_default` process wrote acme's `skill.executed` into `_default`'s chain under `_default` and turned each learning record into an `audit.tenant_mismatch` ERROR (measured). L5 shadow evidence therefore accrues only for the process tenant — one process per tenant, as for learning events. The console runs the turn's routing record (`_route_console_turn`: triage, worker-engine target, post-hoc record) in a worker thread (`_route_console_turn_off_loop`, `asyncio.to_thread`), and the WebSocket engine guard's `turn_phase_decision` / `will_delegate` / `record_unavailable_skill_route` likewise — the Skill's 500 ms budget, chained audit writes and the ledger `flock` stalled every chat of the console process while they ran inline (review DLG-14); a turn cancelled while its record is still being written gets that row joined to a NOT-JUDGED `cancelled` outcome when it lands. The carried phase decision reaches the triage-native branch too (`resolve_delegation_route(phase_decision=...)`, review DLG-15) — one decision per turn on every branch |
| L5 routing ledger + gates | `core/skills/os_skills/monitoring/` — `routing_ledger` (decision/outcome rows, closed vocabulary, ADR-0297 `has_sensitive` gate on every string), `correctness_tracker` (observable success, skill- vs bundled-routed), `rollback_detector` (trip order: the restrictive-only pending marker `<tenant>/learning/routing/rollback_pending.json` FIRST — Phase 2 off in every process and across restarts, an unreadable marker restricts too, and an EMPTY or torn marker (cut by a crash) is a HOLD (`cause: marker_torn`) — never a trip recorded over 0 samples: it is re-judged, trips only on a trailing window and is released audited on a judgeable window that finds no trip; the marker writers and the state writer fsync their temp file before the link/rename (review c120 r7b-4) — then `routing.rollback_triggered`, then the trip state, and only then is the marker removed; a failure at any step keeps the marker and is retried by every phase decision and readiness refresh, and a retry after a committed record only writes the state, never a second record — also across processes: a process holding the tenant on an in-process hold re-reads the committed marker under the lock, and another process's judged marker (record committed, state outstanding) replaces the hold, so one trip is chained once (review c120 r7b-6) — and a COMMITTED judged marker also replaces this process's own judged trip whose record had not committed, so its retry writes the state instead of a second `routing.rollback_triggered` (review c120 M1-2). Every trip/reset state write raises a `generation`; a pending trip from an older generation than the persisted one (another process reset or tripped meanwhile) is dropped — decided by generations ONLY, never by comparing wall-clock times, so a reset taken under a clock step erases neither a later lock-failure hold nor a judged trip whose record has not committed (review c120 r7b-1; a state without a generation reads as generation 0 and drops nothing; a write over a state that cannot be read — torn, not an object, EACCES — takes a generation above every counter value, a nanosecond clock reading never below the highest generation still visible, so an operator reset of a torn state still outranks another process's older pending trip; review c120 M1-1: it restarted at 1 and that trip re-tripped the tenant after the reset; an entry created while the state could not be read has no generation and records the unreadable file's inode instead — it is dropped once the state is readable under ANOTHER inode (a reset or trip replaced it), kept while the same inode turns readable again, so a bridge hold taken over a torn state no longer outlives the console's reset until a restart; review c120 M1-3) — and the readiness cache drops a verdict computed under another state stamp — generation, inode, mtime — so a trip/reset by ANY process invalidates it — and only for its own tenant: in-process, a trip/reset clears only that tenant's verdicts (`readiness.clear_cache(tenant_id)`, per-tenant generation; review c120 r4_s2-2: a global clear served every other tenant's next console turn as `pending` and restarted its bridge soak). A trip (marker, record, state) and a reset run under one per-tenant `flock` (`<tenant>/learning/routing/rollback.lock`), so a reset never lands between a trip's record and its state; the reset re-reads the state under that lock (that is where the route's 409 comes from), and a committed trip whose state write had failed gets that state written before `routing.rollback_reset` — if it still cannot be written, the reset answers 503 `state_not_written` with no reset record (review c120 r4_s2-3). The lock fails CLOSED (review c120 r5_s2-1): when `rollback.lock` or the routing dir cannot be opened/created or `flock` fails, the judge holds the tenant (in-process pending entry always — also under EMFILE, review c120 r6_s2-3 — and the pending marker where none exists and the dir is writable), chains `routing.rollback_unverified` (`tenant_id`, `cause: lock_unavailable`; once per hold) and never answers "not tripped"; the reset route answers 503 `lock_unavailable` (nothing written, still tripped). The release of such an UNVERIFIED hold is automatic and audited (review c120 r6_s2-1): the next judge or phase-gate retry that gets the lock re-judges it — a trailing window completes it as a real trip (the hold's in-process entry and marker are first REPLACED by the judged trip — its details, no `cause` — so a `routing.rollback_triggered` that fails to commit leaves a judged trip that is retried, never released, review c120 r7), a judgeable window (≥100 per side) that finds no trip chains `routing.rollback_hold_released` (`tenant_id`, `cause`, `skill_n`, `bundled_n`) and only then removes the marker, too few samples keep it held; a marker that cannot be read inside the lock holds without judging (never a trip over an unread window). A JUDGED trip still needs the operator reset. Known limit: a held tenant serves no Skill turns, so a hold over a window with fewer than 100 Skill samples stays until the operator resets it. A reset opens a FRESH window (reviews c120 r5_s2-2, r6_s2-2): the reset stores the ledger's POSITION (`ledger_pos`: device + inode + byte offset + `fp`, the sha256 of the 256 bytes before the offset, read under the ledger's lock; unreadable → 503 `ledger_position_unreadable`, no reset record) and the judge evaluates only rows appended after it — while the position still names the same content: a ledger that was replaced, re-created (also under a reused inode number) or truncated — also one regrown past the offset — no longer matches and is read WHOLE, no row skipped, so it never hides post-reset failures (it may re-count pre-reset ones: the restrictive direction; review c120 r7b-2/-3) — no wall-clock comparison, so a clock step before or after the reset neither hides post-reset rows nor admits pre-reset ones (a state without `ledger_pos`, written before this, still bounds by its `at`), so the first Skill-served turn after a reset — also a judge that queued on the lock behind the reset — cannot re-trip on the pre-reset failures; the ≥100-samples-per-side minimum must be met again on post-reset turns. Known limit (review c120 r4_s2-1, not fixed): every audit writer — the bridges' `audit_event` and the console's own `_emit` alike — is refused by the core chain (F-A6) for a record whose `tenant_id` differs from the PROCESS tenant (`CORVIN_TENANT_ID`), so on a console process serving a session of another tenant the reset answers 503 `audit_not_committed` and that tenant's trip can only be cleared from a process running as that tenant. The operator reset is `POST /v1/console/learning/routing/rollback/reset` (CSRF, tenant from the session, 409 `not_tripped`; audit-first `routing.rollback_reset`, a state write failing after it chains `routing.rollback_reset_not_applied` and answers 503 `state_not_written`, still tripped; it never answers 200 `tripped: false` while the tenant is still held (review c120 r7b-5): a pending marker that cannot be removed (a directory, or its directory not writable) is refused BEFORE the record — 503 `marker_not_removed`, no record, still held — and a tenant still held after the reset's state write chains `routing.rollback_reset_not_applied` and answers the same 503) — e.g. `curl -X POST -H "x-csrf-token: <token>" -b corvin_console_sid=<sid> http://127.0.0.1:8765/v1/console/learning/routing/rollback/reset` from a logged-in console session; there is no UI button), `readiness` (≥500 judged decisions, join ≥0.95 over judged decisions, per-complexity proxy evidence; bridge additionally needs the continuous 7-day console soak; the 30-day window is read from the ledger's last 8 MiB (`routing_ledger._READ_TAIL_BYTES`, ~17 k turns) — when that tail starts inside the window every verdict carries `window_truncated: true` (`routing_ledger.read_window`), and a bridge soak the visible tail is too short to contain answers reason `window_truncated` instead of `console_soak_incomplete` (review DLG-24). The ledger is NOT rotated (deferred, growth ~480 B/turn): the rollback judge's reset position `ledger_pos` names ONE file (device + inode + offset + fingerprint) and a position that no longer holds is answered by reading the file whole — "no row skipped" only while there is one file; a rotated segment holds exactly the rows after an older position, so rotation first needs a segment-aware position and a migration of every stored `ledger_pos`), `dual_write` (phase gate + clamp) | see ADR-2092; Phase 2 activation is refused and audited once per process per reason (`routing.phase2_refused`) until every gate holds. Readiness is stale-while-revalidate: a verdict older than 300 s (`_CACHE_TTL_S`) is refreshed on a daemon thread and served until the refresh lands, up to 1 h (`_CACHE_MAX_STALE_S`). A missing verdict (first turn after a start), one older than 1 h, or one invalidated by a rollback trip/reset of ITS tenant (that tenant's verdicts dropped, its generation bumped; a refresh in flight across it is discarded) answers the transient `evaluating` placeholder, which runs shadow, is recorded with `gate: pending` and is never audited as a refusal. `rollback_active` is checked before any verdict is read. On the 2026-10-27 ledger copy 15.2 % of console turns (56/369) and 8.0 % of bridge turns (86/1072) come more than 1 h after the previous turn on their surface and would still be served shadow in a cleared Phase 2 (at the former 300 s rule: 66.4 % / 60.5 %). The console computes the phase ONCE per turn (`delegation_policy.turn_phase_decision`) and carries it to the engine guard (`will_delegate`) and to `stream_turn(route_phase=...)`; `route_and_record` re-checks a carried decision only in the restrictive direction (a trip after the decision still turns it to shadow). Every decision row carries `gate: verdict | pending | post_hoc`. |
| Outcome sink | `core/learning/outcome_sink.py` | `emit_task_outcome()` / `recent_outcomes()`; content-free; fail-soft; tenant from task metadata only; the booted emitter's store is `TenantEventStores`, so both write and read the task tenant's own directory (LRN-2) |
| Task chokepoint | `core/console/corvin_core/task_manager.py::TaskManager.record_event` | emits on `task.completed` / `task.failed`; `create_task(tenant_id=…)` at every production creation site (console chat, CCC chat router, both bridge sites). An omitted `tenant_id` defaults to `_default` (ADR-0007) — the task's audit record and its OUTCOME name that tenant, never the environment (`test_task_without_tenant_is_filed_under_the_default_tenant`) |
| Audit-first store | `core/learning/event_store.py::EventStore.write_event` | core chain record (`learning.<event_type>`, content-free) FIRST via `event_persistence.core_audit_event`; no chain commit ⇒ no disk record (RuntimeError); disk record carries `audit_ref`. The PII scrub covers the payload fields only (`signal`, `skill_config_delta`) — never the identity fields readers join on (tenant, skill, event id, timestamp). A partition with a torn tail line (crash, full disk) gets a newline before the next record, so the torn bytes never swallow a committed record; `event_persistence.EventStore` readers, retention and the GDPR Art. 17 erasure skip such a fragment (erasure removes one that still names the subject, also one cut inside the id after its first character) instead of raising (review 2026-11-05, LW-1/2/5, LRN-27). A `skill_executed` signal's `output` is stored as produced (the ADR-0534 Layer-2 hash is recomputed from it; its producers scrub it at the source) — the regex scrub turned Unix timestamps and 40-hex ids into `[PHONE]`/`[TOKEN]` and every honest `claimed_output_hash` was refused (LRN-17). Both learning stores write `<tenant>/learning/events/`; every reader filters by schema (this store: bare `EventType` values; `event_persistence`: `learning.`-prefixed types and tombstones), so `count_events` equals what `query_events` returns — both read the same partitions,
`EventStore._partition_files()` (date-named `YYYY-MM-DD.jsonl` only, sorted; an operator copy
such as `backup.jsonl` is neither counted nor returned, a pre-2026 partition is both; no implicit
2026-01-01 floor or "today" ceiling, RL-9) — `event_persistence.read_events` no longer raises on this store's records, and a skipped record is logged by its missing FIELD names and file only, never verbatim (LRN-5/6/7). The tenant rule is the canonical `core.tenants.validation.validate_tenant_id` (`re.fullmatch`; refuses `ACME`, `-x`, reserved `test`, a trailing newline — LRN-15). Process-wide writers (`core/skills/boot.py`, the token hook, `standalone.py`) use `event_store.TenantEventStores`: one BOUND store per tenant, so tenant `acme`'s OUTCOME lands in `tenants/acme/learning/events` and `recent_outcomes("acme")` reads it there; an unbound store over `<home>/tenants/<tid>` refuses an event of another tenant (LRN-2) |
| Config adapter | `core/skills/os_skills/skill_adapter.py` | under `<CORVIN_HOME>/tenants/<t>/skills/os_delegation_router_config.json`; versions persisted WITH config snapshots; epoch persisted; `rollback()` works after restart and records `active_version` (the version whose config is live — NOT the last list entry: a rollback restores an older config and keeps the newer versions, and reporting `versions[-1]` named the wrong version in the audit trail and the routing ledger); `load_skill_config()` is the read side (mtime-cached, never writes; files written before `active_version` existed fall back to the last entry) |
| Router consumption | `core/skills/os_skills/delegation_router.py::DelegationRouterSkill.execute` → `_learned_threshold` (re-exported by `os_skills_phase1.py`) | optional `tenant_id` input → learned `confidence_threshold` (non-shadow calls only; a shadow call returns before reading it); `shadow`/`bundled_engine` echoed. The value is acted on only in `delegation_policy` dual_write (`confidence_threshold` as the de-escalation acceptance bar) |
| Console API | `core/console/corvin_console/routes/method_discovery_api.py` | see table below; ALL real, ALL audited; CSRF on mutations; tenant from `SessionRecord` |
| Panels | Learning Loops view (`routes/learning_analytics.py`), maturity (`routes/api_vibe_maturity.py`) | register L17 (A): they report the loops' health, they do not learn. The former `web-next/src/panels/LearningDashboard.tsx` no longer exists (checked 2026-10-25), and no web-next file reads the `learning_enabled` flag |

## Console endpoints (`/v1/console/learning/…`)

| Method · path | Answer | Audit |
|---|---|---|
| `GET config-versions?skill_id=` | real version history; `[]` until a hypothesis is accepted | — |
| `POST feedback` | requires the per-user consent scope `learning_feedback` (deny-by-default; missing ⇒ 403). The feedback must pass the ADR-0534 gate (needs a `skill_executed` of the skill in the last 24 h; else 422 `feedback_rejected`). Then `{status: recorded, hypotheses[…accepted, optimizer_reason], recent_outcomes, current_config, current_version}` | denial: console `action_denied` (action `learning.feedback`, reason `consent_required`; a 503 with reason `consent_unverifiable` when the consent store cannot answer). Else console `learning.feedback_received:<quality>` + chain `learning.feedback` (+ `skill_config_updated` on accept) |
| `POST config/rollback?to_version=` | real rollback; **404** on unknown version | console `learning.config_rollback` + chain `learning.config_updated` |
| `GET preferences` | derived from recorded OUTCOME events per task_type; `{}` without outcomes | — |
| `POST preferences/confirm?task_type=` | PREFERENCE event | console `learning.preference_confirmed` |
| `GET health` | `operational` only when the subsystem imports AND an emitter is booted | — |

Only `os.delegation_router` is tunable (`TUNABLE_SKILLS`); any other `skill_id` is a 400.

**Epoch-test label (ADR-2305 Part 1, PLAN-0966 WP2, 2026-10-26).** The epoch test is a trend rule,
not an experiment: the baseline is the mean of the windows of epochs 2–50 (until 2026-11-05 it was
overwritten by the last window, and an empty window `(0, 0)` counted as a 0 % rate — it is now
skipped as "no evidence" without advancing the epoch, and `hypotheses_tested` counts accepted
hypotheses too; review LRN-12), the input
`recent_outcomes(tenant, 10)` is tenant-wide, and outside a cleared Phase 2 nothing reads the
learned threshold, so an acceptance cannot be an effect of the change. An accepted hypothesis
therefore reads `optimizer_reason: "improvement_<x>%_accepted:unverified_causality"` and its version
`change_reason: "<rule reason>:unverified_causality"` (also in `GET config-versions` and
`GET learning/checkpoint`). The label is unconditional. It is NOT removed when Phase 2 dual-write
holds on every surface: the epoch test measures before it applies, compares two tenant-wide
windows of 10 tasks without a control group, and applies only a hypothesis whose improvement reaches
the MDE 0.05 — with no reader of the learned threshold outside the gated Phase 2, an acceptance is a
threshold crossing, not a measured effect. Only the causal test (ADR-2305 Part 2) removes it.
Acceptance threshold, versioning, rollback and the `skill_config_updated` audit are unchanged; the
marker is an enum suffix, never free text, and does not enter the chain. Readers of the text: the
console `POST feedback` answer and the two version lists; no code compares it.
Proof: `tests/learning/test_adr_2305_unverified_causality_e2e.py`.

### Operator interface (`routes/learning.py`, `routes/learning_metrics.py`) — real data or an honest error code (2026-09-07)

Until 2026-09-07 these routes answered with constants (`alpha_core=0.1`,
`convergence_percent=87.5`, `[]`), `override`/`rollback` reported `"success"`
while changing nothing, `learning_metrics.py` was mounted under a doubled
prefix (`/v1/console/v1/console/learning/metrics`) and its WebSocket took the
tenant from a query parameter with no authentication (adversarial review
F-L2/F-L3/F-L4). Everything below is computed from the tenant-BOUND
`event_store.EventStore` and the core chain — never a placeholder.

| Method · path | Answer | Notes |
|---|---|---|
| `GET learning/status` | `{event_counts{per type}, recent_outcomes{window:50,total,successes,success_rate}, outcome_loss (1−success_rate), last_outcome_at, last_feedback_at, last_config_update_at, status}` | `status` ∈ `no_data` (no events) · `collecting` (outcomes, no config change yet) · `learning` (≥1 `config_updated`) |
| `GET learning/metrics?window=1h\|6h\|24h` | 12 equal buckets `{timestamp, outcomes, successes, success_rate, feedback, skill_executions, config_updates}` + `sample_count` | 400 on any other window |
| `GET learning/checkpoint` | the REAL rollback points: `SkillAdapter.get_version_history()` per tunable Skill (`checkpoint_id` = `version_id`) | `[]` until a hypothesis is accepted; 503 when `core.skills` is absent |
| `GET learning/audit?limit=` | the tenant's `learning.*` records from the core hash chain (`audit_ref`, type, ts, skill_id, lom, hash, prev_hash) | content-free by construction; 503 when the writer is not resolvable |
| `POST learning/override` · `POST learning/rollback/{id}` | **501** — there is no live meta loop to override; the real, audited change path is `POST learning/config/rollback?to_version=` | CSRF-gated like every POST |
| `GET learning/metrics/current` · `GET learning/metrics/history` | the same `learning_status()` / `learning_series()` as above (one computation, three surfaces) | single prefix `/v1/console/learning/metrics` |
| `POST learning/metrics/export {format: json\|csv, window}` | the tenant's learning events in the window, INLINE (`Content-Disposition: attachment`, `X-Rows-Exported`) — content-free rows (`event_id, event_type, skill_id, timestamp, audit_ref, lom`) | no download token; GDPR Art. 20 |
| `WS learning/metrics/stream` | pushes `{type: metrics, data: <status>}` immediately and every 10 s; `ping`→`pong` | authenticates the `corvin_console_sid` cookie via `auth.load_session`; **1008** without a live session; tenant = the session's tenant, no `tenant_id` parameter |

Free text is never persisted: `feedback_text` (ratings) and `reason` (grades)
are accepted and reduced to `has_text`/`text_length` (`has_reason`/
`reason_length`) before the event is written (`operator_feedback.build_rating_event`,
`LearningIntegration.grade_pattern` — the latter now writes through the
audit-first `event_store.EventStore`, not the unchained TreeOfThoughts JSONL).
`POST learning/grade` and `POST learning/note` were removed on 2026-11-05 (LRN-18):
each request built a fresh `LearningIntegration` whose node cache nothing in production
fills, so both answered 404 for every pattern, and no console page called them.
`GET learning/nodes` stays (it falls back to the earned tree from the CEL stage grades).
The integration's failure clusterer writes under the TENANT
(`<tenant_home>/learning/discoveries`, created on first write); it used to create a
tenant-less `<corvin_home>/learning/discoveries` on every request.
`EventStore(tenant_home, tenant_id=…)` is tenant-bound: an event carrying any
other tenant is refused before the chain write.

`EventStore.query_events(..., newest_first=False)` selects from the OLDEST end
by default (date files ascending, write order within a file). A consumer that
wants "the last N samples" — e.g. `consistency_checker.FeedbackConsistencyValidator`
— MUST pass `newest_first=True`, which walks the date files descending, reverses
each file's lines and returns newest → oldest; the default returned the oldest N,
so a "recent" window computed from it never advanced (2026-09-07, R3-B2). One
unreadable line never costs more than itself: a malformed JSON line AND an
unknown `event_type` enum value (a newer schema, corruption) are logged and
skipped per line — the unknown enum used to raise `ValueError` out of
`query_events` and discard every later event, so a single record made consumers
see an empty history (R3-B3).

**Retention (review 2026-11-05, LRN-13).** `core/background/learning_host.py` runs
`_retention_once` once a day (`RETENTION_CHECK_S`, first on the host's first tick):
`event_persistence.EventStore.cleanup_old_events(retention_days=90)` removes the expired date
partitions of `<tenant>/learning/events/` — BOTH stores' records, since they share the partition
(tombstones are removed, not counted) — and chains `learning.retention` (`scope: events`);
then `feedback_gate.prune_ledgers` (`scope: feedback_gate`), after `feedback_gate.reconcile_pending`
voided admissions a crash left unconfirmed (RL-3, below). The remaining tenant learning stores follow
with the same 90-day window, each chained as `learning.retention` with its own `scope` (refutation
2026-11-05, RL-10): `skill_feedback` (`<tenant>/learning/skill_feedback/events.jsonl`, the stream4
ratings with their `subject_id`; `history_store.prune` replaces the file under the writers' lock and
`append` re-opens when it locked a replaced inode), `token_metrics` (`learning/token_metrics.db`,
`created_at`), `ce_stage_grades` (`global/ce_stage_grades.json`, a grade's `ts` — undated legacy
grades are kept; operator grades expire too, so a non-builtin stage needs fresh operator grades to
stay default-eligible) and `model_confidence` (`global/model_confidence_stats.json`, an entry's
`stats.last_updated`; an unparseable file is left untouched, never rewritten as empty). One store's
failure is logged and named (`<scope>_error`, `status()["retention"]["last_error"]`); the others
still run. Other tenants' records under a tenant's partitions (the pre-LRN-2 unbound emitter wrote
them there) are REPAIRED once, before expiry: `event_persistence.EventStore.repair_foreign_records`
appends a record whose `tenant_id` validates to that tenant's own partition of the same date, removes
any other foreign record, and chains `learning.cross_tenant_records_repaired` (WARNING; file,
`moved_count`, `removed_count`, validated `foreign_tenants` only). The same records are therefore
never re-alerted as `learning.cross_tenant_contamination_detected` (CRITICAL) every day; that alert is
left for a record whose move failed (retried on the next pass). The routing ledger
(`learning/routing/`) is not part of this pass. The host runs for its process tenant only
(see register L02).

### Core-chain allowlists (`event_persistence._LEARNING_EVENT_ALLOWLISTS`)

The core writer's metadata floor is default-deny for detail keys (F-A4). Every
learning chain record type (`learning.<event_type>`, `learning.retention`,
`learning.erasure`, `learning.hyperparameter_changed`, the hybrid-context and
erasure-cascade events) registers its positive key set at writer resolution,
exactly like `skill_registry_phase1` does for `skill.*`. Without it the
`audit_ref` the commit check reads back is scrubbed and every learning write
fails closed. Adding a detail key to a learning record means adding it there.

### Deleted: 9D / meta loop and the "staged but unwired" modules

`nine_d_loss`, `meta_optimizer`, `watchdog`, `unified_loss`, `weight_smoother`,
`weight_updater`, `alert_policy`, `alert_dispatcher`, `geo_validator`,
`feedback_validator`, `outcome_validator`, `metrics_exporter`, `export_import`,
`divergence_detector`, `consistency_checker` and `gradient_backprop` no longer
exist (deleted with the unwired scaffolding on 2026-10-11, T-0125; checked
2026-10-25). The security mitigations once attributed to them (feedback HMAC,
gradient clipping, tiered damping, stale-feedback TTL, oscillation detection,
export HMAC, alert nonce, geo validation) are therefore NOT guarantees of this
install, implemented or otherwise. Which learning systems are live is the
register's A entries, nothing else. The one HMAC that still exists,
`feedback_sink.FeedbackValidator`'s optional `signature`, is verified only when
present; nothing in production signs, and `signature_verified` is never taken from
the caller (`FeedbackEvent.__post_init__` resets it; review 2026-11-05, LRN-21). Its
time window is computed in UTC with both bounds (60 min back, 5 s skew forward,
LRN-22). The protection stored feedback gets is the ADR-0534 gate below.

`savings_meter` (ADR-2259) counts a turn whose token field is malformed (`"1.5e3"`,
negative, bool) but does not price it; one such record used to raise out of the scan
and leave the Free-tier cap on `meter_warming` forever (LRN-16).
The L07 CEL stage-grade store (`context_engineering/stages/grades.py`,
`<tenant>/global/ce_stage_grades.json`) takes an exclusive lock (`ce_stage_grades.json.lock`,
flock + an in-process lock) around every load-append-save and writes through a unique
`mkstemp` temp file; 4 threads x 50 grades kept 49 of 200 before. An unreadable store raises
`GradeStoreUnreadable` (writers refuse, `is_default_eligible` answers False, the turn loop logs):
it used to read as `{}`, so the next grade replaced every prior operator grade (LRN-11). A store
that parses but has another shape (`[]`, `"x"`, a stage record that is not `{"grades": [...]}`, a
grade without a numeric `score`) is unreadable too — it surfaced as TypeError / AttributeError /
KeyError, or was appended to and broke the next read — and the eligibility gate logs once per
tenant when it reads one (RL-7). The
store, `usage_epoch.json` and `model_confidence_stats.json` (and their lock/temp siblings) are
path-gate protected like `learning/` and `skills/` (LRN-10).

ADR-0365 token metrics: `chat_runtime` records every console OS turn that reports `usage`
(`record_turn_metrics` after the `turn_usage` event; imported and never called until
2026-11-05, so the Vibe dashboard's token panel read an empty table) into the tenant's own
`<tenant_home>/learning/token_metrics.db` (`TokenMetricsDB()` resolves the file per tenant;
the old default `~/.corvin/token_metrics.db` was one file for every tenant, outside erasure and
ignoring `CORVIN_HOME`). L36 layer `L-token-metrics` (`erasure_handlers.TokenMetricsHandler`)
deletes the rows naming the subject as user or session. The console row's `user_id` is the
turn's consent subject (`local-operator:<tenant>`, the same principal the browser tool's record
names), so an Art. 17 request naming the user — not only one naming the chat sid — finds it; a
database file removed after the first connect is recreated with its schema instead of failing
every insert with `no such table` until a restart (RL-8). A measurement, not a learning system:
no register entry (LRN-14).
`model_selection_learning_listener.fetch_unprocessed_outcomes` / `run_epoch` were
deleted (LRN-20): they could never return anything; the L08 learner is fed by
`TaskManager` calling `process_outcome` per finished task.

### Live experiment collector (`live_experiment_collector.py`) — retired

Retired by ADR-2282 and deleted on 2026-10-25 (T-0271).

`core/learning/live_experiment_collector.py` and its `corvin-live-collector` console
script were deleted. Its last data was from 2026-09-06, its user unit was disabled, and its
only reader (`live_collection_dashboard`) was already gone. The maturity panel measures on
demand instead (`maturity_live.build_measurement`, served by `routes/api_vibe_maturity.py`).
A disabled `corvin-live-collector.service` user unit outside the repo may still exist on a
host; it now points at a removed entry point and can be deleted by the operator.

## Console surface — bounded, CSRF-gated, honest

* **`SkillAdapter._locked` is BOUNDED** (`LOCK_EX | LOCK_NB` + `LOCK_TIMEOUT_SECONDS`
  = 2.0s, then `SkillConfigLockBusy`). `POST learning/feedback` and
  `POST learning/config/rollback` map it to **503 `lock_busy`** and audit the
  refusal. Both handlers are `async def`, so a plain blocking `flock` did not
  stall one request — it stalled the whole console event loop (an anonymous
  `GET /v1/console/version` timed out alongside it). Never reintroduce a
  blocking `LOCK_EX` here; `core/console/tests/test_route_lock_nonblocking_registries.py`
  covers it.
* **`POST /v1/console/api/learning/subscribe` and `/unsubscribe` require CSRF.**
  They read only query params, so with `content-type: text/plain` they stayed
  CORS-*simple* requests and a browser sent them cross-site with the console
  cookie. The subscriber store is capped (`MAX_SUBSCRIBERS_PER_TENANT`, 503 at
  the cap) and `prune_stale_subscribers` runs on every registration — it
  previously had no caller at all.
* **`GET /api/learning/skills/{name}` is computed from real events**
  (accuracy from OUTCOME, latency from `latency_ms`/`duration_ms`, confidence
  from CONFIDENCE, satisfaction from FEEDBACK `quality_rating`, usage from
  SKILL_EXECUTED). A dimension with no events is `null` = *not recorded*, never
  0. `GET /api/learning/user/{id}` returns `available: false` + a reason:
  ADR-0314 events carry NO user dimension (GDPR Art. 5), so per-user metrics
  cannot be computed — the endpoint says so instead of returning nulls under a
  docstring promising metrics. `/summary`'s `cached` flag is the real cache
  state (it was hard-coded `true`).
* **The CSV export neutralises spreadsheet formulas** (`csv_safe` in
  `routes/learning_metrics.py`): a cell beginning `=`, `+`, `-`, `@`, TAB or CR
  is prefixed with `'`. `skill_id`, `lom` and `audit_ref` are free-form strings
  from the event store and are executed by Excel/Sheets otherwise. The JSON
  export is byte-exact and untouched.

## `query_events` — limit always windows from the NEWEST end

`EventStore.query_events(limit=N)` returns the **N most recent** matching events;
`offset=K` skips the K most recent; `newest_first` controls only the ORDER of the
returned list (default `False` = chronological, so `[-1]` is the newest). There
is no way to ask for the oldest N, deliberately: the old default gave callers
the oldest N, and `recent_outcomes` — the optimizer's sole ground truth — was
therefore frozen on outcomes #4991–#5000 of all time once a tenant passed 5000
of them.

## Audit events (ADR-0537 attribution)

| Event | Chain | Emitted by |
|---|---|---|
| `skill.executed` | core | `CoreAuditBackend` on every registry execution (incl. shadow) |
| `learning.skill_executed` / `learning.outcome` / `learning.feedback` / `learning.config_updated` / `learning.preference` | core (content-free: event_id, event_type, skill_id, skill_version, lom) | `EventStore.write_event` (audit-first) |
| `action_performed` — `learning.feedback_received:*`, `skill_config_updated:hypothesis_accepted`, `skill_config_updated:rollback`, `learning.config_rollback`, `learning.preference_confirmed` | console tenant chain | `method_discovery_api.py` |
| `skill.feedback` | core (metadata only: data_class / destination_engine identifiers, closed `result` / `feedback_type` / `rule_effect`, the DENY rule's real `confidence_before`, `event_id`, `audit_ref`; never `reasoning`) | console `POST /flow/feedback` → flow-guard `LearningIntegration.record_feedback`, audit-first via the STRICT `CoreAuditBackend` (ADR-2283) |
| `skill.approval.requested` / `.granted` / `.denied` / `.revoked` | core (metadata only: ids, metric name, confidence, closed codes, `operator_ref` = sha256 prefix, `audit_ref`; never a reason text or raw operator id) | L5 k=2 `OperatorApprovalGate` via the gateway's STRICT `CoreAuditBackend` (ADR-2283) |
| `skill.grade` / `skill.grade_not_persisted` (WARNING; run id, scope, `error_type` = exception class) | core | `SkillRegistry.grade` audit-first; the compensation only when the FINAL write of the store of record fails (review c120 r3_s1-2) |
| `learning.feedback_accepted` / `learning.feedback_not_stored` (WARNING; ids, `source_key`, `stage`, `error_type`, `voided_files`) / `learning.feedback_void_failed` (ERROR; the same plus `reason` `lock_busy` \| `ledger_append_failed`) | core | `feedback_gate.admit`; `not_stored` only when EVERY ledger file that took the "accepted" also took its `void` line (r3_s1-6, RL-1) — otherwise `void_failed` is chained and the admission stays pending for a retry / `reconcile_pending` |
| `routing.phase2_decision` / `routing.phase2_refused` | core | `delegation_policy` (a Skill-changed route is served only after its decision commits; refusals once per process per reason) |
| `routing.rollback_triggered` (WARNING) / `routing.rollback_reset` / `routing.rollback_reset_not_applied` (WARNING; `tenant_id` only) / `routing.rollback_unverified` (WARNING; `tenant_id`, `cause` enum — the judge could not take the rollback lock and holds the tenant tripped, review c120 r5_s2-1) / `routing.rollback_hold_released` (INFO; `tenant_id`, `cause` enum — `lock_unavailable` or `marker_torn` (review c120 r7b-4) —, `skill_n`, `bundled_n` — an unverified hold released after a judgeable window found no trip, before the marker goes, review c120 r6_s2-1) | core | `rollback_detector.evaluate` (after the pending marker, before the trip state) · `reset_rollback` from `POST /v1/console/learning/routing/rollback/reset` (audit-first; `_not_applied` compensates a state write that failed after the reset committed, review c120 r3_s2) |

**Strict core-chain backend (ADR-2283).** `CoreAuditBackend(tenant_id, strict=True)`
(`core/skills/skill_registry_phase1.py`) is the fail-closed dict backend for learning paths
whose contract is "no decision without its record": it raises `AuditNotCommitted` when the
writer is absent, raises, or reports the record did not commit, and returns the record's
`audit_ref`. The default (`strict=False`, the Skills registry) is unchanged: log, count,
continue. It dots the event type (`skill_approval_granted` → `skill.approval.granted`);
register the dotted name. Live users: the gateway's approval gate
(`corvin_gateway/app.py::_init_approval_gate`, `/v1/approvals/...`; a non-committing write
answers 500 and the approval stays pending) — E2E
`tests/learning/test_adr_2283_approval_gate_audit_e2e.py`; and the console flow-guard
learning route (`routes/flow_guard.py`): `POST /flow/feedback` writes its record BEFORE the
route's own FlowGuard policy changes (503 `audit_not_committed` and no change when it does not
commit), may only strengthen — `rejected` / `pii_leak_detected` add or raise DENY, `error` is
neutral, `success` / `approved` / unknown answer 422 — discards the body's `reasoning`, and
`GET /flow/audit` returns no free text. That FlowGuard is separate from the L34 production
gate — E2E `tests/learning/test_adr_2283_flow_guard_feedback_e2e.py`.

## What is learned from — and what is only audited

`SkillMetadata.learn` (default `True`) says whether a Skill's executions feed the
learning store. Every execution is audited regardless (`skill.executed`).
`os.capabilities`, `os.headless_mode` and `os.plugin_health_monitoring` declare
`learn=False`: they are deterministic flag/manifest lookups the SPA triggers
continuously (346 executions in ten minutes of polling were observed) and no
optimizer consumes them. The manifest route additionally caches the resolved
flags per tenant for 5 s (`routes/capabilities.py::_read_flags`), invalidated
by `POST /features/toggle`, so an operator decision is never stale.

## Honest labels: selector learner and CEL grades (ADR-2281 §5, 2026-10-25)

* **Model-selector learner = a descriptive statistic.** Its per-turn input is a binary
  proxy (`model_selector_shadow.py`: `quality_score = 0.85` when the turn ended without an
  error, `0.2` when it failed; the task outcome listener `0.95` / `0.3`) plus operator ratings,
  and nothing in the model selection reads it. The Models → Learning tab says so ("What the
  learner has recorded, per tier", `web-next/src/pages/models/tabs/learning.tsx`; pinned by
  `tests/unit/learning-tab.test.tsx`). The per-tier task cards of the Models page read the same
  score through `GET /v1/engine/config` and label it **"no-error score (proxy)"** with the hint
  "Derived only from whether turns ended without an error; not a measure of answer quality."
  (`engine-parts.tsx` `PROXY_SCORE_LABEL`; pinned by `tests/unit/task-type-card-evidence.test.tsx`).
  Until 2026-10-27 the badge read "NN% confident" and the finding "✓ Learned confidence" —
  a proxy presented as quality. That is why register L08 is A: it has an operator-facing reader.
* **CEL turn grades: no console surface renders them.** The native console path records
  `success=True` for every turn that reaches its end (`chat_runtime.py`, behind
  `outcome_feedback_loop`; the delegated ACS/TDE branch records the run's own result), so such a
  grade means "turn completed", not "the stage helped". Measured 2026-10-25: the
  only reader is `GET /v1/console/vibe-engineering/grades` (`routes/vibe_engineering.py`);
  its frontend hook `useStageGrades` (`web-next/src/adapters/vibe.ts`) has no consumer, so
  no panel shows a CEL grade and there is no label to change. A panel that starts rendering
  them must label an automatic native-path grade "turn completed".
* **Live proof (2026-10-25, `scripts/console-deploy.sh --marker 'the model selection does not
  read it'`):** the host served `assets/index-DNo90ACa.js`; across the 96 built chunks the
  marker is in exactly one (positive control) and none contains `HotfixPanel`,
  `LearningDashboard`, `SkillLearningDashboard`, `learning-loops-dashboard` or
  `skill-learning-dashboard`, the pages ADR-2282 retired. The running console process still
  mounts the retired backend routes until its next `safe-restart.sh`.

## Feedback trust gate (ADR-0534, accepted + built 2026-10-10)

Feedback is untrusted input. `EventStore.write_event` runs `core/learning/feedback_gate.admit()`
on every `EventType.FEEDBACK` event BEFORE the audit-first write; a refusal raises
`FeedbackRejected(reason, audit_ref)` and nothing is stored (routes answer **422**
`{error: feedback_rejected, reason, audit_ref}`).

| Layer | Module | Rule | Refusal event |
|---|---|---|---|
| 1 Throttle | `feedback_throttle.py` | ≤ 5 decisions / 60 s per subject (all producers), counted over that subject's own index `<tenant>/learning/feedback_gate/subjects/<sha256(subject)>/all.jsonl` (content-free; rejected attempts count; bounded tail read) — never an in-process deque, and never the tenant-wide tail, which junk on other subjects could flush | `learning.feedback_throttled` |
| 2 Reality | `reality_check.py` | the skill ran (a `skill_executed` learning event of THIS tenant) within 24 h before the signal (+5 s skew); an optional `claimed_output_hash` must equal `execution_output_hash()` of one of those runs | `learning.feedback_rejected` (`subject_not_verifiable`, `no_corresponding_execution`) · `learning.feedback_reality_mismatch` |
| 3 Trust | `adversarial_detector.py` | `trust_weight = max(0.1, 1 − rejected/total)` over the last 20 JUDGED decisions on the subject BY THE SAME PRODUCER (`subjects/<h>/<source_key>.jsonl`; the producer's own `rate_limit_exceeded` refusals are not counted — `adversarial_detector.NOT_A_VERDICT` — they are the gate's budget, not a verdict on the signal), stamped into `signal.trust_weight`; < 0.3 → alert naming the producer's `source_key` | `learning.adversarial_feedback_detected` (ERROR) |

**Subject, producer, atomicity (review c120).** The gate keys everything on one canonical subject
(`feedback_gate.canonical_subject`: `skill:X` and `X` are both `X`, other forms stay raw), so two
spellings never get two rate budgets. The trust weight belongs to the producer: `admit(source=…)`
(the console routes pass `"console:" + consent_subject(rec)`), else `signal["producer"]` (stripped
before the event is stored), else the event's `lom`. Only a tenant-scoped sha256 of it
(`source_key`, `src_…`) is stored in the ledger and the gate's audit records. read → decide →
append runs under an exclusive per-subject file lock (`subjects/<h>/.lock`, `fcntl.flock` /
`msvcrt.locking`), and the clock is read under that lock, so parallel requests and processes
(bridge + console) cannot exceed the window; a lock not obtained within 10 s refuses the feedback.
`decisions.jsonl` is still appended (all subjects) for the anomaly monitor.
Write order (review c120 r3_s1-6): `decisions.jsonl` (shared by all subjects) FIRST, then the
per-subject `all.jsonl` (the throttle index), then the producer file `<source_key>.jsonl` — each only
after the previous append succeeded, all under the subject lock, and a short write is cut back to the
file's previous size. Every line carries a random 16-hex `id`. When a later append of an `accepted`
decision fails, every file that already took the line gets a void record `{"decision": "void",
"voids": <id>, "reason": "ledger_write_failed"}` (ids and enums only, never text); the readers drop
voided entries and void lines, so the throttle, the producer's trust weight and the anomaly monitor's
tail never count feedback that was refused. If the void write fails too (the same full disk), the
accepted line stays and the chain says so: `learning.feedback_void_failed` with `voided_files`, never
`learning.feedback_not_stored` (refutation 2026-11-05, RL-1); the admission's pending marker stays and
`reconcile_pending` retries the void. A torn tail line left by an earlier full disk gets a leading newline, so it stays its
own unparseable line (skipped on read) and never swallows the next record. If any ledger append fails
after `learning.feedback_accepted` committed, `learning.feedback_not_stored` (ids, `stage`,
`error_type` — the exception CLASS, never its text) is chained and the feedback is refused
(`ledger_write_failed`, HTTP 422): accepted minus not_stored equals what was stored. Proof:
`tests/learning/test_feedback_gate_c120_r2_e2e.py`, `tests/learning/test_feedback_gate_c120_r3_e2e.py`
(three refused posts leave the subject, producer and tail histories empty; a REAL short write — half
the record, then ENOSPC — to the throttle index or the producer file is cut back and voided).

**The caller's write belongs to the admission (review 2026-11-05, LRN-9).** The gate writes
"accepted" BEFORE the caller stores the feedback. `EventStore.write_event` runs its chain + disk
write inside `feedback_gate.admission()`; `POST /feedback/skill` calls `feedback_gate.void_admission`
when its `skill_feedback` audit record (500) or its history append fails. Either way every ledger
file gets the void line for that entry and `learning.feedback_not_stored` is chained with
`stage` `store` | `route_audit` | `route_store` | `crash_recovery` and `voided_files` (3) —
refused feedback is never counted as accepted.
Proof: `tests/learning/test_review_20261105_lrn_e2e.py::test_lrn9_*` (the route case over the
FastAPI TestClient with the real chain).

**A void that does not land is never reported as one (refutation 2026-11-05, RL-1/RL-2/RL-3).**
`void_admission` returns True only when all three ledger files took the void. A void line some file
refused (ENOSPC — the likeliest reason the store write failed in the first place) or a subject lock
not taken within `LOCK_TIMEOUT_S` (a bounded wait) chains `learning.feedback_void_failed` and returns
False; the admission stays in memory and its pending marker on disk, so a second `void_admission`
or `reconcile_pending` retries it. `admission()` never lets the void replace the body's exception:
the void's outcome is attached to the ORIGINAL error as a note (`add_note`), which then propagates
(an `IOError` stays an `IOError`; a `RuntimeError` from the void would read as "the chain did not
commit" when it had). `POST /feedback/skill` answers **503** `{"error": "feedback_void_failed",
"stage": …}` when its void did not land, and confirms the admission after its history append.
**Crash recovery:** `admit()` writes a pending marker (`feedback_gate/pending/<sha256(event_id)>.json`,
0600, mkstemp + fsync + replace) BEFORE it chains "accepted" (write-ahead); the caller's successful
write removes it (`admission()` / `confirm_admission`), a completed void removes it, Art. 17
`erase_producer` removes the producer's. A marker older than `PENDING_GRACE_S` (15 min, wall clock)
whose admission is not in flight in this process is voided by `reconcile_pending` (chained
`stage: crash_recovery`), unless the ADR-0314 store already holds the event (then only the marker
goes). It runs on the next admission of the tenant and on the learning host's daily pass, whose first
tick is host start. The gate's own ledgers are the record: the feedback WAL (`feedback_wal.py`) is the
learning daemon's processing queue — the gate never wrote to it — so it is not used for this.
Residual: a stream4 history append that landed right before a crash is voided too (an undercount of
one, never a phantom "accepted"). Proof: `test_review_20261105_lrn_e2e.py::test_rl1_*`, `test_rl2_*`
(including the 503 over the real route), `test_rl3_*` (the host pass with the real chain).

**Retention and erasure of the ledgers (LRN-13, LRN-24).** `decisions.jsonl` appends also take
`feedback_gate/.decisions.lock`, so a rewrite never loses a concurrent append (pinned across two
processes by `test_rl11_decisions_lock_keeps_appends_across_a_concurrent_rewrite`).
`feedback_gate.prune_ledgers` drops lines older than 90 days (a void expires with its entry) and
chains `learning.retention` (`scope: feedback_gate`, counts only). `feedback_gate.erase_producer`
removes a GDPR Art. 17 subject's lines and deletes their `<source_key>.jsonl` files: the
`source_key` is an unsalted hash of the subject id (`console:` + consent subject, or the raw
principal), recomputable and therefore personal data. The L36 `LearningEventHandler` calls it.

Accepted → `learning.feedback_accepted` (with `trust_weight`). Consumers multiply by
`adversarial_detector.weight_of(signal)` (1.0 for signals stored before the gate):
operator-rating stats (weighted mean), the router threshold step of `POST /feedback/skill` — that
route moves a live config without storing a FEEDBACK event, so it calls `admit()` itself and passes
`trust_weight` to `loop_closure.apply_outcome_to_skill` — and the optimizer epoch of
`POST /learning/feedback`, which scales every hypothesis delta by the weight the gate stamped
(`feedback_gate.admitted_trust_weight(event_id)`; unknown weight → 503, the optimizer does not run)
and returns it as `trust_weight`. (The workflow optimizer's Beta update named here before was
retired with `workflow_optimizer_skill/confidence_calculator.py`, ADR-2282.)

* `tool:<id>` ratings are refused (`subject_not_verifiable`): a tool has no execution record here.
* `grade_pattern` checks the node exists and passes `subject_verified=True` (replaces Layer 2
  only); an unknown pattern is a 404. The console's per-request `LearningIntegration` has an
  in-memory pattern store, so over HTTP every pattern is unknown today.
* `POST /learning/feedback/submit` writes a real gated FEEDBACK event (it used to answer
  `"queued"` and store nothing).
* No override secret. Change `RateLimitConfig` in code review instead.
* **Producers of rated skills must write the execution.** A rating whose skill never wrote a
  `skill_executed` event is refused, so every surface that offers ratings owes the store that event.
  The Video Producer (`os.video_producer`) is not a registry Skill and runs in a plugin that cannot
  import `core.learning`, so the host does it: `create_video_job` starts a watcher thread
  (`video_producer_api._record_job_execution`) that waits for the job's END STATE and writes one
  `skill_executed` event (ids, status, counts — never the task text) through `EventStore.write_event`.
  Written at the end, not the start: a job that never ran must not make a rating valid. A job ending
  `error` or `cancelled` counts (it ran). Consequences, stated: a video older than 24 h cannot be
  rated (the routes say so: "more than 24 hours ago"), a job the console restart interrupted records
  nothing, and a rating on a running job is refused ("once it has been produced"). Proof:
  `core/console/tests/test_video_producer_process_e2e.py` (real child process → event → rating 200),
  `test_video_producer_review_2026_10_10_e2e.py::JobExecutionIsRecordedForTheFeedbackGate`.

Proof: `tests/learning/test_feedback_trust_gate_e2e.py`,
`tests/e2e/test_stream4_skill_feedback_console_e2e.py`.

## Follow-up grades from the user's own text (both chat surfaces, 2026-10-26)

The operator's next message grades the skills that were active in the previous turn —
"gut gemacht" / "well done" is an approval, "nein, falsch" / "nochmal" a rejection, a
near-repeat of the previous question a rephrase. ONE detector and ONE grader serve both
surfaces: `skill_inject.detect_outcome_signal` and `skill_inject.grade_from_user_followup`.

| | Bridge | Console web chat |
|---|---|---|
| Snapshot "skills of the previous turn" | `adapter._record_last_turn_skills` after the post-turn auto-grade (skills the answer visibly used) | `chat_runtime._stream_turn_impl`: the CEL step computes `followup_grading.injected_skill_ids(bundle, skill_block)` — the ids whose body the rendered skill block really carries (first `MAX_BINDINGS`, non-empty body, not PII-redacted); `followup_grading.record_turn_skills` runs only after the native `claude` subprocess started (`task.started` with its pid) with a prompt containing that block. `/debug-engine`, gate refusals, ACS/TDE delegation and every `no_engine` branch record nothing; an `is_error` result drops the snapshot |
| Grading call | `adapter.process_one` (before the turn) | `routes/chat.py::_run_turn` → `followup_grading.grade_followup` (inside the session turn lock, before `stream_turn`) |
| Switches | chat profile `inject_skills: false` / `outcome_grading: false` | the same two keys in `spec.web_chat` of `tenant.corvin.yaml` |
| Consent | — (bridge has no console session) | active `learning_feedback` consent (`core.compliance.consent.consent_required`, the checker of `POST /learning/feedback`); a refusal or an unverifiable store = no signal |
| Extra gate | — | the previous turn's task must be `COMPLETED`; only skills of the session tenant's own scopes (`session`, `user`) are graded — never the shared repo `project` scope or the `task` scope (`grade_from_user_followup` with an explicit `tenant_id`) |
| Audit | `skill.outcome_graded` (signal, skill names, score) | `console.action_performed`, `action=skill.outcome_graded.<signal>`, `target_kind=skill`, `run_id` = previous task |

Both: the snapshot is in memory only, TTL 30 min, consumed by the first follow-up whether or
not it carries a signal. The console snapshot holds no prompt text (tenant, chat key, run id,
skill ids, timestamp) and is dropped on chat delete; the console therefore produces approval and
rejection signals only, never a rephrase signal. The registry writes its own hash-chained
`skill.grade` record, AUDIT-FIRST: the record commits before the grade is persisted; if it cannot
commit, nothing is persisted, the grade is not reported (no `skill.outcome_graded` /
`console.action_performed`), and no unchained line is appended to the chain: the registry never writes
a raw line (the former `OSError` fallback is gone for hash-chained registries), and the core writer
cuts its own partial append back under the chain lock when a write is short (full disk, file-size
limit; POSIX — Windows keeps the text-mode writer). If persisting the grade fails AFTER its record
committed, a compensating `skill.grade_not_persisted` record (run id + error class) is chained and the
error propagates, so the chain nets "graded, not persisted" and a retry is one grade, not two. Write
order (`SkillRegistry._save`, review c120 r3_s1-2): the DERIVED resolver manifest first, the store of
record `skills_registry.json` LAST (the final atomic rename) — so "persisted" means exactly "the final
rename landed". A failed resolver write inside `grade()` is logged and the grade is still stored, with
no compensation (`derived_best_effort`); only a failure of the final write chains
`skill.grade_not_persisted`, and then the resolver manifest is re-derived from the record on disk. The
dedupe marker lives in the grade note, so it is stored only together with the grade. A failed
`meta.json` copy after the manifest was saved is logged only: the grade is stored and reported.

**Once per 24 h per signal kind (decision):** an organic follow-up grade is written at most once per
skill per channel per signal KIND (approval / rejection / rephrase) in a rolling 24 h window, deduped
at write time under the registry lock (`SkillRegistry.grade(dedupe_marker=..., dedupe_also=...,
dedupe_window_s=86400)` → `DuplicateGrade`; for an active canary candidate, by the `~dd=<marker>`
suffix and the timestamp of the canary grade). An earlier approval therefore never blocks a later
rejection, and two praises at 23:59:59 and 00:00:01 UTC are one window. The marker is
`sha256("followup-grade|<kind>|<UTC day>|<channel>")[:16]`, stored as `dd=<marker>` in the grade note —
never the channel id, and not linkable across days; a new grade is refused when a grade carrying
today's or yesterday's marker is younger than 24 h. The key is ONE rule for both surfaces,
`skill_inject.followup_dedupe_key(scope, channel_id=, tenant_id=)`: the chat (`<channel>:<chat_key>`)
for a `session`-scope skill, `tenant:<tenant>` for every other skill — a `user`/`project` skill is shared
by every chat and every surface of the tenant, so praise from three bridge chats AND the console web
chat in one window is one approval (review c120 r3_s1-4, r4_s1-3; a rejection is still its own kind).
The bridge passes its chat and the env tenant, the console no chat and its session's tenant; both
reach the same key. No production caller passes `dedupe_channel` (a test-only override).

**Console grading gates, in order** (`followup_grading.grade_followup`, all must hold, any failure
= no signal and nothing written): a session tenant → a snapshot for this chat younger than 30 min
(popped, so it is consumed even if a later gate fails) → an active `learning_feedback` consent → the
previous turn's task `COMPLETED` → then inside `grade_from_user_followup`: the tenant switches
`spec.web_chat.inject_skills` / `outcome_grading` not `false`, a detected signal, only `session` /
`user` scope skills of the session tenant, once per 24 h per skill and signal kind (above).

**Detection rules** (precedence rejection > approval > rephrase, unchanged):

* The two sides are deliberately ASYMMETRIC: a false approval is an organic 0.9 that may lift a skill
  over the promotion bar, so approval is conservative; a false rejection only lowers a grade (the safe
  direction), so rejection is liberal.
* Quotation (review c120 r3_s1-1/-3): single- and double-quoted spans (`_strip_quotations`, also
  guillemets and typographic quotes) are removed before BOTH tests, and a message that carried a quote
  is never an approval: quoted praise ("'super'", "he said "great"") and a quoted "try again" / "no"
  are no signal. Not a quotation (review c120 r4_s1-2): inline code in backticks ("Thanks, `make
  build` works now") and a short quoted NAME — at most 4 tokens with no praise, thank-you, success,
  negator or rejection word (`_is_name_span`: the "export" button, das Feld "email") — are replaced
  by a neutral token and leave the praise around them intact.
* Rejection (liberal): a token STARTING with a rejection stem (`_OUTCOME_REJECTION_STEMS`: "falsch" —
  falsche/falsches/falschen —, "inkorrekt", "wrong" — wrongly —, "incorrect") that is not a known
  compound or idiom (`_OUTCOME_REJECTION_COMPOUNDS`: Falschgeld, Falschparker, Falschaussage,
  Falschmeldung, wrongdoing, wrongful, …); a token phrase from `_OUTCOME_REJECTION_PHRASES` ("stimmt
  nicht", "klappt nicht", "immer noch nicht", "not correct", "nicht richtig", "try again", "still
  broken", "same error", …); "nein"/"no"/"nope" followed by `,` `.` `!` or the end, ONLY at the start of
  the message or right after a sentence break — never "item no. 7" — and not before a closing idiom
  (`_OUTCOME_CLOSING_AFTER_NO`: "nein, danke", "nein, sonst nichts", "no, thanks", "no, that's all",
  "no worries", "no problem"); "nochmal"/"nochmals" only when the message starts with it, has at most 4
  words, it opens a clause, or it follows aber/but/doch/bitte/please ("gut gemacht, aber nochmal bitte")
  — never inside a longer request ("Kannst du das Diagramm nochmal in Blau rendern") and never next to a
  thank-you ("danke nochmal", "thanks again" is the praise idiom). At any length, and a `?` does not
  cancel it. A praise token together with a closing idiom ("Perfekt! Nein, sonst nichts.") is no
  signal either way (decision).
* Approval (conservative), all must hold: no `?`; not a request or question, checked in EVERY clause
  (`_looks_like_request`, review c120 r4_s1-1/-2): an imperative or auxiliary at the clause start —
  teste/mach/schreib/prüf/make/write/test/check/please/is/does/can, … (`_OUTCOME_REQUEST_STARTS`) —
  unless it is a noun followed by a predicate (`_OUTCOME_NOUN_VERBS` + `_OUTCOME_PREDICATES`: "Test
  passed", "Plan looks good", "Liste ist perfekt" are statements); a question word
  (wie/warum/wo/welche/how/why/which/what, "was" only at a clause start) followed by a verb or noun
  ("Danke, wie mache ich das", "how do I run it", "how many rows") — but not one that opens an
  exclamation or relative clause ("What a great answer", "Wie toll", "genau wie gewünscht", "exactly
  what I needed", "…, which is great"); a German verb-first clause ("Danke! Klappt das auch mit
  Excel-Dateien", "Ist das auch threadsafe") or a verb-first clause with du/ihr ("Weißt du …");
  "ob"/"whether" anywhere; an elliptic follow-up ("and the same for Postgres", "any chance");
  a present-tense need with a subject ("Danke! Ich brauche noch einen Test", "I also need", "brauche
  ich") unless it is relative ("was ich brauche", "what I need"). "Teste ob es funktioniert" and "Make
  sure it works on Windows" are never approval; no token over
  `_OUTCOME_MAX_TOKEN_CHARS = 40`; at most `_OUTCOME_APPROVAL_MAX_WORDS = 12` tokens, raised to
  `_OUTCOME_APPROVAL_MAX_WORDS_THANKS = 20` when the message carries a thank-you token
  (`_OUTCOME_THANKS_ONLY`: danke, dankeschön, dank, thanks, thank, thx, merci) — both decisions; emoji
  are not tokens; no eye-roll / anger / thumbs-down emoji, and laughing emoji (😂🤣) void it only
  without a thank-you ("Haha super, danke 😂" counts); no sarcasm opener (`_OUTCOME_SARCASM_PHRASES`:
  "na toll", "ja toll", "oh great", "yeah right", "alles weg", …) and no negative continuation after
  the praise ("Great job breaking production", "super kaputt", "Thanks a lot for deleting my file" —
  the complaint words in `_OUTCOME_APPROVAL_VOIDERS`, sarcasm by continuation); no 'd contraction; no
  negator anywhere (single tokens and multi-word forms "alles andere als", "far from", "not really",
  …) except inside a positive idiom (`_OUTCOME_POSITIVE_IDIOMS`: "nicht schlecht", "not bad", "no more
  errors", "keine Fehler mehr", "Fehler ist weg", "weiter so"); no void cue
  (`_OUTCOME_APPROVAL_VOIDERS`: a new request or contrast "bitte"/"weiter"/"neue"/"next"/"please"/
  "aber"/"but", a complaint or reversal word — "kaputt", "down", "leer", "slower", "overwriting",
  "gelöscht" —, a conditional / irrealis, a concession or dismissal, a thank-you for work not yet done
  or for something else — "im Voraus", "vorab", "in advance", "ahead of time", "schon mal",
  "gestern", "übrigens" —, a tag question "oder"; `_OUTCOME_VOID_PHRASES`). "jetzt"/"now" do NOT void
  praise ("Much better now, thanks", "Danke, jetzt verstehe ich es"): a new request after them is
  caught by the imperative at the clause start ("Great, now add tests"), a reversal by a complaint word
  or a negator ("Perfect, now nothing works"); "wieder"/"again" count next to a success verb ("works
  again"). Then ONE of: a
  phrase from `_OUTCOME_APPROVAL_PHRASES` anywhere ("gut gemacht", "hat geklappt", "passt so", "well
  done", …); a weak success word (`_OUTCOME_WEAK_APPROVAL_PHRASES`: "funktioniert", "klappt", "läuft",
  "works", "worked", "excellent", "brilliant", "works like a charm") ONLY together with a thank-you —
  "Ich will dass es funktioniert" is no approval; or a WHOLE reaction — every token an approval phrase,
  a weak success word, a generic word (`_OUTCOME_APPROVAL_EXACT_WORDS`: gut, top, passt, richtig, …) or
  a filler (`_OUTCOME_FILLERS`: es, das, so, ist, it, now, …): "Top, passt!", "Richtig, so ist es.".
  Generic words never count inside a longer sentence. A voided praise is NO signal, not a rejection.
* Measured on an INDEPENDENT holdout (review c120 r5_s1: 146 messages, 21 classes, DE+EN, written by
  the reviewer before reading the detector source or the fixer's tables; origin/main → this tree):
  approval recall 0.852 → 0.667 (46 → 36 of 54), precision 0.622 → 0.973 (false approvals 28 → 1);
  rejection recall 0.295 → 0.523 (13 → 23 of 44), precision 0.867 → 1.000 (false rejections 2 → 0).
  Per class (correct / rows, origin/main → tree): praise_de 11/12 → 11/12, praise_en 8/12 → 10/12,
  praise_litotes 3/4 → 3/4, praise_case 6/6 → 6/6, praise_emoji 5/6 → 5/6, **praise_then_request
  5/6 → 0/6, praise_then_question 4/4 → 0/4, praise_long 4/4 → 1/4**, reject_de 3/10 → 7/10,
  reject_en 5/10 → 7/10, reject_negated_praise 0/6 → 0/6, reject_emoji_case 2/5 → 3/5, sarcasm 0/8 →
  1/8, polite_correction 3/5 → 5/5, request 8/8 → 8/8, request_praise 4/8 → 8/8, question 7/10 →
  10/10, innocent 5/10 → 10/10, quoted 1/5 → 5/5, thanks_in_advance 0/4 → 4/4, emoji_neutral 3/3 →
  3/3. **Trade-off (decision for the operator):** the detector is stricter than origin/main for
  approval by design — a false approval can promote a skill; the measured cost is recall on praise
  followed by a question or a new request ("Thanks, that worked. How do I run it on Windows?",
  "Great, that works. Now add logging please.") and on reactions over 20 tokens, which give no
  organic grade on either surface. The earlier claims "approval recall 0.95 → 0.98, rejection recall
  0.72 → 0.94" were measured on the fixer's own 147-row holdout, whose praise-plus-question rows are
  labelled no-signal and whose long class holds no positive row; they describe that table's class
  mix, not the detector, and are withdrawn. The round-3 claim "precision 1.00, recall 0.95" was
  likewise measured on its own tuning table (recall 0.31 on a fresh one, review c120 r4_s1).
  Known limit (review c120 r5_s1-2, behaviour unchanged): negated praise gives NO signal on either
  tree ("Das ist nicht gut.", "That wasn't helpful.", "Nicht wirklich hilfreich.", "Not perfect, the
  header is missing.": 0/6), and neither do "This is useless.", "Bad answer.", "Schlechte Antwort.",
  "Völlig daneben.", "It crashes now.", "👎" or "nope 👎" (thumbs-down only voids an approval) — the
  "rejection stays liberal" design does not cover negated praise. "deleted" is a complaint word
  ("Perfect, it deleted my whole folder." is no approval, review c120 r5_s1-3).
  Known residue on the fixer's holdout: "Super, das ist genau das, was ich gesucht
  habe!" is no approval ("super" is a generic word, which counts only as the whole reaction —
  decision; origin/main approved it by substring); "Das stimmt so nicht." is no rejection (the phrase
  "stimmt nicht" is not contiguous); "My manager wants a perfect slide deck by Monday." and "Die
  Antwort an den Kunden soll mit Danke beginnen." are still false approvals (as on origin/main). On the
  tuning table "Ich brauche ein Regex, das inkorrekte IBANs erkennt.", "Explain why my old code had
  the same error message.", "Validiere das Formular, falsche Eingaben sollen rot werden." and "Write a
  unit test for incorrect password handling." read as rejection (a real stem or phrase; rejection
  stays liberal on purpose), and the closing-idiom rows are no signal by decision.

**Privacy:** the user's text is read, never stored — on the console not even the previous prompt is
kept in memory. The grade note names only the signal, the previous run id and the day-bound dedupe
hash; the learning-inbox event carries skill id + signal; the console audit carries the enum.

**Scores and the cap are unchanged:** approval 0.9, rejection 0.1, rephrase 0.3. The follow-up grade
is `organic=True` and is NOT clamped to `AUTO_GRADE_CAP_MAX` (0.3): one seed grade plus one approval
reaches a mean of 0.6, above the session→project promotion bar. Whether that should be bounded
(e.g. a minimum number of organic grades before promotion) is an open operator decision.

Proof: `corvin_operator/bridges/shared/test_outcome_praise_e2e.py` (phrase/negation table + three
`adapter.process_one` turns), `core/console/tests/test_followup_grading_ws_e2e.py` (two turns over the
real chat WebSocket: approval, rejection, negated/incidental praise, no consent, tenant opt-out, a
skill the renderer dropped, `/debug-engine`, an error answer, a cancelled turn, another tenant's project-scope skill with a
real repo-shaped project root, no prompt text in the snapshot, chat delete, a failing `skill.grade`
writer with `verify_chain` and the ADR-0232 `audit_chain_intact` tripwire green, 24 h per-kind dedupe),
`corvin_operator/skill-forge/tests/test_grade_audit_first.py` (MCP `skill_grade` over the JSON-RPC loop
with a failing writer: error response, nothing persisted, chain verifies).
`test_outcome_praise_e2e.py` also covers the r2 table rows, a same-day rejection after an approval and
the rolling 24 h window across UTC midnight through `adapter.process_one`;
`test_followup_grading_ws_e2e.py` covers liberal rejection, sarcasm/conditional/concession, praise
recall, approval-then-rejection, a failed save after the record (compensated, retry nets one grade) and
a failed `meta.json` copy (grade still reported) over the real chat WebSocket. Round 4 (`-k r4` in
both files): every fixed praise class with a near-miss control, questions/requests after a thank-you,
"Write the answer as: No.", and a praise on the bridge (`adapter.process_one`) plus one in the console
WebSocket in one window — exactly one 0.9 grade, in either order.

## Registry hardening (2026-09-07 adversarial review F-K2/F-K3/F-K5/F-K6/F-K8)

| Mechanism | Where | Rule |
|---|---|---|
| LoM required AND resolvable | `SkillsRegistry.execute(..., lom=)` | `lom="<file>:<function>"` (or `<file>:<function>:L<line>`) is mandatory; missing OR unresolvable → audited `skill.executed` with `status=error`, the Skill does NOT run. `lom_hash` = SHA-256 of the named function's source segment (`ast`), so it survives line drift; for the `:L<line>` form the FUNCTION is resolved first and the line must fall inside it (decorators included) — before 2026-09-07 that form hashed the line without ever looking the function up, so a fabricated name still "bound", and a blank line produced the constant `sha256("")` (R3-B1). A blank line, a non-`.py` target, anything outside the repo root and anything under `.corvin/`, `.claude/`, `.venv/`, `site-packages/`, `node_modules/` or `.git/` are all refused — the exclusion set names every root the RUNNING SYSTEM can rewrite, and `.claude/` was missing from it until 2026-09-07 (round-4 review, F8) while `.claude/worktrees/` held 17 full `.py`-bearing copies of the repo inside the repo root, so a LoM naming one bound and produced a normal-looking source hash for source that is not the shipped source, and a source file > 2 MB is not parsed. Resolution is memoised on `(lom, path, mtime, size)` — `execute()` resolves twice per call and re-parsing cost up to 80 ms each time. Production call sites: `capabilities.py:_read_flags_uncached`, `slash_commands.py:_plugin_builder_enabled`, `vibe_engineering.py:get_pipeline`, `bootstrap.py:start_health_monitoring`, `delegation_policy.py:_consult_router`, `console/app.py:headless_enabled`. A call site that forgets `lom=` is REFUSED and the refusal is SILENT at the call site — it just reads as "the feature is off" — which is how `headless_api_mode` shipped dead. `tests/integration/test_phase1_k2_k5_call_sites.py::TestEveryProductionCallSitePassesALoM` is an AST fence over `core/`, `operator/` and `ops/` that fails on the next one. |
| Decision in the chain | `SkillExecutionResult.to_audit_event` → `decision_summary()` | the core writer drops any `output` key; the chain now carries an allowlisted `decision` (engine, enabled, mode, confidence, shadow/bundled_engine, `flag_count`/`flags_on`/`flags_hash`) — never free text. Field sets are registered as positive allowlists (`SKILL_AUDIT_ALLOWLISTS`). |
| Compliance tier | `SkillMetadata.tier` (`compliance` / `core` / `installed`) | `os.capabilities` is `compliance`: `unregister()` / `disable_skill()` raise `SkillDisableRefused`, the 3-failure auto-disable is refused — every refusal is audited as `skill.disable.refused`. |
| Lost-update guard | `SkillAdapter._locked()` | `fcntl.flock` on `<config>.lock` + RELOAD inside the lock around `run_optimizer_epoch` / `rollback`; two concurrent feedback requests advance the epoch by two, never one. |
| Namespace gate | `skill_forge.registry.SkillRegistry(caller_persona=…)` | create/update/delete/grade/promote are gated on `forge.policy` namespaces when a persona is attached (MCP server hands its turn persona over; console routes act as `assistant` → `assistant.*` only, 422 otherwise). Fail-closed when the policy cannot be loaded. |
| Grade cap | `SkillRegistry.grade(..., organic=False)` | non-organic grades (self-award, auto, bootstrap) are clamped to `AUTO_GRADE_CAP_MAX = 0.3` and audited `capped`; only a caller vouching for a real run passes `organic=True` (MCP `skill_grade` with a `run_id` from a NAMED wildcard persona only — the operator names `skill-forge` or `forge`; a NAMESPACED persona's MCP grade is a self-award and is capped, its run id is a string it chose; `skill_inject.grade_from_user_followup` — an outcome grade backed by the previous turn's `run_id` and the operator's own follow-up). The post-turn auto-grade stays non-organic. The note prefix `outcome` is reserved for organic grades (the autopilot's loss signal reads it): a non-organic grade carrying it is stored as `self-reported: …`. A namespaced persona may not pass `force=True` to MCP `skill_promote` (refused, `skill.scope_denied`), the same rule as for a project/user create (review 2026-11-05 LR-1..LR-3). The MCP server takes its caller from `CORVIN_CALLER_PERSONA`, else `SKILL_FORGE_PERSONA` (the name the cowork resolver puts on the server's own env, which now carries both); an ABSENT persona is not a wildcard: every mutating tool is refused (`namespace-gate: no caller persona`) and it gets no force, no project/user create and no organic grade. Until then the console web-chat worker - wired by the resolver, never exporting `CORVIN_CALLER_PERSONA` - was a wildcard caller that could force-promote session->user and grade its own run ids organically (LRN-1, `tests/learning/test_review_20261105_lrn_hosts_e2e.py` starts the server from the console's own `--mcp-config`). `grade()` is audit-first: the `skill.grade` record commits before the grade is persisted; a failed record raises and persists nothing. |
| Ungraded injection | `skill_context.resolve_inject_ungraded` | conjunctive: `CORVIN_DELEGATE_INJECT_SKILLS_UNGRADED=1` alone never widens; `=0` always narrows. |

## Invariants (must NOT be weakened)

* The bundled routing answer is never altered in shadow. In a CLEARED Phase 2 the
  Skill may only de-escalate to `native` (the ADR-0251 D2 bound hooks already obey),
  never over an explicit `/delegate`, and only after `routing.phase2_decision`
  committed to the chain (ADR-2092 G3/G4).
* **An outcome has three kinds: `ok: true`, `ok: false`, NOT-JUDGED** (ADR-2092 G1 as
  amended 2026-10-27, both surfaces). A turn that was cancelled (console operator stop, or an `rc == -1` cut with no noted
  reason; bridge `/cancel`), killed by a restart (a console host shutdown cut, noted
  `shutdown`, and the boot reaper, which marks the console task `orphaned_on_restart`
  and records the routing turn named by the task's `task.routing` event), or in which no
  engine ran (gate/budget/quota
  refusal, debug engine, empty `/use-engine` directive) is recorded with `"ok": null`
  and `"excluded": "<cancelled|orphaned|no_engine>"` (closed set,
  `routing_ledger.EXCLUDED_REASONS`). It is counted and visible (`summarize()` →
  `not_judged`, `not_judged_by_reason`; `Readiness.not_judged`) but excluded from the
  join-rate numerator AND denominator, from every success rate and from the rollback
  judge. A bridge refusal is never a native success any more; a cancelled ACS run is
  never an ACS failure. A client disconnect does not cancel a console turn (the turn is
  detached and finishes). The detached-turn watchdog (`detached_timeout`,
  `routes/chat.py::_DETACHED_TURN_MAX_S_DEFAULT` = 1800 s, overridable by the env `CORVIN_DETACHED_TURN_MAX_S`, minimum 60 s) is a JUDGED failure (`ok: false`) of the served engine
  (`chat_runtime._cut_turn_outcome`): a hung engine reaches the rollback judge and every
  success rate. On the bridge a turn the Skill routed (`route_source: skill`) onto an
  engine that cannot run here (`engine_unavailable`, e.g. no claude CLI after a
  de-escalation to native) is a JUDGED failure of that engine, not `no_engine`; the
  bundled rule's own refused turns stay `no_engine`. A bridge ACS/TDE delegation that
  returns no answer is judged as THAT engine, like the console (review DLG-12,
  2026-11-05): `used: acs|tde, ok: false` when it ran and failed, `excluded: no_engine`
  when its gate refused it before it ran; only a spent compute pool records the native
  turn that served (the console's quota fallback). Before, the native degrade was
  recorded, so the bridge's delegated rows held only successes. A bridge whose
  `bridge_worker_engine_parity` flag is off turns the rule's `acs` into `native` BEFORE
  the decision row is written (DLG-13), so no row names an engine that cannot run there. Old ledger lines (no `excluded` key, bool `ok`) still read as
  judged. A bridge turn killed by a restart has no task reaper link and still counts as
  a missing outcome.
* **The bridge soak (ADR-2092 G4) is continuous** (`readiness.console_soak_start`):
  the bridge may enter Phase 2 only when the console has been in dual_write for
  `CONSOLE_SOAK_S` (7 days) since the LATER of (a) the oldest console decision of the
  current unbroken dual_write run, (b) the last rollback trip and (c) the last rollback
  reset. The run is walked newest-first over the console decisions with `gate: verdict`
  (a line without `gate` reads as one) or `gate: pending`, and is broken by (1) ANY
  such decision recorded `phase: shadow` (env switched back, a refusal, a not-ready
  verdict, a rollback — and every `pending` decision: no verdict existed yet after a
  start or invalidation, or none younger than 1 h, so the turn was served shadow) and
  (2) a gap of more than `SOAK_MAX_GAP_S` (24 h) between two consecutive dual_write
  decisions or between the newest one and now. In addition at least
  `SOAK_MIN_DUAL_WRITE_SHARE` (95 %) of ALL console decisions since the soak start —
  `post_hoc` ones (the engine had already run; pinned shadow) included — must be
  dual_write (review r3_s2-2: one dual_write burst a day kept a 95.8 %-pending-shadow
  week's run alive; on that synthetic ledger the soak is now none). Consequence: a
  console whose turns are routinely more than 1 h apart gets a pending turn after
  every pause and cannot complete the soak. An unreadable rollback
  state is no soak (fail-closed). The phase read is the EFFECTIVE phase recorded per
  decision: `CORVIN_ACP_PHASE=phase2_dual_write` on a console whose own gates do not
  hold records `shadow` and starts no soak.
* An explicit `/use-engine tiered_delegation` choice never reaches the router: no Phase-2
  decision can be audited for a route the TDE branch does not serve; it is recorded post hoc
  as a forced TDE route (`force_delegate`, shadow).
* The core chain admits only the PROCESS tenant (ADR-0007). A learning event
  for another tenant is refused by the writer, detected by the read-back, and
  **not written to disk** — counted in `EventEmitter.write_failures`. Tests
  that write for tenant X run as tenant X (`CORVIN_TENANT_ID`).
* `core/paths/tenant.py::corvin_home()` honours `CORVIN_HOME` (then a
  repo-local `.corvin`, then `~/.corvin`) — the same order as
  `corvin_operator/bridges/shared/paths.py`. Nothing in learning/skills may hard-wire `~/.corvin`.
* The free-text feedback `reason` is used by the interpreter's keyword rules
  and is never persisted or chained.
* `SkillConfig.apply_delta` clamps to `[0, 1]` and rejects unknown params;
  `ConfigHypothesis.delta` is bounded to `[-0.20, 0.20]` — one feedback can
  move a parameter by at most 0.20, and every accepted change is reversible.
* Learning brake (ADR-2304): a freeze is audit-first, only stops writes, never rolls back,
  never lifts itself, and has no flag, env variable or setting; it covers `SkillAdapter` AND
  the DoD weights. Only `skill.config_oscillation` / `skill.confidence_regression` freeze; the
  DoD weights freeze on the oscillation counted from their chained `skill_config_updated`
  records (their weight file has no versions), never on confidence (their `skill_executed`
  carries none).
* The `:unverified_causality` label (ADR-2305 Part 1) is always present on an accepted hypothesis.
  No Phase 2 state removes it; only the causal test (Part 2), which does not exist yet, may.

## Verification

```bash
# unit + E2E (real HTTP boundary, real store, real chain in a sandbox)
.venv/bin/python -m pytest core/learning/tests/test_event_store_audit_first.py \
    core/learning/tests/test_outcome_sink.py core/skills/tests/test_skill_adapter_persistence.py \
    tests/test_delegation_policy_acp_shadow.py -q
core/console/.venv/bin/python -m pytest core/console/tests/test_learning_loop_routes_e2e.py \
    core/console/tests/test_task_manager_outcome_sink.py -q

# LIVE with a REAL LLM turn (real claude CLI; costs credits)
CLAUDE_LIVE_E2E=1 core/console/.venv/bin/python -m pytest core/console/tests/test_learning_loop_live_e2e.py -q -s
# → [live] loop closed: shadow=1 outcome=1 hypotheses=1

# live service (after a restart of corvin-webui.service)
curl -s -c c -b c http://127.0.0.1:8765/v1/console/auth/local-login >/dev/null
curl -s -b c http://127.0.0.1:8765/v1/console/learning/health
grep -c '"learning.skill_executed"' "$CORVIN_HOME/audit.jsonl"
```

## Known limits (stated, not hidden)

* The L10 entry point `adapt_context_l10` (`os.context_adapter`) runs in shadow
  since 2026-09-26 (register L11, state B): the CEL stage `l10_adapter` calls it once per turn from
  `pipeline.build_context`, audits `skill.executed` + `context.adapted`
  (`adaptation_type=l10_shadow`) and never changes the served brief. The Skill
  receives content-free inputs only (no task text), so its advice carries almost
  no signal yet. It runs only in a process where `boot_skills` booted an audited
  registry for the turn tenant (gateway/console, and the bridge adapter via
  `adapter._boot_acp_skills`); a process without one records
  `skipped: skills_not_booted` rather than an unaudited execution. **A
  hand-authored `spec.context_engineering.pipeline` REPLACES the defaults**, so it
  must list `- stage: l10_adapter` itself or the stage never runs for that tenant
  (the live install had 0 `context.adapted` records for exactly this reason until
  2026-10-11); `resolve_pipeline` logs a one-time warning when it is missing.
* **Unwired learning scaffolding was deleted (2026-10-11, T-0125).** 117 of the 184 modules
  in `core/learning/` (about 39 000 lines, e.g. `unified_loss_optimizer`, `feedback_batcher`,
  `phase2b_*`, `nine_d_loss`, variants such as `confidence_scorer_a3`, `meta_optimizer_a4`,
  `phase7_orchestrator_bridge` and the ADR-0393 classifier family) had no path from any production root and were removed together with the
  tests that exercised only them. What remains: `EventStore` / `outcome_sink` (events, register
  L03, A), `stream4_skill_feedback` -> `loop_closure.apply_outcome_to_skill` -> `SkillAdapter`
  (the one feedback path that writes a config, register L05, B: its reader is the gated Phase 2),
  `model_selection_*` (descriptive statistics over proxies, L08, A: shown on the Models page,
  never read by the selection) and the dashboards (L17, A). The operator-triggered epoch test of
  `POST /learning/feedback` also writes the router threshold (L05). There is no automatic
  optimizer: nothing reads a learned router threshold outside the gated Phase 2 path, so an
  optimizer for it would close no loop. Method note: a plain importer graph MISSES
  names re-exported by `core/learning/__init__.py` (`from core.learning import LearningIntegration`
  is a production import of `integration`); resolve re-exports before calling anything dead.
* **ADR-2157's SkillScore modules retired (2026-10-26, ADR-2296, T-0282).** `core/skills/confidence_calculator.py`,
  `confidence_metrics_calculator.py` and `ab_testing_optimizer.py` (972 lines) and their three test files were deleted:
  the zero-caller sweep showed no entry point reaching them, and the two documents ADR-2157 names never existed.
  What they described is covered by skill grades and the injection gate (L06), canary A/B (ADR-2094) and the
  feedback gate (L04). `core/skills/` stays outside the ADR-2281 ratchet scopes; a retired path is held by
  `tests/learning/test_adr_2282_retired_modules.py`.
* **Second clean-up round in `core/skills` (2026-10-11, T-0140).** 66 more modules (about 15 000
  lines: the marketplace installer/core/skill integration, A/B-testing framework, batch approval,
  config applier/tuner, the `graders/`, `orchestration/` and `feedback/` packages, the
  `context_filter*` and `knowledge_graph` skills, older copies inside `flow_guard/`,
  `workflow_optimizer_skill/` and `security_orchestrator/`) and the tests that exercised only them
  were removed. NOT removed, on purpose: anything an ACCEPTED ADR names by file or by its own
  directory (decided, not yet wired: e.g. `skill_validator`/`compiler` for ADR-0533, `executor`,
  `health`), the Data Hub learning group (PLAN-0948), the Video Producer, and everything those
  import. Method note: a package's `__init__` runs when ANY child is imported, so a live module
  also makes its parents' eager imports live; the first draft of this round missed that and
  would have broken four live packages (caught by a dangling-import check before any commit).
* **Third round, by decision (ADR-2282, 2026-10-25).** The never-wired learning code that
  accepted decisions still described was retired together with those decisions, group by
  group (T-0264..T-0272), each named with its live successor: L5 K3–K5
  (`quality_gate`/`conflict_resolver`/`rollback_guard`/`utils`), the LDD in-task loop and
  `base.LearningLoop` (DoD learning moves to `weight_optimizer`), `loss_signals` (successor
  `anomaly_monitor`), `outcome_sink_a2` (successor `outcome_sink.emit_task_outcome`),
  `completion_detectors` + `core/notification` (successor `completion_notify` + the
  workflows outbox), `token_accounting`/`token_metrics_aggregator`/`vibe_metrics_api`
  (successor `engine_span.usage_split`), the legacy `core/console/routes/` tree, the six
  flow-guard learning modules (`threshold_tuner` lowered an L34 threshold and
  `exception_system` forced ALLOW — both contradicted ADR-2049; L34 itself is unchanged),
  `workflow_optimizer_skill/confidence_calculator.py`, and the CEL stages
  `selective_injection`/`memory_pruning`/`adr_reranking` (in no shipped pipeline; their
  "embedding" was a salted `hash()`; `context_priority` is the live stage), 11
  `core/marketplace/*` learning/distribution modules (successors `corvin_plugins/trust.py`,
  the manifest origin axis, `state.py`, `routes/marketplace.py`; `custom_repositories.py` and
  `auth/` stay), and the Flask `routes/learning_loops.py` with `learning_loop_manifest` /
  `learning_loop_audit_integration` / `learning-loops-dashboard.tsx` (successor
  `learning_analytics.py` + `loop_discovery.py`, ADR-0908), the live experiment collector
  (successor `maturity_live.build_measurement`) and the Stream-2 optimizer route with its eight
  `*_stream2.py` modules and `components/learning/` (successor `stream4_skill_feedback`, same
  feedback gate), and the Brain hub (`hub`/`brain`/`brain_startup`/`config`/`cli` and
  `core/orchestration/subsystems/`, except `notification_daemon.py`, which the unreachable
  `core/skill_forge/skill_forge_v2.py` still imports), plus the parallel skill optimizers
  (`core/skills/{skill_learning_loop,skill_optimizer}`, `core/learning/skill_optimizer`,
  `core/engine/{meta_skills,skill_learning_loop}`, `skill_learning_routes` +
  `skill-learning-dashboard.tsx` — successor: SkillForge grading/canary/promotion),
  `auto_grading` (it would let a skill promote itself past the 0.3 auto-grade cap),
  `operator_fingerprint`/`adaptive_strategy`/`confidence_optimizer`/`tool_ranking(_cache)`
  and `core/vibe/` (successor `anomaly_monitor`). Registered audit
  event types of retired emitters stay in `EVENT_SEVERITY`/`_EVENT_ALLOWLIST` (old chain
  records may carry them).
* **The one config-writing feedback path (register L05) is race-tested across processes.** `SkillAdapter.apply_config_delta`
  is a read-modify-write of `<skill>_config.json` shared by the console and the bridge;
  `tests/skills/test_skill_adapter_cross_process_race.py` runs 4 real processes x 6 deltas and
  requires 24 unique gapless versions and the exact end value (with the `flock` removed only 6-7
  of 24 survive). An HTTP-level concurrency test cannot show this: requests share one event loop
  and the handler has no `await` between read and write. `EventEmitter` accepts an event only if
  it precedes `stop()`'s sentinel (one lifecycle lock; `tests/unit/test_event_emitter_races.py`).
* **Phase 2 readiness has a structural dead end, and says so.** The Skill may only
  de-escalate TO `native`, so a surface where the bundled rule never delegates has
  nothing to change. `monitoring/readiness.py` reports that as
  `no_delegation_subject` (with `delegating_decisions == 0`) as soon as 30 decisions
  exist, instead of `insufficient_samples`, because more samples cannot help.
  `skill_never_disagrees` now means the Skill HAD delegations to question and never
  disagreed. On 2026-10-11 both live surfaces were in the first state (console 131
  decisions, bridge 560, 0 delegating); `console_soak_incomplete` now carries the
  bridge's real counts instead of a hard 0. Unblocking Phase 2 therefore needs
  delegating traffic (an operator decision on `web_chat.worker_engine`), not time.
  Measured again 2026-10-25 (T-0280, read-only on the live tenant, recorded in the
  register entries L05/L12): both surfaces now delegate and neither is ready. Console:
  365 decisions, join 0.923, 36 delegating, 0 disagreements → `insufficient_samples`
  (and the join rate is below 0.95). Bridge: 962 decisions, join 0.989, 26 delegating,
  1 disagreement → `console_soak_incomplete`. No Phase-2 decision is proposed.
  Re-measured 2026-10-27 on a read-only copy of the live ledger with the amended
  gates: console 369 decisions, join 0.9241, 36 delegating, 0 disagreements →
  `insufficient_samples`; bridge 1072 decisions, join 0.9897 → `console_soak_incomplete`
  (no console decision has ever been recorded with an effective `dual_write` phase).
  No historic line is NOT-JUDGED: the exclusion applies to turns recorded from now on.
  Had the 24 NOTE-0011 console turns (23 restart kills, 1 pre-spawn refusal) been
  recorded NOT-JUDGED, the console would read 345 decisions, join 0.9884.
* `DelegationRouterSkill` advises in model tiers (`claude-haiku-4 / sonnet-4 /
  opus-5`), not in `delegation_policy` engine ids (`native / acs / tde`); the
  shadow record carries both so agreement is measurable, but the advice cannot
  be promoted to a real override without a vocabulary mapping (follow-up ADR).
* Learning events for a session tenant that is not the process tenant are
  refused fail-closed (see invariants) — one process per tenant is the
  supported multi-tenant deployment for learning.

## Operator skill feedback route (Stream 4)

`POST /v1/console/feedback/skill` (mounted from `routes/stream4_skill_feedback.py`) takes a
rating in `-2..2` for `os.delegation_router` / `os.workflow_optimizer` /
`os.security_orchestrator` / `os.flow_guard` (`VALID_SKILLS`).
The rating's sign becomes the OUTCOME signal (`>0` yes, `<0` no, `0` other).
The route is consent-gated like `POST /learning/feedback`: without an active `learning_feedback`
consent for the session's consent subject it answers 403, records `console.action_denied`
(`reason: consent_required`) and gates, chains and stores nothing. It passes the ADR-0534 trust
gate itself (refusal → 422) before anything is chained. The route chains a
`skill_feedback` event FIRST (signal only — no free text); if the chain does not commit it
answers 500 and stores nothing. It then appends the structured record (ids, rating, category —
never the free-text reason) to `<tenant>/learning/skill_feedback/events.jsonl`
(`core/skills/feedback/history_store.py`). `GET …/history?skill_id=` and `GET …/status` read that
file only; `status` reports `total_received` and `last_feedback_timestamp` and nothing unmeasured.
Loop closure (`core/skills/feedback/loop_closure.py::apply_outcome_to_skill`): for a skill in
`CONFIG_CONSUMERS` (today only `os.delegation_router`, whose `_learned_threshold` reads the
config back through `load_skill_config`) the outcome moves `confidence_threshold` by +/-0.05 via
`SkillAdapter.apply_config_delta` (audit-first, versioned, rollback-able). The response carries
`config_applied` / `config_version`; a rating of 0 ("other") and a threshold already at its bound
change nothing; for every other skill the feedback is audited and stored only, and the message
says the skill reads no learned config yet. A config write that cannot be audited fails and the
feedback stays stored. The rating names an arbitrary `subject_id` and is joined to no routing
decision, so it is a **proxy** of routing correctness (register L05, `target: none`); the
threshold it moves acts only in a cleared Phase 2.
Proof: `tests/e2e/test_stream4_skill_feedback_console_e2e.py`.

**Where an operator rates (2026-10-13).** Until then no reachable console surface posted here — the
four `pages/skill-feedback/*` pages and `components/learning/FeedbackModal.tsx` (retired by ADR-2282) were mounted nowhere
and called endpoints that do not exist — so the tenant had 0 feedback in two weeks. The Learning Loops
detail view now shows "Rate recent decisions" for the four skills above
(`components/learning-loop-rating.tsx`): `GET /v1/console/feedback/skill/recent?skill_id=` lists the
latest `skill_executed` records (event id, time, and the decision only when it is a short identifier —
never input, output or reasoning text), and Correct/Wrong posts `rating ±1, category accuracy` with
the event id as `subject_id`. Proof: `core/console/tests/test_learning_anomalies_e2e.py`.

## DoD weight loop (ADR-2283 decision 3, ADR-2284 decision 1, 2026-10-25)

Learned Definition-of-Done weights persist per tenant in
`<tenant_home>/global/forge/dod_weights.json` (`weight_persistence.weights_path`, resolved through
`core.paths.tenant.tenant_home` — `CORVIN_HOME` honoured, never `Path.home()`; atomic write, mode
0600). The file holds the weights, their per-weight confidence and the sample count; all three are
loaded back, so the confidence gate survives a restart.
Proof: `tests/learning/test_adr_2283_dod_weights_tenant_path.py`.

The loop (`core/console/corvin_console/routes/dod_verifier_dashboard.py`):

1. `POST /v1/console/api/dod/verify` scores with `DoD_WeightOptimizer.recommend_next_weights(task_type)`
   (injected into `DoD_VerifierSkill` as `weights_for`): per weight the learned value only when its
   confidence is > 0.5 (ADR-0723), else the default; then clamped to [0.05, 0.6] and normalised to
   sum 1. The response says `weights_source: default|learned`. It records a `SKILL_EXECUTED` and an
   `OUTCOME` learning event, both carrying `task_type` — the first is the execution the ADR-0534
   reality check needs (without it every DoD rating was refused, and the route answered 500).
2. `POST /v1/console/api/dod/feedback {task_id, check_name, feedback}` looks up the newest verification
   of `task_id` (none → 422), requires a learnable task type (`cli_command`, `api_endpoint`,
   `lib_function`, `plugin`, `skill`, `feature` — anything else → 422; it is never folded into another
   type's bucket) and a live check key (else 422), then writes FEEDBACK through
   `EventStore.write_event` (the ADR-0534 gate: throttled → 429, refused → 422).
3. The verdict maps to a signed delta (`accurate` −0.20 → the check's weight up, `inaccurate` +0.20 →
   down, `not_applicable` +0.10 → down by half a step; the optimizer caps one rating at 0.05) and
   `observe_feedback` runs on the tenant's restored state. The change is chained FIRST as
   `skill_config_updated` (`param_name weights.<task_type>`, `task_type`, `old_weights`, `new_weights`,
   `confidence` — numbers only, never the `note`); a change that cannot be chained is not persisted (500).
   Then the tenant file is written.

Each rating raises the confidence of one weight by 0.1, so a weight is first applied after its sixth
accepted rating. Feedback traffic is near zero today: the loop is wired but mostly idle. More than
3 chained weight changes in one hour freeze the weights (learning brake, below); a frozen weight
file answers 409 `learning_frozen` and stores no change.
Proof: `tests/learning/test_adr_2284_dod_weight_loop_e2e.py` (verify → 6 ratings → verify over HTTP;
defaults until the gate, one chained record per change, the gate's throttle refuses a burst).

## Learning anomalies (ADR-2044 Phase 3, 2026-10-13)

`core/learning/anomaly_monitor.py` measures its conditions over data the loop already stores;
`LearningHost.tick` runs it every 300 s (`ANOMALY_CHECK_S`), edge-triggered and audit-first: an
anomaly is recorded once when it starts (`audit_sink.emit`, WARNING) and marked active only after
the record committed; it is forgotten when the condition clears.

| Event | Condition | Source |
|---|---|---|
| `skill.high_rejection_rate` | > 20 % of ≥ 10 gate decisions on one subject refused in 24 h | `<tenant>/learning/feedback_gate/decisions.jsonl` |
| `skill.config_oscillation` | > 3 config versions of one skill in 1 h; for the DoD weights > 3 changes in 1 h | `<tenant>/skills/*_config.json` (`versions`); DoD: the tenant core chain's `skill_config_updated` records of `os.definition_of_done_verifier` (`param_name: dod_weights`) |
| `skill.confidence_regression` | mean `output.confidence` of the last 24 h ≥ 0.10 below the 6 days before (≥ 20 each) | `skill_executed` learning events |
| `skill.convergence_stall` (ADR-2284) | newest ≥ 20 (≤ 200) OUTCOME events of a skill in four equal windows: loss `1 − success_rate` > 0.20 in every window, max − min < 0.02 | OUTCOME learning events (`signal.success`, else `signal.passed`) |
| `skill.latency_regression` (ADR-2284) | p95 of `latency_ms` over the last 24 h > 1.5 × the p95 of the 6 days before (≥ 20 each side, nearest-rank p95) | `skill.executed` records of the tenant CORE chain (legacy `skill_executed` too; newest ≤ 50 000 read, needle-filtered) — the learning store's `SKILL_EXECUTED` signal carries `execution_time_ms`, not `latency_ms`, so the chain is the stored source |

Every condition's event is registered statically in `EVENT_SEVERITY` / `_EVENT_ALLOWLIST` for the
ADR-2284 ones (metadata only: skill id, counts, ratios) and at runtime through
`audit_sink.register_events(anomaly_monitor.EVENTS)` for all.

Consumer: the host's persisted state (`anomalies`), returned by `GET /v1/console/api/learning/daemon`
and shown in the "Anomalies" card of the Learning Loops view (an ADR-2284 event shows its event name
there; the card has no dedicated wording for it yet). Three of them only report; the two below
freeze (learning brake).
Proof: `tests/learning/test_anomaly_monitor.py`, `core/console/tests/test_learning_anomalies_e2e.py`,
`tests/learning/test_adr_2284_anomaly_conditions.py` (seeded store → one learning-host tick → one
chained record → the daemon route; no second record on further ticks).

The oscillation anomaly names the skill by the config file's own `skill_id` (what `SkillAdapter`
writes, e.g. `os.delegation_router`); until 2026-10-26 it fell back to the file stem
(`os_delegation_router`) because the persisted versions carry no skill id.

### Learning brake (ADR-2304, PLAN-0966 WP3, 2026-10-26)

An anomaly may stop a change, never cause one. `anomaly_monitor.FREEZES_LEARNING` =
`skill.config_oscillation`, `skill.confidence_regression` (operator chat answers of 2026-10-26 to
PLAN-0966: D1 "ja", D2 "okay"; recorded as a dated amendment in ADR-2304, which stays `proposed`);
stall, latency and rejection rate only report. For SkillAdapter skills the oscillation is counted
from the config file's `versions`; for `os.definition_of_done_verifier` (whose weight file has no
versions) from the tenant core chain's `skill_config_updated` records for that skill - more than 3
in one hour, anomaly `param_name: dod_weights`. The confidence regression does not apply to the DoD
skill: its production `skill_executed` records carry `output = {score, passed}`, never a confidence.

| Step | Where | Contract |
|---|---|---|
| Freeze | `LearningHost._check_anomalies` → `_freeze` → `skill_adapter.freeze_learning` → `SkillAdapter.freeze` | only AFTER the anomaly record committed; audit-first: `learning.frozen` (WARNING) on the tenant chain, then `OptimizerState.frozen/frozen_reason/frozen_since` persisted in `<tenant>/skills/<skill>_config.json` (`optimizer`). Not committed → nothing frozen, the anomaly entry has no `freeze` key and the next check retries. The new state is written to a temp file BEFORE the record and renamed after it: a temp-file failure records nothing; a rename failure after the record committed chains `learning.freeze_not_applied` (ERROR) and is retried. Done once per anomaly episode (`freeze: frozen` on the host's anomaly entry). Every retry counts `freeze_attempts` on the entry and the next `learning.frozen` carries `attempt` (so a retry never reads as a second, independent freeze). Only an id failing `valid_skill_id` - checked by the host itself - is `freeze: invalid_skill_id`, never retried; a config file that exists but does not parse (one malformed number is enough) is recorded once per episode as `learning.freeze_failed` (`cause: config_unreadable`) and retried until it parses |
| Refuse | `SkillAdapter._apply_config_delta_locked`, `SkillAdapter._run_optimizer_epoch_locked`, `WeightPersistence.save_weights`, `dod_verifier_dashboard` feedback route | `LearningFrozen` raised, no config/weight change, not even an epoch tick; one `learning.freeze_refused_write` (`target: skill_config` or `dod_weights`). The refusal holds even if its record does not commit. The DoD weights are frozen under `os.definition_of_done_verifier` (`weight_persistence.DOD_SKILL_ID`); an unreadable brake file - including a malformed number - reads as frozen (`brake_state_unreadable`). A file without an `optimizer` block reads as never frozen (legitimate pre-2026-09-06 files; the brake always writes the block); an `optimizer` that is not an object is unreadable. `WeightPersistence.save_weights(record=...)` runs brake check, the caller's `skill_config_updated` and the write under the DoD skill's config flock (`skill_adapter.brake_lock`, the lock a freeze takes), so a freeze cannot land between check and write. The DoD feedback route uses `WeightPersistence.update(compute)`, which also loads the stored weights and computes the optimizer step inside that flock: two concurrent ratings (two console processes) never step from the same old weights, and each chained `old_weights` is the state that was stored |
| Surface | `POST learning/feedback` → hypotheses `optimizer_reason: learning_frozen` (feedback still recorded); `POST /feedback/skill` → `config_applied: false` with the note `config not updated: LearningFrozen`; `POST /api/dod/feedback` → 409 `{error: learning_frozen}`, no `skill_config_updated`; 503 `lock_busy` when the brake lock stays held | |
| Lift | `POST /v1/console/learning/config/unfreeze?skill_id=` (`routes/learning.py`) | `require_csrf` (no CSRF → 403); tenant from the session only (a skill frozen in another tenant is 404 `not_frozen` here); audit-first `learning.unfrozen`, then state, then console `learning.config_unfreeze`. 503 `audit_not_committed` leaves it frozen. 503 `state_not_written` when the state could not be written (if `learning.unfrozen` had committed, `learning.unfreeze_not_applied` follows it; still frozen). 422 `skill_id_alias` (+ `canonical_skill_id`) for an id that names another skill's file (`os_delegation_router` for `os.delegation_router`): one canonical id per file, the stored id is never rewritten. 409 `brake_state_unreadable` for a file that does not parse. Never automatic, no time-out |

A freeze never triggers a rollback (`SkillAdapter.rollback` stays the separate manual action and
still works while frozen). There is **no switch** — no flag, no env variable, no setting. Fields of
the six events (`learning.frozen`, `learning.unfrozen`, `learning.freeze_refused_write`,
`learning.freeze_not_applied`, `learning.unfreeze_not_applied`, `learning.freeze_failed`):
`skill_id`, `reason` (a freezing anomaly name, `brake_state_unreadable` or `unknown` - a value read
from the file is normalised, never copied), `version_id` (`v<N>`, `none` or `unparsed`), `tenant_id`
(+ `target` on the refusal, `attempt` on frozen / freeze_not_applied / freeze_failed, `cause` on
freeze_failed), registered in `EVENT_SEVERITY` / `_EVENT_ALLOWLIST` and at runtime via
`skill_adapter.BRAKE_EVENTS`. Today a router freeze has no routing effect (the learned threshold is
read only in a cleared Phase 2, register L05); it bites on the DoD weights, which freeze
automatically on more than 3 stored weight changes in one hour.
There is no unfreeze button in the console yet; the host's anomaly entry (`GET
/v1/console/api/learning/daemon`) carries `freeze: frozen`.
Proof: `tests/learning/test_adr_2304_learning_brake_e2e.py` (seeded oscillation → one host tick →
one `learning.frozen` → refused writes through the console routes → unfreeze over HTTP; the DoD
weights frozen from four changes made through `POST /api/dod/feedback`) and
`tests/learning/test_adr_2304_brake_review_fixes_e2e.py` (concurrent freeze vs weight write,
compensated freeze/unfreeze, alias refusal, malformed-number retry, closed reason/version shapes).

## Data Hub learning loop (PLAN-0948, ADR-2255, 2026-10-12)

A second, separate loop - not the L5 router loop above. Its subject is a **Data Hub SKILL artifact**: created through
`POST /v1/console/datahub/create` it is registered in SkillForge as `assistant.datahub_<slug>` with
`meta.data_sources = ["datahub:<artifact_id>"]` (`datahub_unified/register.py`).

| Stage | Where | What |
|---|---|---|
| Produce | `skill_inject.collect_active_skills` / `grade_from_user_followup` | a content-free line in `<tenant>/learning/daemon/inbox-YYYYMMDD.jsonl` (`core/background/learning_inbox.py`; closed fields; producers never raise) |
| Host | `core/background/learning_host.py`, started by `corvin_gateway.app`, `corvin_console.app`, `corvin_console.standalone` | drains the inbox from a persisted cursor, checks watched sources by hash every 300 s, runs the regeneration batch, snapshots `state.json`; 2 s -> 30 s idle back-off; a failing tick is counted, not fatal |
| Learn | `learning_daemon.py` | per-source weights (unseen source = 0.5), feedback consensus, causal edges; consensus mean < 0 over >= 3 events, or a changed source, queues the skill; 24 h cooldown, a request during cooldown is deferred not dropped |
| Act | `datahub_unified/regenerate.py` | re-read the source (secret -> refused), regenerate via `UnifiedCreator`, `learning.regeneration` `applying` on the chain FIRST, `update_body(quota_exempt)` (grades + meta kept), `finished` |
| Tidy | `datahub_unified/prune.py`, run by the host once a day (`PRUNE_CHECK_S`) | the system removes Data Hub skills that are dead, duplicated or unused - by rule, audited, guarded (below) |
| See | `os.learning_daemon` (registry Skill, read-only), `GET /v1/console/api/learning/daemon` | persisted summary: weights, queue, regenerations, decisions, `prune` (last pass, total removed) |

**Self-clean (D6, 2026-10-26).** Data Hub skills piled up with nothing to take them away: a registered skill outlived its
artifact, and the engine-facing plugin slot (`corvin_operator/skill-forge/skills/dyn/assistant_datahub_<id>/`, gitignored) kept
mirrors of skills no registry held any more - 120 of them on the live install (107 from tests), read by the engine's skill loader
and the CEL skill stage but never injectable, gradable or regenerable. `LearningHost._prune_once` now runs `prune.run` once a day
(first tick after start, then every 24 h) and the system decides by rule, in this order:

| Reason | Holds when |
|---|---|
| `orphan_mirror` | a slot mirror that NO tenant registry holds (a mirror is per repo, so every tenant's registries are checked), older than 10 min. Also the legacy name-derived slugs (before DH-2/DH-10), only when the body carries the creator's template; their name is never recorded, only a hash |
| `artifact_gone` | a registered skill whose artifact is deleted or missing |
| `duplicate` | same data profile (`DataHubSkill.profile()` block; without one, same body); the one with earned grades, then the newest, stays |
| `unused` | older than 60 days, never offered to a turn, never rated, bootstrap grade only - and only once the daemon has watched for the whole 60 days (`prune.observing_since`, set at the first save) |

Guards: the intent is chained (`learning.datahub_pruned`, `stage: applying`) BEFORE anything is touched, a record that does not commit
leaves the item alone · a skill with an earned grade is never removed as duplicate or unused · a pass that would remove >= 20
registered skills AND more than half of them is refused whole (`learning.datahub_prune_refused`, `bulk_guard`; orphan mirrors are
exempt) · at most 500 removals per pass. Every removal is appended to `<tenant>/learning/datahub_prune/removed.jsonl` (time, id, kind,
reason, body hash prefix, kept skill - never a body, title or description); `prune.removed_list()` reads it, `prune.verify()` checks that
the names are gone from registry and slot and that no text file still mentions them. A pass that finds nothing writes nothing (an
idle host stays silent). Proof: `core/console/tests/test_datahub_prune_e2e.py` - created through the console route, acted on only by
`LearningHost.tick()`; mutation probes: no call in the tick -> 3 tests red, no slot isolation in `conftest.py` -> the isolation test red.

**Cause of the pile, closed.** Unset, `CORVIN_PLUGIN_SLOT_DIR` resolves to the checkout's REAL `skills/dyn`; every test that created a
skill left a mirror there. `test_admin_route._sandbox` already redirected it, but two Data Hub test files (`tests/skills/test_datahub_unified_complete.py`,
`tests/e2e/test_console_skills_wiring.py`) did not, and the root `conftest.py` did not isolate it. It now does for the whole pytest session
(`_ISOLATED_KEYS`, default `<session sandbox>/plugin_slot`; a value pointing into the checkout is re-armed). Which run left the 35 mirrors of
2026-10-24 was not established.

**Hardened 2026-10-12 (adversarial review, 44 findings across Epic I-11).** The skill id is
`assistant.datahub_<artifact_id>` - never derived from the artifact NAME (two similar names shared one
skill, and the name, possibly personal, became an id in the permanent chain); delete removes only the
skill whose `meta.artifact_id` matches. Regeneration re-checks the stored source path on every read (a
symlink or a path that no longer resolves to itself, or one inside another tenant's home, is refused)
and records EVERY terminal outcome (`completed`, `refused`, `failed`, `partial`, `no_artifact`); a
failed artifact write rolls the registry body back. The inbox keeps a read offset PER day file, so a
line finished in yesterday's file after the reader moved on is not lost. One host per tenant across
processes: an exclusive lease on `daemon/host.lock`; a second process stands by and takes over when
the holder exits. A failed or refused regeneration is retried (1 h / 24 h), not counted as done. A
state file that cannot be restored is recorded (`learning.state_refused`) and kept aside as
`state.json.refused-<ts>`. State of deleted artifacts is evicted (`learning.sources_evicted`) - but
only when the artifact listing could be READ (an unreadable listing evicts nothing). All three host
lifespans stop the host on shutdown.

**Single-tenant per process, by construction.** The core audit writer refuses a record for any tenant
other than the process tenant (ADR-0562), so a learning host can only audit - and therefore only run -
for `CORVIN_TENANT_ID`. Producers drop events for any other tenant instead of letting an unread inbox
grow. The same chokepoint makes the brake and every learned-config write of a foreign tenant fail
(both are audit-first), so a console process cannot write another tenant's SkillAdapter config at
all: one host per process tenant covers everything that process can learn. Review 2026-11-05
(LRN-3) asked for a host per tenant directory; it is not built for this reason, and
`tests/learning/test_review_20261105_lrn_hosts_e2e.py::test_lrn3_a_process_cannot_learn_for_a_foreign_tenant`
pins the premise. A second tenant learns in its own process (`CORVIN_TENANT_ID`).

**Limits, stated.** Operator *ratings* of these skills do not feed the daemon (the ADR-0534 gate admits a rating only for a subject
with a recorded execution; prompt skills record none) - the live feedback source is the operator's follow-up in the next turn.
The generator is the template `UnifiedCreator`: regenerating from the same data rebuilds the same text. Since 2026-10-24 a SKILL
body carries a **data profile** (`DataHubSkill.profile()`: rows, per-column fill, min/max/mean/median/sum, distinct counts) computed
from ALL ingested rows - before that the body held only the row count and the operator's description, so a worker given the skill
learned nothing about the data. Default is aggregates only; `include_values: true` on `POST /datahub/create` (kept in the artifact's
`source`, so regeneration honours it) adds the most frequent labels of low-cardinality columns, never when the ADR-0661 scan flagged
PII or injection text. The scan is narrow (IBAN, SSN, e-mail+phone pair): a clean scan is NOT proof of "no personal data", which is
why values are opt-in. ACS workers receive such a skill as text with `dynamic_skills.enabled` (delegation-routing.md section 10);
proof: `tests/e2e/test_datahub_skill_reaches_acs_worker.py` (live part opt-in via `CORVIN_LIVE_LLM=1`); decision: ADR-2279 D6. `os.data_hub` /
`os.skill_tool_creator` (the 12-phase generator) stay optional and out of scope.

**Must NOT do:** put user text, a path or a skill body into an inbox line or a `learning.regeneration` record · let a producer raise into a
turn · regenerate before the `applying` record committed · spend the operator's authoring quota on a daemon rewrite ·
start the host from a request path (it is started once per host process) · prune before the `learning.datahub_pruned` intent committed ·
remove a skill that carries an earned grade for being a duplicate or unused · write a legacy slug (it can be a person's name), a title or
a body into a prune record · judge a slot mirror against one tenant's registry alone · skip the bulk guard.
