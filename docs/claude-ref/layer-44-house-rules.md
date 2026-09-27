# Layer 44 — Acceptable-Use / House-Rules Guard (ADR-0143)

**Status:** ACTIVE (M2) — wired into every OS-turn spawn (ClaudeCode path +
`_run_pre_dispatch_gates` for all non-CC engines), registered as a mandatory
Tier-3 capability, fail-closed, with the Haiku Tier-1 adjudicator live. The
remaining hardening (LIP pin of `house_rules.py` + `house_rules.yaml`)
is a release-time step owned by Corvin Labs; until then integrity is anchored
by the committed `EXPECTED_POLICY_SHA256` constant.

## What it does

Enforces the operator's *acceptable-use* policy — what **purposes** CorvinOS may be
used for. Orthogonal to L34 (data) and L35 (network). Shipped baseline forbids:

| Rule id | Action | Maps to |
|---|---|---|
| `no-military` | `deny` | EU AI Act Art. 5 (operator-stricter) |
| `no-offensive-cyber` | `escalate` | EU AI Act Art. 5 (operator-stricter) |
| `no-disinformation` | `deny` | EU AI Act Art. 5 + Art. 50 |

## Mechanism vs content (core design)

- **Mechanism** = `corvin_operator/bridges/shared/house_rules.py` — core, fail-closed,
  audit-first, **not disableable** (no env flag, no off-switch).
- **Content** = `corvin_operator/policy/house_rules.yaml` — committed, operator-edited in git.
  This is the **single source of truth**; you change the rules here.

## Repo linkage + integrity

The gate verifies `house_rules.yaml`'s sha256 against the committed
`EXPECTED_POLICY_SHA256` anchor in `house_rules.py`. A mismatch (local tamper to
weaken the rules) → fail-closed deny. The anchor file `house_rules.py` is itself
L10-path-gate-protected (runtime writes blocked) and a mandatory Tier-3
capability. After editing the policy:

```bash
sha256sum corvin_operator/policy/house_rules.yaml   # paste digest into EXPECTED_POLICY_SHA256
```

A CI test (`test_policy_anchor_matches_repo_file`) fails the build if the anchor
drifts from the shipped file.

**Release-time hardening (Corvin Labs):** add `house_rules.py` and
`house_rules.yaml` to `layer_integrity.MANDATORY_LAYER_FILES` and re-sign
`corvin_operator/security/layer-manifest.json` (`python corvin_operator/security/sign_layer_manifest.py`).
This adds the LIP integrity pin on top of the committed-anchor check.

## Wiring (M2)

- `adapter._check_house_rules_or_fail()` runs in the ClaudeCode OS-turn path
  (after the Tier-3 capability gate) and in `_run_pre_dispatch_gates()` (after
  L35) — every engine, every spawn.
- **Owner-console web-chat (second enforcement surface):**
  `core/console/corvin_console/chat_runtime.py::_check_house_rules_or_fail()`
  mirrors the adapter's gate one-for-one and runs in `stream_turn()` BEFORE
  either OS-turn spawn path — the direct `claude -p` subprocess AND the ACS
  delegation fan-out (`ACSRuntime`). It gates the substantive task text
  (prompt with any `/delegate` prefix stripped). Same fail-closed semantics
  (import/policy/gate error ⇒ refuse), same audit-first ordering (the
  `house_rules.*` event lands on the *per-tenant* L16 chain
  `<tenant_home>/global/forge/audit.jsonl` inside `gate.classify()` before the
  refusal is yielded), same metadata-only floor, and the same two-way escalate
  wording (neutral try-again for `classifier_error`/`clear_low_confidence`,
  operator-approval for a genuine borderline/violation). A blocked turn reuses
  the existing engine-unavailable bookkeeping (`os_turn.started` +
  `task.failed` + `web.turn.completed(rc=1)`). The console-chat
  `_check_capabilities_or_fail()` also runs the ADR-0141 Tier-3 presence assert
  on both paths (the ACS path already runs L34/L35 via `spawn_gates`, but did
  not assert capability presence). Before this fix the console web-chat ran an
  authenticated LLM spawn with NO L44 gate on either path — a structural
  fail-open of the acceptable-use control (round-3 review, EU AI Act Art. 5).
  Tests: `core/console/tests/test_chat_house_rules_gate.py`.
