# Art. 14 — Human oversight

> **Short summary:** Art. 14 requires that high-risk AI systems (and by deployer obligation,
> Limited Risk systems) can be overseen and corrected by humans. In Corvin, oversight
> is implemented as three interoperable technical controls: compliance-zone routing,
> data classification gating, and network egress lockdown.

<p align="center">
  <img src="../diagrams/25-data-flow-guard.svg"
       alt="Data classification × engine compliance matrix"
       width="900" />
</p>

---

## What "human oversight" means technically

EU AI Act Art. 14 requires that deployers be able to:
- Monitor the AI system's operation
- Interrupt, correct, or override outputs
- Understand what data the AI is processing and where

Corvin implements this through three complementary technical gates that **restrict what the
AI engine can do before it does it** — not by inspecting outputs after the fact.

---

## Control 1 — Compliance-zone routing

**What it does:** Routes messages to engines that are geographically and jurisdictionally
authorized for the tenant's declared `data_residency`.

**Configuration (tenant.corvin.yaml):**

```yaml
spec:
  data_residency: eu          # "eu" | "us" | "local"
  allowed_engines:
    - opencode_http            # self-hosted OpenCode HTTP server on the tenant LAN
    - claude_code              # only if data_residency permits
  forbid_engines:
    - codex_cli                # explicitly forbidden
```

**How it enforces oversight:**
- The operator declares which engines are permitted
- The system validates every spawn against the `allowed_engines` list
- A forbidden engine never receives message content — the block happens before the call
- All denials emit `gateway.engine_denied` (CRITICAL) into the audit chain

**Regulation:** EU AI Act Art. 14 §4 (human oversight measures "appropriate to the risks")

---

## Control 2 — Data Classification Flow Guard (Layer 34)

**What it does:** Assigns a sensitivity level to every conversation context and blocks engines
whose jurisdiction or network properties don't match.

**Module:** `corvin_operator/bridges/shared/data_classification.py`

### Classification levels

Enforcement starts the moment a `tenant.corvin.yaml` exists (no tenant config
on disk → no L34 enforcement). A tenant config without its own `matrix` gets the
**restrictive default matrix** (`DEFAULT_MATRIX` in `data_classification.py`):
residency is the default, and WIDENING it (e.g. `CONFIDENTIAL: [local, eu_cloud,
us_cloud]`) is the operator's explicit, audited choice in `tenant.corvin.yaml`.

| Level | Meaning | Default matrix (shipped) |
|---|---|---|
| `PUBLIC` (0) | No sensitivity | Any locality |
| `INTERNAL` (1) | Business-sensitive | Any locality |
| `CONFIDENTIAL` (2) | Personal data (name / e-mail / phone) | `local` or `eu_cloud` |
| `SECRET` (3) | Literal credentials / regulated data | `local` + `network_egress: none` |

**No bundled local-inference engine (ADR-2091).** Hermes and every local-Ollama
engine were removed. Of the bundled engines only `opencode_http` (self-hosted
OpenCode HTTP on the tenant LAN, `local`/`local`) is admissible for CONFIDENTIAL,
and **no bundled engine is admissible for SECRET** (none has `network_egress:
none`) — SECRET turns are blocked unless the tenant declares its own engine via
`engine_compliance` (see *Tenant override* below).

### Engine locality + egress classification

Corvin ships with pre-classified compliance metadata for each engine:

| Engine ID | Locality | Network Egress | Notes |
|---|---|---|---|
| `claude_code` | `us_cloud` | `external` | api.anthropic.com — US jurisdiction |
| `codex_cli` | `us_cloud` | `external` | api.openai.com — US jurisdiction |
| `opencode_http` | `local` | `local` | Self-hosted HTTP on LAN |
| `opencode` | `unknown` | `external` | Provider-dependent; operator must override |

### Tenant override

Operators can override the default compliance matrix per engine:

```yaml
spec:
  data_classification:
    engine_compliance:
      my_private_model:
        locality: local
        network_egress: none
        notes: "Air-gapped model on dedicated hardware"
    matrix:
      CONFIDENTIAL:
        - locality: local
        - locality: eu_cloud    # custom override: eu_cloud also allowed for CONFIDENTIAL
```

### What happens on a mismatch

If the engine's locality doesn't satisfy the classification requirement:

