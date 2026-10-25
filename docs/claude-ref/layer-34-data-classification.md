# Layer 34 — Data Classification + Flow Guard (ref doc)

Companion to the short CLAUDE.md section. Full operational details
live here so the main CLAUDE.md stays under the per-session size
budget.

→ **ADR:** Corvin-Knowledge: decisions/ADR-0042-l34-data-classification.md
→ **Module:** `corvin_operator/bridges/shared/data_classification.py`
→ **Tests:** `corvin_operator/bridges/shared/test_data_classification.py`

---

## What this layer does

Enforces a **sensitivity grading** orthogonal to the existing
content-type-presence axis owned by `compliance_zone_classifier.py`.
Both axes must permit a spawn; either can refuse it.

| Axis | Owned by | Question answered |
|---|---|---|
| Sensitivity | **L34** | how harmful is leakage of this content? |
| Content type | `compliance_zone_classifier` | is there PII / external-facing material here? |
| Engine locality | L34 | where does this engine run + what does it call? |
| Engine identity | `engine_policy` | is this engine_id allowed at all in this zone? |

---

## Core API

```python
from data_classification import (
    DataClassification,        # IntEnum: PUBLIC < INTERNAL < CONFIDENTIAL < SECRET
    DataFlowGuard,             # construct once per tenant
    DataFlowDenied,            # raised by validate_or_raise()
    FlowDecision,              # the return shape of validate()
    classify_task,             # heuristic default classifier
    make_forge_audit_writer,   # default audit-writer for production
)
```

### Constructing the guard

```python
import yaml
from pathlib import Path
from data_classification import DataFlowGuard, make_forge_audit_writer

tenant_cfg = yaml.safe_load(Path(tenant_yaml_path).read_text())
audit_path = Path(corvin_home) / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"
guard = DataFlowGuard.from_tenant_config(
    tenant_cfg,
    audit_writer=make_forge_audit_writer(audit_path),
)
```

The factory pulls overrides from
`tenant_cfg["spec"]["data_classification"]`. Missing fields keep the
module defaults. Malformed entries raise `ValueError` — operators
should see configuration errors loudly.

### Validation at the spawn site

```python
decision = guard.validate(
    classification=DataClassification.CONFIDENTIAL,
    engine_id="claude_code",
    persona="coder",
    channel="discord",
    chat_key="dm:42",
)
if not decision.allowed:
    raise RuntimeError(f"data flow denied: {decision.reason}")
```

Or the strict variant:

```python
try:
    guard.validate_or_raise(
        classification=cls,
        engine_id=eid,
        persona=persona, channel=channel, chat_key=chat_key,
    )
except DataFlowDenied as e:
    # e.decision is the FlowDecision; matched_rule discriminates
    return f"refused: {e.decision.reason}"
```

### Diagnostic helper

```python
# Which engines does the guard currently admit for a given grade?
admissible = guard.list_engines_for(DataClassification.SECRET)
# → ["air_gapped"] given the default registry
```

---

## Delegation fan-out alias (`DELEGATION_ENGINE_ID`)

When a web-chat turn is routed through the delegation fan-out, `chat_runtime`
does **not** classify it under the configured OS engine — it classifies it under
a dedicated alias, `DELEGATION_ENGINE_ID` (currently `"acs"`, the ACS
orchestrator). This alias **must** exist as a key in
`DEFAULT_ENGINE_COMPLIANCE`. If it does not, the guard fails closed with
`matched_rule="unknown_engine"` and **silently blocks every delegated turn**
while direct (non-delegated) turns keep working — the failure looks like
"delegation is broken" rather than "an engine is unregistered".

This is exactly the 2026-06-27 production incident (session `web:VErk2UPDjg`):
the producer emitted the literal `"acs"` while the registry only held
`"acs_worker"`, so every delegated turn was blocked. Root fix:

* `DELEGATION_ENGINE_ID` is the **single source of truth**, defined next to the
  registry in `data_classification.py` and re-exported through
  `core/console/corvin_console/_spawn_gates.py`. `chat_runtime` imports the
  constant instead of hard-coding the literal, so the producer and the registry
  key can never drift again.
