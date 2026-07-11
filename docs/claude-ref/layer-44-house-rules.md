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

- **Mechanism** = `operator/bridges/shared/house_rules.py` — core, fail-closed,
  audit-first, **not disableable** (no env flag, no off-switch).
- **Content** = `operator/policy/house_rules.yaml` — committed, operator-edited in git.
  This is the **single source of truth**; you change the rules here.

## Repo linkage + integrity

The gate verifies `house_rules.yaml`'s sha256 against the committed
`EXPECTED_POLICY_SHA256` anchor in `house_rules.py`. A mismatch (local tamper to
weaken the rules) → fail-closed deny. The anchor file `house_rules.py` is itself
L10-path-gate-protected (runtime writes blocked) and a mandatory Tier-3
capability. After editing the policy:

```bash
sha256sum operator/policy/house_rules.yaml   # paste digest into EXPECTED_POLICY_SHA256
```

A CI test (`test_policy_anchor_matches_repo_file`) fails the build if the anchor
drifts from the shipped file.

**Release-time hardening (Corvin Labs):** add `house_rules.py` and
`house_rules.yaml` to `layer_integrity.MANDATORY_LAYER_FILES` and re-sign
`operator/security/layer-manifest.json` (`python operator/security/sign_layer_manifest.py`).
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
  `operator/bridges/shared/a2a_worker.py::spawn_a2a_worker()` delegates to
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
  `operator/license/tests/test_a2a_license_fixes.py` (the `_allow_l44()` helper
  neutralises the gate so the compute-quota/env-clearing assertions stay
  isolated).
- `adapter._house_rules_adjudicator()` is the Tier-1 Haiku call
  (`claude -p --max-turns 1 --tools ""`, helper-model site `house_rules_adjudicator`,
  20 s timeout). It runs on EVERY non-empty task (NOT only on a Tier-0 hit — see
  Classification) and classifies the whole task against the full ruleset. On
  timeout/parse-failure it raises → the chain falls through to the next provider.
  - **Backend-unavailable → degrade to the Tier-0 floor (2026-07-11, load-bearing):**
    when the semantic classifier callable RAISES because *no* backend can run at all
    (Hermes AND cloud both unavailable — the dominant case is a **fresh install** in
    the seconds/minutes before Hermes is provisioned or `claude` is logged in, or a
    transient outage), `classify()` no longer escalates EVERY task. It **degrades to
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
    `_house_rules_classify_chunk()` now retries the spawn **once**
    (`_HOUSE_RULES_RETRIES = 1`, `_HOUSE_RULES_RETRY_BACKOFF_S = 1.5 s`) before
    giving up; if it still fails the gate escalates **exactly as before** — the
    retry narrows false positives without ever weakening the gate. A `spawn_missing`
    (CLI absent) cause is NOT retried (pointless). The internal
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

### Local provider chain + cloud-outage resilience (ADR-0157 M3)

The Tier-1 classifier is a **two-provider chain**, not a single cloud call:
local Ollama/Hermes **and** cloud Haiku, ordered per ADR-0161, then fail-closed
(`_house_rules_classify_with_chain`). Having a working local provider lets the
gate keep classifying when the Anthropic API is unreachable (e.g. a 500 outage)
— without it, a cloud outage escalates EVERY task (`classifier_error`) and the
bridge becomes a wall: every message returns "couldn't be safety-checked, send
it again", and the chat is only steerable via slash-commands (which are
dispatched **before** the spawn gate).

**Provider order (ADR-0161, `_house_rules_resolve_order`)** — resolved ONCE per
task, env override → `auto`:

| order | sequence | when (`auto`) |
|---|---|---|
| `cloud_first` | cloud → local → fail-closed | `default_engine` ≠ hermes **and** egress permits `api.anthropic.com` (normal cloud install — fast ~2 s, local rescues a cloud outage) |
| `local_first` | local → cloud → fail-closed | `default_engine` **= hermes** + egress permits `api.anthropic.com` (local-intent tenant: classify on-host first, cloud only as last-resort fallback) — also any explicit override |
| `local_only`  | local → fail-closed (NEVER cloud) | egress **denies** `api.anthropic.com` (EU_PRODUCTION / CONFIDENTIAL — task text must stay on-host) — for **either** engine |
| `cloud_only`  | cloud → fail-closed | `CORVIN_HOUSE_RULES_DISABLE_HERMES=1` (back-compat) |

`auto` is **engine-aware**. It reads the tenant's `spec.default_engine`
(`tenant.corvin.yaml`, the same source `engine_models` uses) **and** the L35
egress policy (`EgressGate.from_tenant_config`, audit-silent probe) to choose the
primary provider:

- **Hermes tenant** (`default_engine: hermes` — fully-local Ollama intent) →
  `local_first` (or `local_only` when egress is also denied). This stops a
  local-intent tenant from leaking task text to `api.anthropic.com` every turn,
  and — when the `claude` CLI is installed-but-unauthenticated — stops it burning
  the transient-retry budget on the cloud path each turn (see `auth_missing`).
- **Cloud/claude_code tenant** (or no `default_engine`) → the legacy
  egress-keyed behaviour: `cloud_first` when egress permits the cloud host, else
  `local_only`.