1. `DataFlowGuard.validate()` returns `FlowDecision(allowed=False, reason="locality mismatch: …")`
2. `data_flow.blocked` (CRITICAL) is emitted into the audit chain
3. `IncidentAutoDetector` opens a `engine_policy_violation` incident automatically
4. The engine is **never spawned** — the block is pre-spawn, not post-output

**Audit allow-list (never includes task text):**

```json
{
  "classification": "CONFIDENTIAL",
  "engine_id": "claude_code",
  "persona": "research",
  "channel": "discord",
  "chat_key": "1234:5678",
  "reason": "locality mismatch: engine=us_cloud, required=local"
}
```

**Test coverage:** `corvin_operator/bridges/shared/test_data_classification.py`

---

## Control 3 — Network Egress Lockdown (Layer 35)

**What it does:** Restricts which hosts the engine is allowed to contact, at the DNS/hostname
level, enforced before the engine is spawned.

**Module:** `corvin_operator/bridges/shared/egress_gate.py`

### Configuration

```yaml
spec:
  egress:
    enabled: true
    default_action: deny           # deny unless explicitly allowed
    allowed_hosts:
      - localhost
      - 127.0.0.1
      - opencode.internal          # e.g. the tenant's self-hosted OpenCode HTTP server
    forbidden_hosts:
      - api.anthropic.com          # EU production: US cloud blocked
      - api.openai.com
```

### Decision precedence

1. **`forbidden_hosts`** — explicit deny, always wins
2. **`allowed_hosts`** — explicit allow
3. **`default_action`** — `allow` or `deny` for everything unmatched

### EU production presets

Corvin ships one ready-made configuration in `corvin_operator/bundle/config-templates/`:

| Preset | Default action | Description |
|---|---|---|
| `eu_production_http` | `deny` | Self-hosted HTTP + local; all US cloud blocked |

The former `eu_production_ollama` preset was removed with local Ollama inference
(ADR-2091). A tenant whose `deployment_profile` still reads `eu_production_ollama`
keeps the strict EU-production checks (self-test and operator declaration treat
it as `eu_production`).

### What happens on an egress block

If the target host is not permitted:

1. `EgressGate.validate()` returns `EgressDecision(allowed=False, matched_rule="default_deny")`
2. `egress.blocked` (CRITICAL) emitted to audit chain
3. `IncidentAutoDetector` opens `engine_policy_violation` incident automatically
4. Engine is never spawned

**Audit allow-list (never includes URL path or request body):**

```json
{
  "host": "api.anthropic.com",
  "engine_id": "claude_code",
  "persona": "research",
  "reason": "default_deny",
  "matched_rule": "default_deny"
}
```

**Test coverage:** `corvin_operator/bridges/shared/test_egress_gate.py`

---

## How the three controls interlock

These are three independent, complementary lines of defence:

| Threat | L34 Data Classification | L35 Egress Gate | Compliance-zone routing |
|---|---|---|---|
| Wrong jurisdiction engine selected | Blocks at locality check | Blocks at host check | Blocks at zone routing |
| Operator misconfigures zone | Blocked by classification | Blocked by egress | N/A (zone is the root) |
| Engine tries to exfiltrate to US cloud | Not directly detectable | Blocks outbound connection | Blocked at zone |
| Unknown engine added | Default → INTERNAL (partial) | default_action:deny | Must be in allowed_engines |

A deployment with all three controls active is robust against:
- Accidental engine misconfiguration
- Operator error (wrong zone)
- Prompt injection attempting to use a non-permitted engine

---

## Compliance manifest rules

```yaml
# eu-ai-act.yaml
- id: eua.art14.compliance_zone
  article: "Art. 14"
  severity: critical
  invariants:
    - "zone routing blocks engines not in allowed_engines"
    - "data_flow.blocked is CRITICAL, not advisory"
  implemented_by:
    - layer: compliance_zone_routing
      file: corvin_operator/bridges/shared/compliance_zone_classifier.py

- id: eua.art14.engine_policy
  article: "Art. 14"
  severity: critical
  implemented_by:
    - layer: compliance_zone_routing
      file: corvin_operator/bridges/shared/engine_policy.py
```

Both rules are verified by `bridge.sh doctor` at every boot and blocked at PR time by
the GitHub Actions Haiku review (`compliance-check.yml`).