* `test_data_classification.test_delegation_engine_id_registered` (+ the
  PUBLIC-turn and producer-source-guard tests) lock the invariant at CI for any
  future engine rename.

Adding a new fan-out engine? Add its compliance row to
`DEFAULT_ENGINE_COMPLIANCE` **and** point `DELEGATION_ENGINE_ID` at it in the
same commit; the test will fail otherwise.

---

## Default matrix — residency is the default, widening is opt-in (F-A10, 2026-09-07)

```text
PUBLIC       : {local, eu_cloud, us_cloud}
INTERNAL     : {local, eu_cloud, us_cloud}
CONFIDENTIAL : {local, eu_cloud}            # personal data never to US cloud by default
SECRET       : {local}   (+ network_egress == "none")
```

`CONFIDENTIAL` (a message carrying a name / e-mail / phone) stays in EU/local
jurisdiction unless the operator WIDENS the row explicitly — the audited,
deliberate choice — in `tenant.corvin.yaml`:

```yaml
spec:
  data_classification:
    matrix:
      CONFIDENTIAL: [local, eu_cloud, us_cloud]   # opt IN to a US-cloud engine
```

The enforcement contract is unchanged: with NO `tenant.corvin.yaml` on disk,
`load_guard_for_tenant` returns `None` (no enforcement). The restrictive default
applies the moment a tenant config exists without its own `matrix` — and it is
what the fail-closed fallback for an unparseable config always promised.

