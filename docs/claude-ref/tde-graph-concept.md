# TDE Graph — Current State and Parity Concept vs. ACS Workflow Graph

Status: investigation + concept only, no code changed. Written 2026-07-24 in response to a
question whether a dedicated "TDE Graph" section exists next to "ACS Workflow Graph" in the
Audit UI. Short answer: **it already exists and is fully wired**, but at a different structural
level than "ACS Workflow Graph" — see Gap Analysis below for the one piece that has no TDE
equivalent yet.

---

## 1. Current state (IST-Zustand)

### 1.1 Per-chat Audit panel (`chat.tsx`)

`core/console/corvin_console/web-next/src/pages/chat.tsx` renders an Audit panel per chat
session with a 3-way top-level tab bar (`auditTab` state, line ~806):

| Tab label     | Component               | Purpose                                              |
|---------------|--------------------------|-------------------------------------------------------|
| Single-Chain  | `WdatAuditPanel`         | classic single-hash-chain audit view, 5 sub-views     |
| Dual-Track    | `DualTrackAuditPanel`    | dual-track audit comparison                           |
| **TDE Graph** | `TdeAuditGraphPanel`     | **already a dedicated top-level tab, ADR-0214**       |

`TdeAuditGraphPanel` (`src/components/TdeAuditGraphPanel.tsx`):
- Resolves which TDE run to show from the **live `ChatMessage`** (`tdeRunId` / `tdeProgress`,
  stamped by the `engine` / `engine_progress` stream events in `chat-registry.ts`), scanning
  messages back-to-front for the latest TDE turn in this chat.
- On reload, the same data is **rehydrated from the backend-persisted `tde_progress`** field on
  each chat turn (`chat.tsx` lines ~1010–1029) — this is the "k=8 chain" from commit `9ba544c`
  that closes the persistence loop so the tab isn't empty after a page refresh.
- Renders a metrics card (steps, delegated/local count, L34 gate outcome, latency delta,
  token-savings — honestly rendered as "not measured" when `token_usage_instrumented` is false,
  per the 2026-07-24 honesty pass).
- Has a manual run-id override input (regex-validated `tde-<epoch>-<8 hex>`) as a fallback for
  older turns that predate the live-stamping plumbing.
- Delegates the actual graph rendering to `ComputeGraphView(mode="tde", runId, pollMs)`.

### 1.2 Shared graph component (`ComputeGraphView.tsx`)

One React-Flow component serves **three modes**: `"l25"`, `"acs"`, `"tde"`. Each mode fetches a
different backend payload shape but shares layout/rendering machinery
(`buildReactFlowGraph` for l25/acs, a TDE-specific `buildTdeReactFlowGraph` for tde), the node
tooltip panel, and the legend — all already mode-aware (`NodeTooltipPanel`, `GraphLegend`).

### 1.3 Backend: TDE audit-graph endpoint

`GET /compute/tde/{run_id}/graph` (`routes/compute.py:4047`, `_build_tde_audit_graph` at
`:3788`):
- Reads the **hash-chained `tde.*` audit trail** (not a manifest/artifact file on disk, unlike
  L25/ACS), filters by `details.tde_run_id == run_id`.
- **Tenant-scoped fail-closed** (ADR-0007): only serves records stamped with the requesting
  session's `tenant_id`; unmatched records 404 indistinguishably from "run doesn't exist" —
  no cross-tenant existence leak.
- **Hash-chain verification is first-class in the payload**: `meta.chain_verified` (segment
  scoped to this run's own event-line range) *and* `meta.chain_verified_global` /
  `chain_problems_total` (whole-chain verdict, since a break before the segment un-anchors it
  transitively — this distinction was added in the 2026-07-24 adversarial review).
- Emits an audit event of its own (`tde.audit_graph_viewed`) for every successful fetch.
- Node/edge structure mirrors the ACS graph builder's shape (task root → manager decision
  (`engine_selected`) → per-step decision nodes (chained like ACS iterations) → per-step
  worker nodes (`step_delegated` / `step_executed_local`, `loss_recorded` merged in) →
  completion (`plan_executed`)), plus a TDE-only `l34_prescan_block` node type when the L34
  gate blocks a turn before any step runs.

### 1.4 Data model — nodes / edges / meta actually rendered