- **A2A worker spawn (third enforcement surface):**
  `corvin_operator/bridges/shared/a2a_worker.py::spawn_a2a_worker()` delegates to
  `spawn_gates.check_l44(...)` at step **1c.5** — after the L34 (1b) and L35 (1c)
  gates and BEFORE the compute-quota increment (1d) and any scratch-workspace
  creation, so a denied acceptable-use request consumes neither the tenant's
  compute quota nor FS resources. It classifies the SANITIZED inbound instruction
  (`clean`, the exact text the worker will execute — the same input L34
  classifies), with `channel="a2a"`, `chat_key=origin_id`,
  `engine_id="claude_code"`, and `corvin_home=_CORVIN_HOME_SNAPSHOT_A2A`
  (the import-time snapshot, matching the compute-quota gate). On a non-None
  return (deny / escalate / fail-closed error) the worker is NOT spawned and a
  `WorkerResult(status="rejected", error=<refusal>)` is returned — the receiver
  maps that to its normal A2A rejection path, preserving the audit-first A2A
  invariant. STRUCTURAL fail-closed: if even importing `check_l44` fails
  (`spawn_gates` absent) the spawn is rejected rather than proceeding — an
  acceptable-use guarantee may never evaporate into fail-open. Before this fix
  a signed remote origin's instruction reached the worker engine with NO L44
  gate (round-N review finding #5, EU AI Act Art. 5; CLAUDE.md L38 forbids
  bypassing L10/L34/L35 — the same now holds for L44). Tests:
  `corvin_operator/license/tests/test_a2a_license_fixes.py` (the `_allow_l44()` helper
  neutralises the gate so the compute-quota/env-clearing assertions stay
  isolated).