`DataClassification.parse()` returns `None` for an unknown value (it used to
return `INTERNAL`, which every default row allows on a cloud engine — a typo in a
caller's label silently downgraded the guard to "allow");
`DataFlowGuard.validate` denies `None` with `matched_rule="unknown_classification"`.

**Three overrides a tenant config may NOT make (R2-A5, 2026-09-07).** The
matrix is the residency policy, so a config line that widens it past these is
refused at load with a `ValueError` naming the offence — loudly, rather than
producing a guard that quietly allows what its own table forbids:

* `matrix.SECRET` may only list `local`. It is the residual floor; widening it
  made the strictest classification in the system weaker than a config line.
  The `network_egress == "none"` rule is an AND, not a substitute — a cloud
  locality that also claimed zero egress passed both.
* `unknown` is not an admissible locality in ANY row. It is the label for an
  engine nobody has classified yet (`opencode` ships with it), so allowing it
  says "send this grade of data wherever we have not looked", and the hole
  widens by itself with every new unclassified engine. Classify the engine
  under `engine_compliance` and name its real locality instead.
* `engine_compliance` for `claude_code` may override neither `locality`
  (already pinned, ADR-0072 V-020) nor `network_egress`. Both are facts about
  the engine: it calls api.anthropic.com. The egress half was unpinned, and
  since the SECRET rule reads exactly that field, `network_egress: none` routed
  SECRET data to a US cloud by the other half of the same false claim.
* The same pin covers every other bundled engine that calls a US cloud API
  (T-0226, ADR-2268): `acs`, `acs_worker`, `anthropic_batch`, `codex_cli`,
  `video_producer`, `video_producer_openai`, `video_producer_edge`
  (`data_classification.PINNED_US_CLOUD_ENGINES`). An `engine_compliance` entry
  that sets one of them to `locality: local` or `eu_cloud`, or to
  `network_egress: none` or `local`, makes `from_tenant_config` raise; the loader
  failure refuses every spawn of that tenant (`spawn_gates._load_l34_guard`),
  fail-closed, never "allow". Restating `us_cloud` / `external` or narrowing to
  `unknown` is accepted, because both only refuse more. `copilot` is the
  deliberate exception: its bundled note documents GitHub Enterprise Cloud EU
  residency and on-premise GHES. At boot `boot_platform` names a tenant's
  rejected entries once (log plus the audit event `data_flow.relabel_rejected`,
  ids and field only). Not covered here: `claude_code` can still be relabelled
  `eu_cloud` (ADR-2268 records it as an open weakness).
* **Provider declarations and `validate_provider` (T-0228, ADR-2268).** For a turn that
  keeps `claude_code` and changes only the endpoint, the operator declares facts about
  the provider under `spec.data_classification.provider_compliance` (`provider`,
  `locality`, `network_egress`, `endpoint_host`, and for a platform provider `platform`,
  `region`, `models`). `parse_provider_compliance` reads them in their OWN step and
  can only add or drop a provider; it never touches the matrix or `engine_compliance`.
  Stricter than `engine_compliance`: `unknown` locality, egress `none`, a missing
  `endpoint_host`, a duplicate declaration and a platform provider without region and
  models are each rejected for that provider only (`guard.provider_compliance_rejected`
  lists them). `DataFlowGuard.validate_provider(classification, engine_id, provider,
  current_host)` judges the pair `claude_code@<provider>` from the declaration and the
  matrix row, and denies: any engine other than `claude_code`; `SECRET` always (the
  CLI is not zero-egress); an undeclared provider; an unknown current host; a current
  host that differs from the declared `endpoint_host`. `spawn_gates.check_l35(...,
  provider=)` judges that provider's host and refuses a provider whose host cannot be
  resolved, never falling back to the engine default. Wired: the reroute
  (`spawn_gates.resolve_l34_route`, below) runs this second pass on every switchable spawn.
* **The sensitive-route setting and its API (T-0218, ADR-2268).**
  `spec.data_classification.sensitive_route: {enabled, provider, model_id}` names the
  declared provider and model a turn would run on instead of being refused. The
  predicates are defined once in `corvin_operator/bridges/shared/sensitive_route.py` and
  read by the gate and the console alike: `ROUTE_BLOCK` (a mapping; `null` is absent),
  `ROUTE_ON` (`enabled` is the boolean `true`), `ROUTE_MALFORMED` (`enabled` present but
  not a boolean, or an enabled block whose `provider` / `model_id` is missing or outside
  its charset; `model_id` is `[A-Za-z0-9._:@/-]{1,200}` and stored verbatim). A provider is
  **eligible** only with an explicit `provider_compliance` entry of locality exactly
  `local` or `eu_cloud`, never the native `anthropic` provider, never an aggregator
  (`model_source: openrouter`) or a provider behind `proxy_base_url`, and only while its
  current egress host still equals the declared `endpoint_host` (a platform provider is
  judged on the host rendered from its DECLARED region and resource, not the
  environment). API on the engine router: `GET /v1/console/settings/engine/sensitive-route`
  (the stored value plus `eligible_providers` with `admitted_classes`; an empty
  declaration set gives an empty list), `PUT …/sensitive-route`
  (`{enabled, provider, model_id}`; `422 target_not_eligible` or `policy_invalid`;
  disabling always works) and `PUT …/provider-compliance` (the server fills
  `endpoint_host`; `422` with `locality_not_admissible`, `network_egress_not_admissible`,
  `unknown_provider`, `native_provider`, `aggregator`, `proxy_provider`,
  `platform_incomplete`). An unparseable tenant file is `422 policy_invalid` and is never
  written; the file is written at mode `0o600`; every write is audited as
  `engine_setting_updated`, every refusal as `engine.setting.update`. The console reads
  and writes the home through `spawn_gates._resolve_corvin_home`, the resolver the gate
  uses. It is read at spawn time by the reroute below, on the
  bridge OS turn and the non-delegated console OS turn.
* **The additive detector (T-0220, ADR-2268 Decision 6).**
  `corvin_operator/bridges/shared/sensitive_detector.py` finds personal data and
  credential prefixes in German and English text and can only RAISE a class: personal
  data (e-mail address, phone number, IBAN with a valid mod-97 checksum and the
  country's length, payment card with a valid Luhn checksum, German tax id with a valid
  check digit AND a context word, US social security number) gives at least
  `CONFIDENTIAL`; credential prefixes (`sk-ant-`, `sk-proj-`, `sk-`, `ghp_`,
  `github_pat_`, `AKIA`, `xox?-`, `AIza`) give `SECRET`, never `CONFIDENTIAL`, because
  `SECRET` is never rerouted. `detect()` returns category names only, never the matched
  text; `raise_class(current, text)` is `max(current, detector)`, so it cannot lower a
  class, override the secret bank or be switched off by a `[class:public]` marker in the
  text; any failure raises `DetectorError`, which the caller must treat as a refusal.
  Measured on a self-written synthetic corpus (75 texts, `tests/fixtures/
  sensitive_detector_corpus.jsonl`, run 2026-10-26): recall email 5/8, phone 9/11, IBAN
  6/7, payment card 5/6, tax id 4/5, US SSN 2/2, credential 8/9 (39 of 48 sensitive
  texts), 1 false positive in 27 harmless texts (`git@github.com:` looks like an
  address). Every miss is a labelled `known_miss` (spelled-out or space-separated
  digits, `[at]`/`[dot]` writing, a tax id with no context word, a prefix split from its
  secret) and the test pins the miss set exactly. **That corpus is not real traffic: do
  not read these numbers as a rate.** Not detected at all: names, postal addresses,
  health or financial prose, birth dates, any value outside the categories above.
  **Wired (T-0219):** `classify_task(..., detector=True)` is the per-tenant opt-in. The
  class is then `max(marker, secret bank, personal_data zone, detector)`; a marker can only
  raise, so `[class:public]` no longer switches classification off; a failing step raises
  `ClassifierError` (fail-closed) instead of becoming `PUBLIC`. The opt-in is
  `DataFlowGuard.detector_on` (an enabled, well-formed `sensitive_route`), read by the four
  production callers of ADR-2268: the turn gate `spawn_gates.check_l34`, the bridge history
  gate `adapter._ledger_data_flow_withhold`, the console history gate
  `chat_runtime._session_ledger_block` and the A2A worker `a2a_worker.spawn_a2a_worker`.
  A malformed `sensitive_route` (`guard.route_malformed`) refuses every turn of the tenant
  in `check_l34` and in the A2A worker. `core/console/tests/test_detector_call_sites_e2e.py`
  drives each caller with a text only the detector recognises. **Still on the legacy
  path:** `delegation.py` and `peer_gate.py` also call `classify_task` and were not on the
  ADR's list; they do not run the detector.
  Measured on the same synthetic corpus (not real traffic): the legacy path flags 29 of 48
  sensitive texts but also 10 of 27 harmless ones; the detector alone flags 39 of 48 and
  1 harmless one; their union flags 41 of 48. A `[class:public]` prefix turns 41 of the 48
  sensitive texts `PUBLIC` on the legacy path and none of them in opt-in mode.
* **The reroute decision (T-0219, ADR-2268 outcome table).**
  `spawn_gates.resolve_l34_route(engine_id, tenant_id, classification=|prompt=, ...)`
  returns one `RouteOutcome`: `native` (exactly where `check_l34` allows today: no policy,
  a PUBLIC turn, an admitted turn), `reroute` (`provider`, `model_id`) or `refuse` with a
  stable `reason`. A reroute needs an enabled, well-formed `sensitive_route`, an INTERNAL or
  CONFIDENTIAL turn, the engine `claude_code`, an eligible declared provider with a
  verbatim-admitted model, and `validate_provider` allowing it under the unchanged matrix.
  SECRET, PUBLIC, an unknown class, a malformed route, a classifier or gate error and any
  ineligible target are refused, never rerouted. **Wired (T-0219, T-0221, T-0229):** the bridge OS turn
  (`adapter._resolve_os_turn_route` → `_apply_sensitive_route`) and the non-delegated console
  OS turn (`chat_runtime._route_off_loop` → `_spawn_gates.resolve_console_spawn`) switch;
  ACS, the video producer, the gateway dispatcher, A2A and a delegated console turn keep
  `check_l34` and refuse (a route that exists but cannot switch there is audited as
  `data_flow.reroute_refused`, reason `site_not_switchable`). `resolve_l34_route` returns an
  `L34Route` (`refusal`, `provider`, `model_id`, `rerouted`): where nothing is rerouted its
  `refusal` is byte for byte the `check_l34`-then-`check_l35` answer; a reroute runs both
  gates again for the provider's current host and audits `data_flow.rerouted` (provider,
  model, engine, class, never the prompt). The switching site builds the env with
  `engine_models.resolve_provider_env_strict` (never degrades to the default endpoint, strips
  every `ANTHROPIC_*` / `CLAUDE_CODE_USE_*` / platform override, verifies the upstream host
  against the declaration and against the host L35 judged) and passes the declared model id as
  `--model`, replacing the OS-model resolver's choice. A route that cannot be built refuses
  the turn. Limit: the ADR-2102 history gates still judge earlier turns against `claude_code`
  itself, so a rerouted turn may carry less history than the provider would be allowed to see
  (over-withholding, never leaking), and `ClaudeCodeEngine` still strips a `provider/` prefix
  from a Claude-lineage model id.
  Adversarial record (T-0223, `test_sensitive_route_adversarial.py`): the detector folds zero-width/format characters and compatibility forms (NFKC) and scans original and folded text, so a zero-width space or a full-width `@` no longer hides an address. **Known limit:** a spelled-out address (`jane dot doe at example dot com`) is not detected; the `[class:...]` marker and the matrix are the mitigation. No route case degrades to the default endpoint: a missing credential, unknown or undeclared provider, illegal model id or changed host raises `ProviderRouteError`.
  E2E: `test_bridge_sensitive_reroute_e2e.py`, `core/console/tests/test_console_sensitive_reroute_e2e.py`.