| Node group      | Source event(s)                                   | Notes                                   |
|------------------|----------------------------------------------------|------------------------------------------|
| `task_root`      | run envelope                                        | diamond, level 0, `n_events`, `wall_time_s` |
| `l34_block`       | `tde.l34_blocked` (scope=prescan)                  | red triangle, only if the gate fired pre-scan |
| `manager`        | `tde.engine_selected`                              | star, confidence-colored, engine/task_type/complexity |
| `decision`       | `tde.delegation_decision` (per step)               | chained sequentially                     |
| `worker`         | `tde.step_delegated` / `tde.step_executed_local`   | merges `tde.loss_recorded` when present  |
| `completion`     | `tde.plan_executed`                                | terminal node                             |

`meta`: `run_id`, `n_events`, `n_steps`, `n_delegated`, `n_local`, `wall_time_s`, `engine`,
`confidence`, `loss_min`/`loss_max`/`loss_curve`, `chain_verified`, `chain_problems`,
`chain_verified_global`, `chain_problems_total`.

---

## 2. Comparison with "ACS Workflow Graph"

The label "ACS Workflow Graph" itself lives in a different place than one might assume from the
question — it is **not** a sibling top-level tab in `chat.tsx`. It exists at **two separate
structural levels**, and TDE currently matches only one of them:

| Level | ACS | TDE | Match? |
|---|---|---|---|
| **A. Per-chat audit sub-view** — one of 5 view-toggle buttons (`acs / os / spans / log / debug`) inside `WdatAuditPanel`, itself the "Single-Chain" tab of the chat Audit panel. Label: `"ACS Workflow Graph"` (`WdatAuditPanel.tsx:1501`). Backend: `GET /compute/acs/{run_id}/graph`. | N/A — TDE has no sub-view inside Single-Chain | **TDE instead got its OWN top-level tab** ("TDE Graph", sibling to "Single-Chain"/"Dual-Track") rather than a sub-view nested inside Single-Chain. | Structurally different, but arguably a *stronger* placement — TDE is one click away instead of two, and doesn't compete for space with OS-turn/spans/log/debug views. |
| **B. Dedicated cross-session page** — `/app/compute` (`pages/compute.tsx`) has a top-level tab `"Agent Shell"` (`key: "acs"`) that lists **all** ACS runs across all chats/sessions (`GET /compute/acs` list endpoint) and renders `AcsTab` → `ComputeGraphView(mode="acs")` per selected run. | **Does not exist.** No `"tde"` entry in `compute.tsx`'s `TABS` array, and **no backend list endpoint** (`GET /compute/acs` has no TDE counterpart — only the single-run `GET /compute/tde/{run_id}/graph` exists). | **Gap.** | This is the one real, actionable gap. |

So: the per-chat "is there a TDE Graph tab" question is already answered **yes**, and it is
already wired to the real hash-chained audit trail with tenant isolation and chain-integrity
surfacing — arguably more rigorously than the ACS sub-view, which reads from
manifest/artifact files rather than a hash-chained log.

The gap is that **TDE runs are only discoverable from inside the chat that produced them** —
there is no equivalent of the compute page's "Agent Shell" tab where an operator can browse
*all* TDE delegation runs ever made, independent of which chat triggered them.

---

## 3. Concept: closing the "Agent Shell" gap

### 3.1 Backend — new list endpoint

Add `GET /compute/tde` (mirrors `GET /compute/acs` at `routes/compute.py:2794`):
- Scans the hash-chained audit log for `tde.engine_selected` / `tde.plan_executed` /
  `tde.l34_blocked` events, groups by `details.tde_run_id`, tenant-scoped exactly like the
  existing per-run endpoint (fail-closed on `tenant_id` mismatch).
- Per-run summary row: `run_id`, `chat_key`/`sid` (to let the operator jump back to the
  originating chat), `started_at`, `n_steps`, `delegated_count`, `local_count`, `engine`,
  `l34_blocked: bool`, `chain_verified` (segment).