- `adapter._house_rules_adjudicator()` is the Tier-1 Haiku call
  (`claude -p --max-turns 1 --tools ""`, helper-model site `house_rules_adjudicator`,
  20 s timeout). It runs on EVERY non-empty task (NOT only on a Tier-0 hit — see
  Classification) and classifies the whole task against the full ruleset. On
  timeout/parse-failure it raises → the gate degrades to the Tier-0 floor (below).
  It runs only under the `cloud_only` order; a `floor_only` tenant never spawns it
  (see "Classifier order" below).
  - **Backend-unavailable → degrade to the Tier-0 floor (2026-07-11, load-bearing):**
    when the semantic classifier callable RAISES because *no* backend can run at all
    (cloud Haiku unavailable — the dominant case is a **fresh install** in the
    seconds/minutes before `claude` is logged in, or a transient outage), `classify()` no longer escalates EVERY task. It **degrades to
    the always-available deterministic Tier-0 floor**: the prohibited-class patterns
    (`no-military` / `no-offensive-cyber` / `no-disinformation`) still MATCH and BLOCK,
    but a task matching NO rule passes. This is **fail-TO-FLOOR, NOT fail-open** — the
    policy `default_action` is reached only for content the deterministic floor cleared,
    so the acceptable-use guarantee (prohibited classes never pass) is preserved while a
    benign first message (`"hallo"`) is no longer blocked out of the box. The audit
    chain records the distinct reason `classifier_error_tier0_degraded` (the semantic
    check did not run), and the degradation still feeds the ADR-0157 M4 health window
    (heal trigger). Genuine classifier UNCERTAINTY (a backend RAN but returned low
    confidence / an anomalous rule id) still ESCALATES — only total UNAVAILABILITY
    degrades. Maintainer-approved. Tests: `test_house_rules.py::
    test_classifier_backend_unreachable_degrades_to_tier0_floor` +
    `test_console_spawn_gates.py::test_gate_exception_degrades_to_floor`.
  - **Caller-timeout → same floor via `check_l44_floor` (2026-07-18):** the
    backend-unavailable degradation above only fires when the classifier callable
    itself RAISES. A caller that wraps `check_l44` in its OWN wall-clock bound — the
    imagegen MCP server bounds it at `_L44_TIMEOUT_S = 100s` — hits its timeout FIRST
    when the cloud classifier is merely slow (FREE tier / `claude` not logged in),
    so `classify()` never
    reaches its own degradation and the caller used to hard-refuse EVERY image, even a
    benign "queen bee". `spawn_gates.check_l44_floor()` exposes the identical Tier-0
    floor as a standalone, classifier-free, never-hangs call (`HouseRulesGate` built
    with `classifier=None`); the imagegen server calls it on timeout and honours its
    verdict — benign proceeds, a prohibited-pattern prompt is still BLOCKED. Same
    fail-TO-FLOOR contract, applied at the timeout boundary. Tests:
    `test_spawn_gates.py::test_floor_*` + `test_imagegen_zero_config.py::
    test_l44_hang_is_bounded_fast_and_degrades_to_the_floor` /
    `test_l44_timeout_still_blocks_a_prohibited_prompt_via_the_floor`.
  - **Binary resolution (load-bearing):** the classifier subprocess resolves the
    claude CLI via `adapter._resolve_helper_claude_bin()`
    (`CORVIN_CLAUDE_BIN` → PATH → engine known-location fallbacks), NOT the bare
    name `"claude"`. Under systemd the adapter runs with a stripped PATH that
    lacks `~/.local/bin`; a bare-name spawn raised `FileNotFoundError` →
    `classifier_error` → because the gate is fail-CLOSED this escalated EVERY
    request ("operator approval required (rule 'acceptable-use')"). The
    WorkerEngine path already resolves through
    `agents.claude_code._resolve_claude_bin`; the helper spawn now shares it.
  - **Transient retry (false-positive damping):** a single Haiku spawn can blip
    transiently (CLI timeout, a 429 rate-limit, an empty/garbled reply). Because the
    gate is fail-closed, every blip used to escalate a benign request (live: 6 of 9
    Discord escalations were transient `classifier_error`, not real content).
    `_house_rules_classify_chunk()` now retries the spawn up to **twice**
    (`_HOUSE_RULES_RETRIES = 2`, exponential backoff from
    `_HOUSE_RULES_RETRY_BACKOFF_S = 1.0 s`, capped at 4 s) before giving up; if it
    still fails, the error propagates and the gate degrades to the Tier-0 floor
    (above). A `spawn_missing` (CLI absent) or `auth_missing` cause is NOT retried
    (pointless). The internal
    `_HouseRulesClassifierError(cause=…)` tags the failure
    (`timeout`/`empty_output`/`no_json`/`bad_json`/`spawn_error`/`spawn_missing`)
    and `_house_rules_classify_chunk()` logs the precise cause to the **adapter log**
    for observability — the audit chain reason stays the coarse `classifier_error`
    (no PII, no detail persisted).
  - **Confidence semantics (anchored + raised thresholds):** the classifier prompt defines `confidence`
    as "how sure you are the reported `violated_rule_id` is correct" — a task the
    model is sure is clean takes an empty id with **HIGH** confidence. This removes
    the inversion where a clean task reported a tiny "violation probability" (e.g.
    `0.01`) that the fail-closed gate then escalated as if it were uncertain
    (`clear_low_confidence`). Operator decision 2026-06-25 EXTENSION (commit 4720933):
    to block only **clearly** classified tasks and reduce false-positive escalations
    on legitimate work (CSV analysis, debugging, security engineering), confidence
    thresholds are raised and made rule-specific:
      * **Global violation floor:** 0.85+ (was 0.8) — hard action only with 85%+ confidence
      * **Global clear floor:** 0.75+ (was 0.7) — escalate on keyword hit if < 75% sure
      * **Per-rule floor (dual-use):** `no-offensive-cyber` requires 0.90+. This preserves
        fail-closed escalation while avoiding false blocks on legitimate security work
      * **No-keyword clear:** low-confidence clear with NO Tier-0 keyword hit → ALLOW
        (benign case). The dominant source of false-positives (0.4 confidence "analyse logs.csv")
        is now allowed instead of escalated.
  - **Audit truth for the low-confidence allow (2026-09-07, F-B4):** the gate
    itself writes `house_rules.escalated` for a `clear_low_confidence` verdict,
    and the bridge adapter then lets the turn run. The adapter now records that
    override as `house_rules.allowed_after_lowconf` (WARNING; `rule_id`,
    `reason_code`, `confidence`, `channel`, fingerprinted `chat_key`,
    `overrides: house_rules.escalated`) so the chain never says "blocked" for a
    request that executed. Regression:
    `test_adapter_house_rules_binary.py::test_lowconf_allow_is_recorded_in_the_audit_chain`.
  - **User-facing message split:** an `escalate` caused by a non-finding —
    `classifier_error` (transient) or `clear_low_confidence` (classifier judged the
    task clean but unsure) — returns a neutral "couldn't be safety-checked just now,
    please resend" message instead of the alarming "touches a restricted or uncertain
    area / needs operator approval" wording, which is reserved for a genuine
    borderline/violation verdict. Both still **block** the spawn (fail-closed
    preserved) — only the wording differs.