`SECRET` is the only row kept local-only by default. It fires solely on literal
credentials (API keys, private keys, `password = …`) detected by regex, occurs
rarely in normal use, and stops those credentials from egressing — a security
*floor*, not a residency policy. See ADR-0042 (and the opt-in amendment) for the
rationale.

**Which bundled engine can take CONFIDENTIAL / SECRET (ADR-2091).** The local
inference engines `hermes`, `opencode_ollama` and `claude_code_local` were
removed from `DEFAULT_ENGINE_COMPLIANCE`; `validate` now refuses those ids as
`unknown_engine`. Consequences under the default matrix:

* `SECRET` has **no** bundled admissible engine — none has
  `network_egress == "none"` (`opencode_http` is `local`/`local`), so every
  bundled engine is refused (`secret_egress` or `matrix`).
* `CONFIDENTIAL` is admissible only on `opencode_http` (self-hosted, `local`)
  or on an engine the tenant declares itself under `engine_compliance` with a
  `local` / `eu_cloud` locality. `claude_code`, `codex_cli`, `copilot`, `acs`,
  `acs_worker` and `anthropic_batch` are `us_cloud` and are blocked
  (`matrix`); `opencode` is `unknown` and is blocked until classified.

The ATO M5 route that used to send CONFIDENTIAL/SECRET work to Hermes
(`delegate_hermes`) is gone, but the explicit refusal stays:
`ato_classify.classify` returns `delegation_target = "l34_block"`
(`DELEGATION_L34_BLOCK`) for those classes on a `claude_code` OS turn — the
task is refused, it never falls through to `claude_code`.

