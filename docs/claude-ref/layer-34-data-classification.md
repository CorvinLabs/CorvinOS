# Layer 34 — Data Classification + Flow Guard (ref doc)

Companion to the short CLAUDE.md section. Full operational details
live here so the main CLAUDE.md stays under the per-session size
budget.

→ **ADR:** Corvin-ADR: decisions/0042-L34-data-classification.md
→ **Module:** `operator/bridges/shared/data_classification.py`
→ **Tests:** `operator/bridges/shared/test_data_classification.py`

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

## Default matrix — residency restriction is opt-in

```text
PUBLIC       : {local, eu_cloud, us_cloud}
INTERNAL     : {local, eu_cloud, us_cloud}
CONFIDENTIAL : {local, eu_cloud, us_cloud}
SECRET       : {local}   (+ network_egress == "none")
```

The default is **permissive**: a zero-config single-operator install runs
frictionless on its configured cloud engine (e.g. `claude_code` = `us_cloud`).
A normal chat message containing a name or e-mail classifies as `CONFIDENTIAL`
and must NOT be blocked by default. Data-residency restriction (EU/local-only)
is an explicit operator opt-in via the tenant matrix below (see the
`tenant.corvin.eu-production-ollama.yaml` preset). This mirrors the classifier's
own stance: *"Default is PUBLIC — users opt in to restriction."*

`SECRET` is the only row kept local-only by default. It fires solely on literal
credentials (API keys, private keys, `password = …`) detected by regex, occurs
rarely in normal use, and stops those credentials from egressing — a security
*floor*, not a residency policy. See ADR-0042 (and the opt-in amendment) for the
rationale.

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
        notes: "tenant pins opencode to --provider ollama"
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
    "engine_id": "opencode_ollama",
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
   - OpenAI / Anthropic `sk-…`
   - Stripe `sk_live_…`
   - AWS `AKIA…` access-key-id
   - Google `AIza…` API key
   - GitHub `ghp_…` personal access token
   - Slack `xox[bpoa]-…`
   - PEM `-----BEGIN … PRIVATE KEY-----`
   - `password = …` assignment
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
public data sources. Combined with the permissive default matrix above, a
`CONFIDENTIAL` (PII) task still runs on a cloud engine unless the operator has
tightened the matrix.

False-positive direction: routes a benign task to the local engine.
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
* Don't change the default matrix (permissive PUBLIC/INTERNAL/CONFIDENTIAL,
  SECRET-local-only) without an ADR amendment — the opt-in residency posture
  is a deliberate compliance decision (see ADR-0042 amendment).
* Don't fail-open the gate on error: unknown engine / unparseable config must
  still enforce the DEFAULT matrix (which keeps the SECRET floor), never
  allow-all.
* Don't import `anthropic` from this module — CI lint enforces.

---

## Tests

```bash
python3 operator/bridges/shared/test_data_classification.py
```

41 tests covering:

* Enum ordering and parsing
* Default registry shape (claude=us_cloud, opencode_ollama=local,
  opencode=unknown)
* Matrix core (PUBLIC/INTERNAL/CONFIDENTIAL allow us_cloud by default —
  residency is opt-in; SECRET stays local + requires egress=none)
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