- Operator CLI: `python -m house_rules show|status` (read-only; no disable command).
- `escalate` currently blocks with an "operator approval required" message; the
  approval routing through the L21 proposal channel is ADR-0143 M3.

### Classifier order — `cloud_only` / `floor_only` (ADR-0161, ADR-2087)

The Tier-1 classifier used to be a two-provider chain (local Hermes/Ollama +
cloud Haiku, orders `cloud_first` / `local_first` / `local_only`). ADR-2087
removed all local Ollama inference, so exactly **two orders** remain, resolved
once per task by `_house_rules_resolve_order(tenant_id)`:

| order | what runs | when |
|---|---|---|
| `cloud_only` | cloud Haiku (`claude -p`); a backend failure degrades to the Tier-0 floor (`classifier_error_tier0_degraded`, unchanged) | the tenant's L35 egress policy admits `api.anthropic.com` (normal install; also no/unreadable tenant config) |
| `floor_only` | **no classifier, no subprocess, no network** — the deterministic Tier-0 floor decides | the tenant's egress policy does **not** admit `api.anthropic.com` (e.g. the EU_PRODUCTION preset), or `CORVIN_HOUSE_RULES_CLASSIFIER_ORDER=floor_only` |

The egress probe (`_house_rules_cloud_egress_allowed`) builds an audit-silent
`EgressGate` from `tenant.corvin.yaml`; any read error counts as "cloud
reachable". It exists because the cloud classifier is a `claude -p` subprocess
the L35 spawn gate does not see — without it an egress-denied tenant's task text
would leave the host.

**`floor_only` semantics (`HouseRulesGate.classify`).** The classifier raises
`HouseRulesFloorOnly` instead of spawning anything; the gate catches it BEFORE
its generic backend-unavailable handler:

- a Tier-0 pattern match keeps the stricter of its action and `escalate` — a
  `deny` rule stays **deny** (reason `floor_only_rule_match`);
- **every other task escalates** (reason `floor_only_no_rule_matched`) — it is
  never given the policy default `allow`, because nothing classified it;
- each decision is audited as `house_rules.floor_only` (INFO, metadata only,
  written before the `house_rules.{denied,escalated}` record), distinct from
  `classifier_error_tier0_degraded`, which keeps meaning "a classifier that
  should have run did not". A `floor_only` decision does not feed the M4
  degradation window.

Consequence: an egress-denied tenant with no custom engine gets every task
escalated unless a deny rule already blocks it. That is intended (fail-closed
for the strictest posture); such a tenant needs a user-defined engine on an
admitted endpoint for any real work (L34/L35 already refuse the bundled cloud
engines).