## Tenant configuration

The sub-key in `tenant.corvin.yaml`:

```yaml
spec:
  data_classification:
    matrix:
      # Tighter than module default — INTERNAL must stay local.
      INTERNAL: [local]
      # Defaults for PUBLIC / CONFIDENTIAL / SECRET kept by omission.

    engine_compliance:
      - engine_id: opencode
        locality: local
        network_egress: local
        notes: "tenant pins opencode to a self-hosted provider on its LAN"
      - engine_id: mistral_eu
        locality: eu_cloud
        network_egress: external
        notes: "Mistral medium / fr-fr region"
```

Validation rules:

* `matrix` keys must be one of `PUBLIC`, `INTERNAL`, `CONFIDENTIAL`,
  `SECRET` (case-insensitive). Other keys raise `ValueError`.
* Locality values must be one of `local`, `eu_cloud`, `us_cloud`,
  `unknown`.
* `network_egress` values must be one of `none`, `local`, `external`.

Hot-reload is the operator's concern (re-construct the guard when
the YAML mtime changes — mirror the pattern in `bridge.sh`).

---

## Audit contract

Two event types, both emitted via the L16 hash chain:

```jsonc
// data_flow.approved (severity INFO)
{
  "event_type": "data_flow.approved",
  "severity": "INFO",
  "details": {
    "classification": "INTERNAL",
    "engine_id": "opencode_http",
    "matched_rule": "matrix",
    "reason": "matrix allow",
    "persona": "coder",
    "channel": "discord",
    "chat_key": "dm:42"
  }
}

// data_flow.blocked (severity CRITICAL)
{
  "event_type": "data_flow.blocked",
  "severity": "CRITICAL",
  "details": {
    "classification": "SECRET",
    "engine_id": "claude_code",
    "matched_rule": "secret_egress",
    "reason": "SECRET requires network_egress='none', engine has 'external'",
    "persona": "coder",
    "channel": "discord",
    "chat_key": "dm:42"
  }
}
```