- Needs an efficient scan strategy — the existing per-run endpoint does a full linear scan of
  the audit file filtered by `tde_run_id`; a list endpoint scanning for *all* run ids needs to
  either (a) do one linear pass building a `run_id -> summary` dict, which is still O(n) in
  audit-log size but only once, or (b) maintain a small on-disk index
  (`tde_runs_index.jsonl`, append-only, written alongside each `tde.plan_executed` event) if
  the audit log grows large enough that a full scan per page-load becomes a problem. Start with
  (a); only build (b) if profiling shows it's needed — no premature index.

### 3.2 Frontend — new tab in `compute.tsx`

Add a `"tde"` entry to the `TABS` array (`pages/compute.tsx:3379`), alongside `"acs"`
("Agent Shell"). Suggested label: **"TDE Delegation"** (matches the existing
`AGENTIC_ENGINE_LABELS.tiered_delegation = "TDE (Tiered Delegation Engine)"` naming in
`chat.tsx:491`) or simply **"TDE"** for tab-bar brevity, consistent with the terse
single-word style of the other tab labels (`Runs`, `Pipelines`, `HAC`).

Structure mirrors `AcsTab.tsx`: a run-list view (`activeTab === "runs"` equivalent) plus a
`activeTab === "graph" && <ComputeGraphView mode="tde" runId={runId} />` pane when a run row is
selected — reusing the exact same `ComputeGraphView` component TDE already uses inside the chat
panel, so no new graph-rendering code is needed, only the list/selection chrome.

A "jump to originating chat" affordance (using the `chat_key`/`sid` the list endpoint returns)
would be new relative to the ACS tab, since TDE runs — unlike ACS/L25 batch runs, which can be
started standalone via `POST /compute/runs` — are always attached to a chat turn. This is worth
keeping as a TDE-specific addition rather than copying the ACS tab UI verbatim.

### 3.3 What NOT to change