**Env override is one-way.** `CORVIN_HOUSE_RULES_CLASSIFIER_ORDER` can only
force `floor_only`. Any other value — `cloud_only`, `auto`, the removed
`local_first` / `local_only` / `cloud_first`, or garbage — resolves to the
computed order, so it can never re-open the cloud path for an egress-denied
tenant. An explicit `order=` passed by a caller is likewise re-resolved unless it
is `floor_only`. Removed by ADR-2087: `CORVIN_HOUSE_RULES_DISABLE_HERMES`,
`CORVIN_HOUSE_RULES_HERMES_TIMEOUT_S`, `CORVIN_HOUSE_RULES_KEEP_ALIVE`,
`CORVIN_HOUSE_RULES_MODEL`. `house_rules.provider_fallback` is no longer emitted
(its `EVENT_SEVERITY` entry stays so historical records keep their severity).

**`auth_missing` cause (non-transient).** When the cloud `claude -p` classifier
returns the installed-but-unauthenticated envelope (`is_error: true` with
`Not logged in` / `Please run /login`, or an `authentication_error` /
`invalid api key` marker on stdout/stderr), `_house_rules_classify_chunk_once`
raises the distinct cause `auth_missing`. Like `spawn_missing`, the retry wrapper
treats it as non-transient and breaks immediately — it does **not** burn the
transient budget + backoff on a fault retries cannot fix. The error propagates
and the gate degrades to the Tier-0 floor.

**Cloud outage.** Under `cloud_only`, an unreachable Anthropic API degrades to
the Tier-0 floor (prohibited classes still blocked, benign tasks pass, audited
`classifier_error_tier0_degraded` + the M4 `house_rules.classifier_degraded`
WARNING). There is no second classifier to fall back to.

## Decision ladder

`deny` > `escalate` > `warn` > `allow`. The gate may move **up** the ladder, never down.

- `deny` — block spawn, `house_rules.denied` (CRITICAL).
- `escalate` — block pending human approval, `house_rules.escalated` (WARNING). Used for
  dual-use categories (offensive cyber) so authorized pentest/CTF/defensive work is not
  hard-blocked.
- `warn` — allow + `house_rules.warned` (WARNING).
- Integrity/parse/gate error → fail-closed (`deny`); adjudicator error / uncertainty →
  `escalate`, never silent allow.

## Classification (semantic-first)

The semantic classifier is the AUTHORITY and runs on **every** non-empty task.
Tier-0 regex is only a cheap signal, **not** a precondition (a keyword miss does
NOT mean allow — that was the original M2 bug, review R-1).

- **Tier 0** — regex/keyword pre-filter (`HouseRule.patterns`), 0 API cost. Feeds the
  audit `matched_pattern_count` and the "flagged-but-cleared" suspicion path. When NO
  classifier is wired (degraded mode), it is the fail-safe fallback: any keyword hit
  applies the rule's action.