Allow-list keys: `classification`, `engine_id`, `persona`, `channel`,
`chat_key`, `reason`, `matched_rule`. The module enforces this at
emission via `_validate_audit_details()`; smuggled keys raise
`ValueError` before the event ever reaches the chain. A regression
test asserts the set.

---

## Default classifier behaviour

`classify_task(task, persona)` precedence:

1. **Secret pattern bank** (cannot be overridden by a marker).
   - OpenAI legacy `sk-…` (no dash after the prefix)
   - Anthropic `sk-ant-…` and OpenAI `sk-proj-…` / `sk-svcacct-…` / `sk-admin-…` (the dash form; these were classified PUBLIC before T-0231)
   - Stripe `sk_live_…`
   - AWS `AKIA…` access-key-id
   - Google `AIza…` API key
   - GitHub `gh[ousr]_…` tokens (personal, OAuth, user, server, refresh) and fine-grained `github_pat_…`
   - Slack `xox[bpoa]-…`
   - PEM `-----BEGIN … PRIVATE KEY-----`
   - `password = …` assignment
   - T-0248 (2026-10-21 review): OpenRouter `sk-or-v1-` + 64 hex, GitLab `glpat-` + 20-char body
     (the routable form's dotted segments included), HuggingFace `hf_` + 34, Groq `gsk_` + 52,
     xAI `xai-` + 80, npm `npm_` + 36 — each at the vendor's documented length, bounded on both
     sides and without `_` in the body, so "the hf_ prefix", `hf_hub_download` or a short
     `gsk_x1y2` stay PUBLIC
   - JWT / compact JWS (`_JwtPattern`): three base64url segments whose HEADER decodes to a JSON
     object naming `alg` — `eyJ` alone is only the encoding of `{"`, so a dotted identifier or a
     three-segment string with a non-JSON header is not a hit
   → `SECRET`.
2. **Explicit marker.** `[class:secret] …`, `[class:confidential] …`,
   `[class:internal] …`, `[class:public] …` — case-insensitive at the
   start of the task. Operator override (only when no secret matched).
3. **PII via compliance_zone_classifier.** A `personal_data` zone hit
   maps to `CONFIDENTIAL`.
4. **Default.** `PUBLIC` — users opt in to stricter classification via
   `[class:internal]` / `[class:confidential]`.

Rationale: residency restriction applies when the user *declares* their data
is sensitive. Defaulting to a restrictive class blocked tasks that use only
public data sources. Under the default matrix above a `CONFIDENTIAL` (PII)
task is refused on every US-cloud engine unless the operator has widened the
`CONFIDENTIAL` row or declared an admissible local / EU engine.

False-positive direction: refuses a benign task on a cloud engine.
False-negative direction: a missed SECRET reaches an external engine.
Operators can install a stricter classifier in the adapter — the guard treats
the classification argument as opaque.

---

## Wiring status

* **M1 (done):** Module + tests + tenant template +
  EVENT_SEVERITY + ADR + ref doc. Standalone; opt-in.
* **M2 (done):** EU_PRODUCTION presets shipped.
* **M2.5 (done):** Adapter `_compliance_gate()` wires
  `DataFlowGuard.from_tenant_config()` alongside the engine-trust
  gate. Classification is computed by `classify_task(prompt, persona)`
  and the guard is called before every engine spawn.

The standalone shape (no adapter dependency) is deliberate: L34
ships + tests in isolation from the adapter wiring.

---

## Must NOT do

* Don't fold classification into `compliance_zone` — orthogonal axes.
* Don't make `data_flow.blocked` advisory; the guard returns
  `allowed=False` and `validate_or_raise` raises.
* Don't put task text, prompt content, or engine output in the audit
  `details` — the allow-list rejects smuggled keys at emission time.
* Don't ship a `claude_code` override flipping it to `local` — the
  locality mapping is load-bearing for the threat model (V-020 / ADR-0072).
  The same holds for `acs`, `acs_worker`, `anthropic_batch`, `codex_cli` and
  `video_producer*` (T-0226): extend `PINNED_US_CLOUD_ENGINES`, never a preset
  that relabels one of them.
* Don't change the default matrix (permissive PUBLIC/INTERNAL, CONFIDENTIAL
  local/EU-only, SECRET-local-only) without an ADR amendment — residency-by-
  default is a deliberate compliance decision (F-A10, ADR-0042 amendment).