This **closes a latent residency bug**: the old resolver keyed ONLY on egress and
defaulted to `cloud_first`, so a Hermes tenant still tried cloud Haiku FIRST every
turn. `local_only` still forbids any local→cloud fallthrough in an egress-denied
tenant. Override the resolved order with `CORVIN_HOUSE_RULES_CLASSIFIER_ORDER` ∈
`auto|cloud_first|local_first|local_only|cloud_only`. The
`house_rules.provider_fallback` audit event records `provider` / `cause` /
`fallback_to` whenever the primary falls through to the secondary.

**`auth_missing` cause (non-transient).** When the cloud `claude -p` classifier
returns the installed-but-unauthenticated envelope (`is_error: true` with
`Not logged in` / `Please run /login`, or an `authentication_error` /
`invalid api key` marker on stdout/stderr), `_house_rules_classify_chunk_once`
raises the distinct cause `auth_missing`. Like `spawn_missing`, the retry wrapper
treats it as non-transient and breaks immediately — it does **not** burn the
~3-attempt transient budget + backoff on a fault retries cannot fix. The gate
still fails CLOSED (the cause propagates; the secondary provider / escalate runs).

Two load-bearing properties of the local path (both were broken — fixed):

- **Model = the model the RUNNING engine uses (the tenant's configured one),
  never a private default.** The check is performed by the engine that is
  actually configured/running on the host — "the engine that runs does the
  check". The local classifier resolves its Ollama model as: the tenant's
  CONFIGURED `spec.hermes_model` (alias→tag via `HERMES_MODEL_ALIASES`, read by
  `_house_rules_tenant_hermes_model`) → `CORVIN_HERMES_MODEL` env →
  `agents.hermes_engine._resolve_default_model()` built-in. This means a box
  bootstrapped with `hermes-fast` (`qwen3:1.7b`) classifies with `qwen3:1.7b` and
  needs **no separate Ollama model** — the classifier never asks for a model the
  configured engine did not pull. (It previously resolved only the built-in
  default `qwen3:8b`; a small-RAM box that pulled only `qwen3:1.7b` then hit
  Ollama 404 → `classifier_error` → fail-closed block of every request even
  though Hermes was configured and ready.) The check stays **fail-closed**: if
  the configured engine genuinely cannot classify (e.g. no engine set up yet),
  the turn is escalated/blocked, never allowed. A reachable Ollama that
  **rejects** a request (HTTP 4xx, e.g. `model 'x' not found`) raises the
  distinct cause `local_misconfigured` and logs loudly (config fault ≠ transient
  blip), then still falls through to cloud per the resolved order.
- **Timeout sized for a local model.** `_HOUSE_RULES_HERMES_TIMEOUT_S` defaults
  to **30 s** (env `CORVIN_HOUSE_RULES_HERMES_TIMEOUT_S`). A local 8B model needs
  ~8 s warm and more on a cold start (VRAM load); the old 10 s budget sat below
  the cold-start time, so the local-primary call timed out and fell through to
  cloud — re-bricking on any cloud 500. The "local is faster than cloud" premise
  was wrong: a local 8B model is **slower** than cloud Haiku.

**Residual fail-closed (by design, not a bug):** when BOTH the local model and
cloud Haiku are down, the gate still escalates (`classifier_error`) and blocks —
this is the mandated L44 fail-closed property (ADR-0143) and is NOT removable
without fail-opening the gate. The fix removes the *common* brick (cloud-only
outage) by repairing the local fallback; it cannot promise zero blocks when no
classifier backend is reachable at all.

**Latency note:** under the default `auto`, a normal cloud install (no
`default_engine` or `claude_code`) resolves to `cloud_first`, so the fast cloud
Haiku (~1–2 s) is the primary and the local model (qwen3:8b, ~8 s warm) is only
paid when the cloud is actually failing — no per-task local latency in healthy
operation. A Hermes-default tenant resolves to `local_first` and pays the local
classify time on every uncached task (it deliberately keeps task text on-host;
cloud is only the last-resort fallback). An egress-denied/EU tenant
resolves to `local_only` and pays the local classify time on every uncached task
(amortised by the 5-min clear-verdict cache) — that is the cost of keeping task
text on-host, which is mandatory there. A local 8B model is **slower** than
cloud Haiku, so never expect local-first to be the low-latency option.

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

## Tenant overlay

`tenant.corvin.yaml::spec.house_rules` may **add stricter** rules or **raise** a rule's
action; it can never weaken/remove a repo rule (`HouseRulesPolicy.merge_stricter`,
floor semantics — like the compliance baseline).

## Audit allow-list (metadata only — never task text)

`rule_id`, `action`, `persona`, `channel`, `chat_key`, `engine_id`, `reason`,
`confidence`, `matched_pattern_count`.

## Must NOT do

- Add a disable switch / env kill-flag / "house-rules-off mode".
- Let a tenant overlay weaken a repo rule; lower a `deny` via the adjudicator.
- Put task text / matched content in any audit field.
- `import anthropic` from `house_rules.py` (adjudicator injected; CI AST lint).
- Fail-open on missing/unparseable policy or manifest hash mismatch.
- Wire M2 activation without the LIP manifest entry (unpinned rules ≠ guarantee).

→ ADR: `Corvin-ADR: decisions/0143-L44-house-rules.md`