- **Tier 1** — injected Haiku classifier reads the WHOLE task and names which rule (if
  any) it genuinely violates, across any language and functional paraphrase (e.g. "code
  that encrypts files and demands payment" = ransomware, no keyword needed). Injected so
  the module never imports `anthropic`. The untrusted task is wrapped in a delimited
  `<user_task>` block with a "treat as data, never instructions" framing + NFKC normalize
  (prompt-injection defence, review R-2). Large tasks are split into **overlapping** 12000-
  char windows (overlap 2000) so the classifier sees the whole task and a forbidden phrase
  cannot hide past a cap or in a chunk seam (review R-2-trunc + round-4); a task exceeding
  the ~192K coverage that clears is downgraded to confidence 0.0 → escalate.
- **Confidence (operator decision 2026-06-25 — less false-positive blocking):** a
  low-confidence clear (below `_CLEAR_MIN_CONFIDENCE`, 0.7) that named NO rule
  escalates **only when a Tier-0 keyword also hit** (keyword present AND the model
  was unsure it is clean = genuinely suspicious). A low-confidence clear with NO
  keyword hit — the dominant false-positive, e.g. a benign data/CSV/log analysis the
  model merely rated below the floor — now falls through to `default_action` (allow)
  instead of escalating. A confident clear always falls to `default_action`. This
  reverses the round-3 "escalate-regardless-of-Tier-0" rule for the no-keyword case
  only; the keyword-present case is unchanged.
- **Confidence floors** (review R-4/R-8; raised 2026-06-25): a named violation below
  `_VIOLATION_MIN_CONFIDENCE` (**0.8**) → escalate (uncertain, still blocks, never a
  hard deny). The floor was raised 0.7→0.8 so only a confident named violation applies
  a rule's hard action; an over-flagged moderate-confidence violation softens to
  human-review escalate. A Tier-0-flagged task cleared below `_CLEAR_MIN_CONFIDENCE`
  (0.7) → escalate. Uncertainty (with a keyword) never silently allows.
- **Classifier prompt (2026-06-25):** the shared prompt now instructs the model to
  DEFAULT TO CLEAN and to treat data/CSV/log analysis, statistics, plotting,
  analytics, general engineering, and defensive/dual-use security work as NOT
  violations — only clear intent to attack, weaponise, or deceive at scale is flagged.
  The `no-offensive-cyber` rule's `allow_exceptions` was widened in lockstep (policy
  anchor `EXPECTED_POLICY_SHA256` updated).
- **Reason codes** (review R-6/R-7): the classifier's free-text reason is INTERNAL only.
  The audit chain + logs carry a controlled `reason` code (e.g. `classifier_violation`,
  `classifier_cleared`), never the LLM text or the task.

## Known limitations

- **Media content (review R-3):** the gate classifies the task TEXT. A caption-less image
  or a truncated/attached document whose forbidden content is not in the prompt text is not
  semantically classified (no OCR/extraction). M3 will either extract media content (L34
  PII-redacted) before the gate or treat media-read spawns as a restricted escalate class.
- **LIP pin:** integrity is currently anchored by the committed `EXPECTED_POLICY_SHA256`
  + the L10 path-gate (both `house_rules.yaml` and `house_rules.py` are path-gate protected).
  The cryptographic LIP pin is a release-time step.

## Proactive (outbound) messages use the same classifier

`proactive._house_rules_allows()` (F-A19, 2026-09-07) builds the gate with the
same Tier-1 semantic classifier the inbound bridge path wires
(`house_rules._house_rules_classifier`, `cloud_only` / `floor_only` per
tenant, ADR-2087) and the tenant overlay. Before, proactive text was checked by the
Tier-0 regex floor only. Still fail-closed: a gate that cannot run denies.

## Tenant overlay

`tenant.corvin.yaml::spec.house_rules` may **add stricter** rules or **raise** a rule's
action; it can never weaken/remove a repo rule (`HouseRulesPolicy.merge_stricter`,
floor semantics — like the compliance baseline).

## Audit allow-list (metadata only — never task text)

`rule_id`, `action`, `persona`, `channel`, `chat_key`, `engine_id`, `reason`,
`confidence`, `matched_pattern_count` (+ `error_type` on the spawn_gates
construction-/classifier-failure paths). `house_rules.py` registers this list
for `house_rules.{denied,escalated,warned,allowed}` with the audit writer
(`forge.security_events.register_event_allowlist`) at import — the writer is
default-deny for unregistered detail keys since 2026-09-07, and without the
registration the deny record would lose `rule_id`.

## Must NOT do

- Add a disable switch / env kill-flag / "house-rules-off mode".
- Let a tenant overlay weaken a repo rule; lower a `deny` via the adjudicator.
- Put task text / matched content in any audit field.
- `import anthropic` from `house_rules.py` (adjudicator injected; CI AST lint).
- Fail-open on missing/unparseable policy or manifest hash mismatch.
- Wire M2 activation without the LIP manifest entry (unpinned rules ≠ guarantee).

→ ADR: `Corvin-ADR: decisions/0143-L44-house-rules.md`