* Don't re-add a bundled engine to `DEFAULT_ENGINE_COMPLIANCE` as `local` to
  "unblock" CONFIDENTIAL/SECRET — a tenant declares its own engine under
  `engine_compliance` (ADR-2091).
* Don't fail-open the pre-spawn gate on a load error: when a tenant policy file exists but
  `data_classification` cannot be imported or the yaml cannot be loaded, `spawn_gates.check_l34`
  refuses the spawn (`L34_GUARD_LOAD_FAILED`, never cached; since 2026-10-21 — before, the guard
  became `None` and every prompt passed). Only a tenant WITHOUT a policy file is fail-open.
* Don't fail-open the gate on error: unknown engine / unparseable config must
  still enforce the DEFAULT matrix (which keeps the SECRET floor), never
  allow-all.
* Don't fail-open on an engine WITHOUT a `name`: nothing can be matched
  against the locality matrix, so `adapter._check_compliance_or_fail`
  refuses the spawn (`[compliance] Spawn rejected … fail-closed`) — it
  returned `None` until 2026-09-07 (F-B9), which let any nameless /
  misregistered engine bypass classification entirely.
* Don't import `anthropic` from this module — CI lint enforces.

---

## Tests

```bash
python3 corvin_operator/bridges/shared/test_data_classification.py
```

41 tests covering:

* Enum ordering and parsing
* Default registry shape (claude=us_cloud, opencode_http=local,
  opencode=unknown; the ADR-2091-removed ids are absent)
* Matrix core (PUBLIC/INTERNAL allow us_cloud; CONFIDENTIAL is local/eu_cloud
  only; SECRET stays local + requires egress=none)
* Opt-in tightening (operator matrix override blocks us_cloud per tier)
* Malformed-config fallback still enforces the SECRET floor (not allow-all)
* Unknown engine fail-closed
* **Delegation engine_id invariant** — `DELEGATION_ENGINE_ID` (the engine_id
  `chat_runtime` classifies a *delegated* web-chat turn under) MUST be a key in
  `DEFAULT_ENGINE_COMPLIANCE`, and a PUBLIC delegated turn MUST be approved. A
  producer-side source guard asserts `chat_runtime` references the shared
  constant rather than a hard-coded literal. See *Delegation fan-out alias*
  below.
* `validate_or_raise` exception shape
* Audit allow-list enforcement (smuggled keys rejected)
* Tenant override (matrix + engine_compliance + malformed errors)
* `classify_task` heuristic (markers, secret patterns, PII delegation,
  empty input)
* CI lint (no `import anthropic`)