- Do not touch the existing per-chat "TDE Graph" tab in `chat.tsx` — it is complete, tested
  (adversarial review 2026-07-24), and serves a different use case (operator is already looking
  at *this* chat and wants *this* turn's graph without navigating away).
- Do not duplicate the hash-chain verification logic — the new list endpoint should reuse
  `_forge_security_events.verify_chain` and the same tenant-scoping pattern as
  `compute_tde_audit_graph`, not reimplement it.
- Do not merge TDE into the existing `"acs"` tab — TDE and ACS are different delegation engines
  (ADR-0214 vs. the ACS manager/worker fan-out) with different node/edge semantics; a combined
  list would need a mode filter anyway, so a separate tab is simpler and matches the existing
  per-chat separation (TDE Graph is already its own tab there, not folded into Single-Chain).

---

## 4. Open questions for the maintainer

1. **Is the cross-session "Agent Shell" gap actually worth closing?** TDE runs are currently
   fully inspectable from the chat that produced them (which is arguably the natural place to
   look, since ADR-0214 TDE is turn-scoped, not a standalone batch job like L25/ACS runs
   triggered via `POST /compute/runs`). Building a cross-session list view is real work
   (new endpoint + list UI) for a use case that may not come up often — worth confirming
   there's an actual operator need (e.g. "audit all TDE delegations this week across all
   chats") before building it.
2. **List-endpoint cost model** — if it's built, should it do a full audit-log linear scan per
   request (simple, correct, but O(log size) per page load) or maintain a lightweight
   `tde_runs_index.jsonl` written alongside `tde.plan_executed`? Depends on expected audit-log
   volume in production, which this investigation did not measure.
3. **Tab label** — "TDE", "TDE Delegation", or reuse the existing `AGENTIC_ENGINE_LABELS`
   string "TDE (Tiered Delegation Engine)"? Cosmetic, but should match `chat.tsx`'s existing
   naming so the same run doesn't get two different display names across the two places it
   appears.
4. **Is "TDE Graph" the right permanent name for the per-chat tab**, given it already exists
   and works? No change proposed here — flagging only because if a second, cross-session
   surface is added under a different name ("TDE Delegation" / "Agent Shell → TDE"), the two
   labels should stay visibly connected so operators don't read them as two unrelated features.

---

## 5. Inline chat-bubble badge — final concept

Sections 1–4 above answered a *different* question ("does a dedicated TDE Graph tab exist
next to ACS Workflow Graph in the Audit panel?" — yes, Section 1.1). This section answers the
question that actually motivated this second investigation: should the **inline per-turn badge
inside the chat bubble itself** (not the Audit panel) show TDE-specific detail instead of the
generic one-liner every engine gets today?

### 5.1 IST-Zustand — the existing generic badge

`chat.tsx:2251-2259` already renders, for **every** engine (not just TDE), a single line under
the assistant bubble whenever `!isUser && m.engine`:

```
⚙ Engine: {AGENTIC_ENGINE_LABELS[m.engine] ?? m.engineLabel ?? m.engine}
```

`m.engine` is stamped live from the `engine` stream event (`chat-registry.ts`); for TDE it
reads `"tiered_delegation"` → label `"TDE (Tiered Delegation Engine)"` (`AGENTIC_ENGINE_LABELS`,
`chat.tsx:487-492`). This line carries **zero** TDE-specific detail — no step count, no
delegated/local split, no token/latency numbers — even though `m.tdeProgress` (the richer
object from the k=8 chain, commit `9ba544c`) is sitting right there on the same `ChatMessage`
and today is used *only* inside the Audit panel's TDE Graph tab (`TdeAuditGraphPanel.tsx`), not
in the main chat timeline.

### 5.2 WANN — trigger condition

Branch on `!isUser && m.engine === "tiered_delegation" && m.tdeProgress` for the richer render;
fall through to the existing generic one-liner for every other engine, **and** for TDE turns
where `m.tdeProgress` is absent. No new gating logic is needed beyond what's already there —
this is a rendering branch inside the existing `!isUser && m.engine` block, not a new condition.

Two things this trigger must respect, both discovered in this investigation:

- **TDE only ever runs as an explicit opt-in today.** `chat_runtime.py:4114-4115` fires the TDE
  path *only* behind the `/use-engine tiered_delegation` slash command
  (`_tde_force`); ADR-0214's own "Status Transitions" section
  (`Corvin-ADR/decisions/0214-tiered-delegation-engine-with-loss-awareness.md:1218,1288-1289`)
  states auto-routing stays on ADR-0114 and TDE auto-routing requires passing a canary gate
  first — **not live**, matches memory. So today this badge only ever appears on a turn the
  user *asked for* by name; it is not yet the mechanism by which a user discovers "this turn was
  silently routed to TDE for me." That changes the day auto-routing goes live — worth a
  follow-up note then, not a blocker now.
- **`tdeProgress` can legitimately be absent even when `m.engine === "tiered_delegation"`.**
  `chat_runtime.py:3491` guards the `engine_progress` yield with `if step_count > 0:` — a TDE
  turn whose plan resolved to zero steps never emits the event, so `m.tdeProgress` stays
  `undefined` while `m.engine` is still `"tiered_delegation"`. The fallback to the generic
  one-liner handles this for free; no special-casing needed, just don't assume `tdeProgress`
  is always present alongside `engine === "tiered_delegation"`.

### 5.3 WELCHE Infos — fields, honestly scoped to what exists today

| Field | Source | Available now? | Notes |
|---|---|---|---|
| Steps completed/total | `tdeProgress.completed_steps` / `.total_steps` | **Yes** (live + persisted) | |
| Delegated vs. local count | `tdeProgress.delegated_count` / `.local_count` | **Yes** | |
| Latency delta vs. local | `tdeProgress.latency_delta_pct` | **Yes**, real measurement | `tde_engine.py::_summarize` computes actual wall-clock delegated-vs-local averages — safe to render as-is |
| Token savings | `tdeProgress.token_savings_pct` + `.token_usage_instrumented` | Field exists, **always `null`/not-instrumented today** | ADR-0215 honesty contract (`_summarize`, `tde_engine.py:174-211`): no per-call token instrumentation exists (`worker_ipc.run_one_shot` uses `--output-format text`, not `json`). Must render **"not measured"**, never a fabricated percentage — this is the exact bug the 2026-07-24 honesty pass already fixed once for the Audit-panel card; do not reintroduce it in the inline badge |
| L34 gate outcome | `tdeProgress.l34_forced` | **Yes** | |
| Task type / complexity | *not yet on `tdeProgress`* — sits in `analysis.classification.{task_type,complexity}`, already in scope in `chat_runtime.py` at the point `tde_progress_dict` is built (line ~3424, currently only used for a delta-text line, not the structured dict) | **No — needs one new field addition, data already exists in-process** | Cheap add: no new computation, just thread an existing local variable into the dict |
| Chosen engine / detector confidence | `selection.get("engine")` / `.get("confidence")` (`send_integration.py:199-205`) | Exists but **not informative in this path** | Under the explicit `/use-engine tiered_delegation` opt-in, `engine_override` forces the choice (`send_integration.py:140-142`) and confidence stays the hardcoded default `1.0` — it never reflects real detector confidence. **Recommendation: omit from the badge** (would look like a real number but is a constant); becomes meaningful only once ADR-0214 auto-routing (undetected-engine path) is live |
| Branches explored / chosen-branch rationale | Per-step `reason_code` / `step_action` / `delegate` — **only** in the full graph payload (`GET /compute/tde/{run_id}/graph`, `compute.py:3917-3950`), never summarized onto `ChatMessage` | **No — and should stay that way** | Rendering every step's rationale inline would duplicate the graph view and bloat the chat timeline. Recommendation below (5.4) is a "View graph →" link into the already-complete TDE Graph tab (Section 1) instead of re-plumbing a summarized rationale field |
| ADR-0216 quota consumption ("N/10 today") | **Nowhere.** `_enforce_tde_compute_quota` (`tde_engine.py:127-161`) only returns allow/deny — `increment_and_check` raises or returns `None`, it never surfaces the running count. `license.compute_quota.get_today_count()` exists and is the right primitive, but nothing in the TDE path calls it | **No — real gap, needs new plumbing (5.4)** | This was the field the maintainer explicitly asked about as an example; it does **not** exist today anywhere in the pipeline and must not be presented as if it does until wired |

### 5.4 WO GENAU — files to change (implementation-ready)

1. **`operator/orchestration/tde/tde_engine.py`**
   - `_enforce_tde_compute_quota()` (line 127): after the successful `_cq_inc(...)` call
     (line 155), call `license.compute_quota.get_today_count(_license_corvin_home())` and
     `license.validator.get_limit("compute_units_per_day")`, and thread both through to the
     caller (currently the function returns only `None` on success — needs to return the
     used/limit pair alongside the "proceed" signal, e.g. as an out-parameter or by returning
     `(None, quota_info)`).
   - `execute()` (line 232-326): stash `quota_used_today` / `quota_limit` (limit `None` =
     unlimited/member tier) into the `summary` dict returned at line 321-326; also pass through
     `task_type` / `complexity` (already available via `analysis.classification` at line 289 —
     currently only used to build `complexity=` for the executor, not returned to the caller).

2. **`core/console/corvin_console/chat_runtime.py`**
   - `tde_progress_dict` construction (lines 3459-3469): add `task_type`, `complexity`,
     `quota_used_today`, `quota_limit` keys, sourced from the extended `summary` dict (above)
     and from the already-in-scope `analysis.classification` (line 3424).
   - `engine_progress` yield (lines 3503-3513): mirror the same new keys into the live stream
     event so the badge updates without a reload.

3. **`core/console/corvin_console/web-next/src/lib/chat-registry.ts`**
   - `TdeProgress` interface (lines 31-41): add `task_type?: string`, `complexity?: string`,
     `quota_used_today?: number | null`, `quota_limit?: number | null`.
   - `engine_progress` reducer case (lines ~363-393, added by commit `9ba544c`): map the new
     event fields onto the `TdeProgress` object being constructed there.

4. **`core/console/corvin_console/web-next/src/lib/api.ts`**
   - `ChatTurn.tde_progress` (line ~1397, added by commit `9ba544c`): no type change needed —
     it's already `Record<string, unknown>` with a comment stating field names match
     `TdeProgress` 1:1 — but update that comment to enumerate the new fields once implemented
     (docs-as-definition-of-done applies to comments that document a contract, not just
     prose docs).

5. **`core/console/corvin_console/web-next/src/pages/chat.tsx`**
   - Badge block (lines 2251-2259): branch `m.engine === "tiered_delegation" && m.tdeProgress`
     to a richer render — steps X/Y, delegated/local split, an L34-gate colored indicator,
     latency delta, token savings (rendered honestly per 5.3), and quota `"N/M today"` (omit
     the line entirely when `quota_limit` is `null`/unlimited — never show "N/null"). Add a
     "View graph →" affordance that sets the existing `auditTab` state (Section 1.1, line ~806)
     to the TDE tab and opens the Audit panel if collapsed, so the detailed per-step rationale
     (5.3's "branches explored" row) is one click away instead of duplicated inline.
   - Keep the current one-liner as the fallback branch (every other engine, and TDE turns
     without `tdeProgress` per 5.2's zero-step edge case).
   - `AGENTIC_ENGINE_LABELS` (lines 487-492) stays as-is — still backs the generic badge and
     can double as the richer badge's heading.
   - Consider extracting the richer render into its own `TdeInlineBadge` component (sibling to
     `TdeAuditGraphPanel.tsx`) rather than growing the already-large message-render block
     in-place — not load-bearing, but keeps `chat.tsx` from growing further (it is already one
     of the largest files in the frontend).

6. **Docs (CLAUDE.md "Testing + Docs Sync" gate — same commit, no exceptions):**
   `docs/claude-ref/layer-engines.md` and `docs/claude-ref/delegation-routing.md` (both already
   listed as the doc targets for the ADR-0216 metering-map change) need the new badge behavior
   and the quota fields documented; ADR-0216 itself
   (`Corvin-ADR/decisions/0216-tde-shared-agentic-compute-pool-and-tenant-scoped-audit.md`)
   does not need amending — the badge is a consumer of the existing pool, not a change to the
   pool's semantics, so this is docs-only, not an ADR-worthy decision (adr-gate: skip reason —
   config/UI surface for an already-decided mechanism, no new structural choice).

7. **Tests.** `tests/test_tde_engine_summarize_honesty.py` already pins the "no fabricated
   token number" contract for `_summarize()` — extend it for the new `quota_used_today` /
   `task_type` / `complexity` fields. **Gap found in this investigation:** despite
   `core/console/corvin_console/web-next/tests/unit/lib/chat-registry.test.ts` existing as the
   test file for that module, it has **no test coverage at all** for the `engine_progress`
   reducer case added in commit `9ba544c` (`grep` for `engine_progress`/`tdeProgress` in that
   file returns nothing) — the k=8 fix that made `tdeProgress` reach `ChatMessage` shipped
   without a regression test. Any implementation of this badge should add that missing reducer
   test *and* a render test for the new richer badge, not just the latter.

### 5.5 What NOT to do

- Do not add a `token_savings_pct` number anywhere in the inline badge until real per-call
  token instrumentation exists (5.3) — this is the same fabrication class the 2026-07-24
  honesty pass already removed once from the Audit-panel card (commit `2daf7ad`).
- Do not surface detector `confidence` on the badge while TDE is opt-in-only (5.3) — a
  constant `1.0` dressed up as a confidence score is misleading.
- Do not inline per-step branch rationale in the chat bubble (5.3/5.4) — link to the existing,
  complete TDE Graph tab instead of duplicating `compute.py`'s graph-building logic in a second
  summarized form.

### 5.6 Open questions

1. Should `quota_used_today` be computed via an *extra* `get_today_count()` read right after
   the charge (cheap, but a second file read/lock cycle per TDE turn), or should
   `increment_and_check` itself be changed to return the post-increment count so the caller
   gets it for free? The latter is a wider-blast-radius change (shared by ACS/compute callers
   too) — recommend the cheap extra read scoped to TDE only, not touching the shared function's
   signature.
2. Does showing "N/10 today" on every TDE turn create pressure to check quota state
   *before* invoking `/use-engine tiered_delegation` (i.e., should there also be a pre-flight
   quota display, not just a post-hoc one)? Out of scope for this badge, but worth flagging —
   the ACS fallback path already has a user-facing "quota exhausted" notice
   (`chat_runtime.py:4297-4306`); TDE's quota-exhausted path currently only returns a
   `{"reason": "quota_exhausted"}` result dict with no equivalent friendly notice text.
3. Member/unlimited tier: confirmed `quota_limit: None` means unlimited (ADR-0216 "Member tier
   stays unlimited"); the badge must treat `None` as "omit the quota line," not as "0" or
   "unlimited" literal text requiring a special-cased string — straightforward, flagging only
   so the implementer doesn't skip the `None` check.
