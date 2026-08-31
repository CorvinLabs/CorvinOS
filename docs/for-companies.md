# Corvin for companies — integration & compliance

> **Audience.** Tech leads, security officers, compliance owners and
> product managers in regulated or scale-out organisations evaluating
> Corvin for production. This page is the *integration & compliance*
> brief — concrete plug-points, the AWP-standards story, and the
> EU-AI-Act / DSGVO posture. For the architectural overview, see
> [overview.md](overview.md); for the full reference, see
> [layer-model.md](layer-model.md).
>
> Companion brief: [for-organizations.md](for-organizations.md) covers
> the broader business / pricing / market framing. *This* page is the
> hands-on technical-and-regulatory entry point.

---

## TL;DR — what Corvin lets you do without forking it

Drop your own files into the configuration tree, restart nothing, and
the platform adapts:

| You drop … | Corvin does |
|---|---|
| `operator/cowork/personas/<name>.json` | New persona with custom system prompt, MCP servers, tool surface, working dir, default engine, TTS voice |
| `operator/bridges/<channel>/daemon.js` | New messaging channel — Teams, Mattermost, Matrix, in-house — slash-command dispatcher comes free |
| `bridges/<channel>/settings.json` → `chat_profiles[<id>]` | Per-chat audience, role list, observer transcript, persona pin |
| `<corvin_home>/global/engine_policy.json` | Compliance-zone routing — PII to EU engines, code to dev engines, declarative |
| `<corvin_home>/global/secrets.json` | Capability-style secret vault, per-persona ACL, never reaches the LLM context |
| `workflow.awp.yaml` | Multi-step DAG that the engine-driven walker executes node-by-node, R17-validated, 5D-budget-tracked |
| `<scope>/skill-forge/skills/<name>/SKILL.md` | Linter-gated knowledge artefact promoted by usage grades |
| `<scope>/forge/tools/<name>.py` | Sandboxed deterministic tool, bwrap-isolated, capability-bound secrets |

Hot-reload covers everything except channel tokens and HTTP ports. Audit
chain captures the change so a regulator can see *what changed when*.

---

## Why companies care — three load-bearing properties

**1. Reach.** One agent, six built-in channels (Discord, Slack, Telegram,
WhatsApp, e-mail, plus pluggable custom adapters). Staff use the channel
they already have open; per-chat memory survives across sessions.

**2. Specialisation.** Per-chat personas expose different tool surfaces,
system prompts and MCP servers. Same agent, different role per channel.
The auto-router picks the right persona per message; per-persona
LDD-disciplines and TTS voices apply.

**3. Audit-evident operation.** Every grant, refusal, secret injection,
tool call, observer admit, engine selection and compliance-zone match
lands in a single SHA-chained JSONL file. A daily systemd timer verifies
the chain and pages the on-call channel on break. `voice-audit verify`
walks the file offline and reports the first divergence.

---

## Tenants — multi-organisation isolation on one runtime

Corvin carries a **fifth scope above `(task, session, project, user)`:
`tenant_id`**. One Corvin install can serve many
organisations from a single binary, each with its own:

* audit chain (no cross-tenant fusion — hash chains are per chain)
* secret vault, gateway tokens, OIDC trust file, SCIM user store
* engine-policy file (`tenant.corvin.yaml`)
* skill / persona / forge-tool workspaces
* run history, webhooks, rate-limit bucket

A leaking gateway token, a forged tool, a misconfigured persona —
nothing crosses the tenant boundary. The isolation is structural
(filesystem + per-tenant resolvers), not advisory.

### Single-operator vs multi-tenant — same code, two shapes

```
~/.corvin/
├── tenants/
│   ├── _default/           ← single-operator (zero config)
│   │   ├── global/{forge,roles,consent,quota,disclosure,auth,...}
│   │   ├── sessions/<bridge>:<chat>/
│   │   ├── forge/, skill-forge/, voice/, cowork/
│   └── acme/               ← provisioned via gateway or operator CLI
│       └── global/, sessions/, forge/, ...
├── global → tenants/_default/global   (back-compat symlinks)
├── sessions → tenants/_default/sessions
└── ...
```

Single-operator setups never touch `tenants/`. The migration helper
moves an existing `~/.corvin/` into `tenants/_default/` and creates
back-compat symlinks the first time the adapter boots after a version
bump — idempotent, audit-first, opt-out via `CORVIN_TENANT_MIGRATE=0`.

Multi-tenant deployments add `tenants/<other_id>/` directories.
Resolution is keyword-only — every state-store function carries an
optional `tenant_id=None` kwarg defaulting to the env var
`CORVIN_TENANT_ID` and ultimately to `_default`.

### Provisioning a tenant — the Gateway REST API

A tenant is reachable through `core/gateway/`, an **opt-in**
FastAPI surface that single-operator setups never enable. Phase 2
shipped these endpoints:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/tenants/{tid}/runs` | Submit an AWP-shaped Run; 202 + `{run_id}` |
| `GET`  | `/v1/tenants/{tid}/runs/{run_id}` | Poll run status + result |
| `GET`  | `/v1/tenants/{tid}/runs/{run_id}/events` | SSE stream of engine events |
| `GET`  | `/v1/tenants/{tid}/metrics` | Per-tenant Prometheus scrape (Phase 6) |
| `GET`  | `/v1/tenants/{tid}/scim/v2/Users` | SCIM 2.0 user provisioning (Phase 3.5/3.6) |
| `GET`  | `/healthz` | Liveness probe |

Every endpoint is gated by **bearer-token auth that identifies the
tenant**. A token bound to `acme` cannot operate on `globex`'s
resources even when the URL points at globex — the mismatch returns
403 + emits `gateway.cross_tenant_denied` into the *token's* tenant
chain.

Outbound: signed webhook callbacks (HMAC-SHA256, GitHub-shape) on
terminal run states. Delivery is at-least-once with exponential
backoff; 4xx is permanent, 5xx is retried.

### Per-tenant policy (`tenant.corvin.yaml`)

Operator drops `<corvin_home>/tenants/<tid>/global/tenant.corvin.yaml`:

```yaml
apiVersion: corvin/v1
kind: Tenant
metadata:
  id: acme
  display_name: ACME Corporation
spec:
  data_residency:
    zone: eu-west
    allowed_engines: [claude_code, vllm_eu]
    forbid_engines:  []
  budget:
    max_runs_per_day:          5000
    max_tokens_per_day:        10_000_000
    max_wall_clock_per_run_s:  300
```

The dispatcher consults this file before every engine spawn:

* **Engine-policy gate** — `allowed_engines` / `forbid_engines` lists
  decide whether a given engine may run. Forbid beats allow; empty
  allowlist is permissive. A denied run fails fast with
  `engine-not-allowed` and lands in the chain as `gateway.engine_denied`.
* **Zone-residency gate** — `data_residency.zone` is compared against
  each engine's `zone` attribute. Engines without a zone serve every
  tenant (`global`); a tenant pinned to `eu-west` refuses every
  non-`eu-west` engine. The denial is `gateway.zone_denied`.
* **Rate-limit** (Phase 7) — `max_runs_per_day` gates run submission
  with a token bucket; throttled tenants get 429 + `gateway.rate_limited`
  *before* body validation, before any engine work.

Strict schema (`extra="forbid"`); a typo in `allowed_engines` won't
silently drop. A missing or malformed `tenant.corvin.yaml` fails
*closed* — every engine is denied until the operator restores or
removes the file. The `metadata.id` field must match the on-disk
tenant directory or the load fails — a misplaced YAML can't be
silently consumed by the wrong tenant.

### Identity — bearer tokens AND OIDC/JWT side by side

Two authentication classes share the same `Authorization: Bearer`
header, dispatched by token shape:

| Header value | Resolver | Backed by |
|---|---|---|
| `atlr_<32 hex>` | gateway bearer | `<tenant>/global/auth/gateway_tokens.json` |
| `<header>.<payload>.<sig>` | OIDC / JWT | `<tenant>/global/auth/oidc.yaml` (issuer + JWKS or `jwks_uri`) |

`atlr_` tokens issue with `python -m corvin_gateway.cli token issue
<tenant>`; plaintext is shown to the operator exactly once. JWTs verify
against the tenant's pinned-or-fetched JWKS, validate `iss` + `aud` +
`exp` + algorithm allowlist (default `RS256` / `ES256`; `HS256` opt-in
per tenant), and additionally cross-check `claims[tenant_claim] ==
on-disk-tenant-id`. A JWT issued by ACME's IdP cannot authenticate
against GLOBEX's tenant URL even if an operator misplaced the trust
file.

**SCIM 2.0** (`/v1/tenants/{tid}/scim/v2/Users`) supports `GET` /
`POST` / `PATCH` / `DELETE`, enough for Keycloak / Okta / Azure AD to
provision users into the tenant automatically. The Keycloak smoke
harness in `core/gateway/scripts/` drives the full
`client_credentials → POST run → poll → SSE → cross-tenant denial`
loop end-to-end.

### Per-tenant observability (Phase 6)

`GET /v1/tenants/{tid}/metrics` returns Prometheus exposition format
(`text/plain; version=0.0.4`). The shape is a *read-side projection of
the audit chain*, not a parallel telemetry pipeline — 14 curated
metric families cover gateway-run lifecycle, webhook delivery, auth
failures, cross-tenant / engine-policy / zone denials, forge tools,
skill creations, dialectic decisions, consent drops, quota
exhaustions, path-gate denials, plus two sanity gauges (audit-chain
events + audit-chain intact).

Two Grafana dashboards ship in `docs/observability/grafana/`:

* `corvin-overview.json` — gateway health, run throughput, latency
  histogram (p50 / p95 / p99), webhook delivery, chain intact gauge.
* `corvin-security.json` — auth failures by reason, cross-tenant
  denials, engine + zone denials, path-gate by tool, consent drops,
  quota exhaustions.

Both expose a templated `tenant` variable. Operator imports them via
the Grafana UI or the HTTP API. Cardinality is bounded by a curated
label allowlist — values outside the curated set collapse to `"other"`
so a hostile or buggy tenant cannot saturate the metrics surface.

### Per-tenant packaging — `.corvin-pkg` (Phase 5)

Signed archive format for cross-tenant / cross-host distribution of
skill / persona / forge-tool bundles. ed25519-signed gzip tar with a
single `manifest.corvin.yaml` at the root and a `payload/{skills,
personas, tools}/` tree.

```bash
# operator-side
python -m corvin_gateway.cli package keygen priv.pem pub.pem
python -m corvin_gateway.cli package build ./my-bundle \
    --name acme-skills --publisher ACME --version 0.1.0 \
    --private-key priv.pem
# delivers acme-skills.corvin-pkg + .sig

# integrator-side
python -m corvin_gateway.cli package verify acme-skills.corvin-pkg \
    --public-key pub.pem
python -m corvin_gateway.cli package install acme-skills.corvin-pkg \
    --tenant acme --public-key pub.pem
```

The verifier rejects tampered archives, wrong-key signatures, symlinks
anywhere in source or archive, and any top-level entry outside the
curated `{skills, personas, tools}` allowlist. The install path
extracts into the tenant's `packages/` tree and emits
`package.installed` into the tenant chain.

### Container deployment (Phase 4)

OCI image + Helm chart for orchestrated deployments. Single-operator
setups never use them — they are the opt-in path for organisations
that already run Kubernetes:

```bash
docker build -t corvin-gateway:latest \
    -f core/gateway/Dockerfile .
helm install gateway core/gateway/chart
```

The chart ships with sane defaults — single replica, 10 Gi PVC at
`CORVIN_HOME`, non-root uid 4711, `/healthz` probes. Operator-side
secrets (`gateway_tokens.json`, `webhook_secrets.json`, `oidc.yaml`)
mount via the `extraVolumes` slot from operator-owned Kubernetes
Secret manifests — never baked into the chart.

### What you, the integrator, must NOT do

* Don't create `<corvin_home>/tenants/<tid>/` directories by hand.
  The migration helper (or the future provisioning CLI) is the only
  sanctioned path; manual setup races with the boot hook.
* Don't share tokens across tenants. Token-class auth identifies the
  tenant; a leaked token for ACME doesn't reach GLOBEX, *unless* an
  operator copy-pastes it across stores.
* Don't migrate audit chains across tenants. Each tenant's chain is
  independent; cross-tenant fusion is structurally meaningless.
* Don't expose `/metrics` without bearer auth. Public scrapes leak
  per-tenant activity volume and security-incident cadence to anyone
  with network reach to the gateway.

---

## Extending Corvin — your plug-in surface

Everything below ships as a file you drop into a directory. No fork,
no rebuild — `bash operator/bridges/bridge.sh restart` if you
touched daemon code; hot-reload covers persona / chat / policy /
skill / tool changes within the next bridge turn.

### Personas — per-chat agent roles

Persona JSON declares the agent role for a chat or a tenant. Drop
`<corvin_home>/tenants/<tid>/cowork/personas/<name>.json`:

```jsonc
{
  "name":              "support-tier1",
  "description":       "First-line customer support for the support number",
  "permission_mode":   "default",
  "allowed_tools":     ["Read", "Grep", "mcp__crm__search"],
  "disallowed_tools":  ["Bash", "Edit", "Write"],
  "mcp_servers": {
    "crm": {
      "command": "node",
      "args":    ["/srv/mcp/crm-server.js"],
      "env":     {"CRM_API_BASE": "https://crm.internal/v1"}
    }
  },
  "working_dir":     "/srv/support/cases",
  "add_dirs":        ["/srv/support/templates"],
  "default_engine":  "opencode",
  "model":           "ollama-cloud/qwen3-coder:480b",

  "forge_enabled":           false,
  "skill_forge_enabled":     true,
  "inject_skills":           true,
  "outcome_grading":         true,
  "ldd_preset":              "quick",

  "append_system":           "Respond in the customer's language. Cite policy IDs verbatim.",
  "routing_anchors":         ["billing", "refund", "subscription", "cancellation"],
  "voice_persona_style":     "neutral"
}
```

Bind to a chat via `bridges/<channel>/settings.json::chat_profiles`:

```jsonc
{
  "chat_profiles": {
    "+49xxx-support-channel": {
      "persona":   "support-tier1",
      "audience": "all",
      "observer_visibility": "transcript"
    }
  }
}
```

A persona may declare its own `default_engine` and `model` — the
adapter routes to the matching `WorkerEngine` (Claude Code, Codex CLI,
OpenCode, future engines) and degrades capabilities gracefully when
the engine doesn't speak the full Layer-22 contract.

### Bridges — your own channel adapter

Each channel is a directory under `operator/bridges/<name>/` with
a `daemon.js`, a `settings.json`, and (optionally) a per-channel
`README.md`. The daemon needs three responsibilities:

1. Receive inbound messages from the channel SDK.
2. Apply the bridge-edge ACL (whitelist + read-only + consent gate).
3. Write an inbox envelope: `{from, chat_id, text, ts}` (plus
   `_btw` / `_cancel` / `_observer` / `_share` for side-channels)
   into `<corvin_home>/voice/bridges/<channel>/inbox/`.

Outbound is symmetric: poll
`<corvin_home>/voice/bridges/<channel>/outbox/` and ship every
envelope through the channel SDK. Slash-command dispatch comes free
via `shared/js/in_chat_commands.js` — every new channel inherits
`/persona`, `/voice-on`, `/btw`, `/consent`, `/role`, `/audit`,
`/quota`, `/propose` + 40 other commands without one line of dispatch
code.

Production channels today: WhatsApp, Telegram, Discord, Slack, e-mail.
Custom adapters in the field: Microsoft Teams, Mattermost, in-house
HTTP webhook receivers. Mean integration cost: 1–2 days for the
SDK-specific code paths.

### MCP servers — typed tool surfaces

Personas declare their MCP servers in JSON. The adapter materialises
the resolved server set into a temp config file and passes it to the
engine via `--mcp-config`. Drop an MCP server into the persona's
`mcp_servers` block and it shows up as `mcp__<server>__<tool>` in the
agent's tool list — no further wiring.

In-house MCP servers cover patterns Corvin doesn't try to ship:
ticketing-system search, internal CRM, document store, KB lookup,
SAP / Workday / Salesforce / SharePoint, code-search over a private
repo. The MCP protocol is a stable JSON-RPC contract; your MCP server
runs in your network, with your auth, against your data.

### Forge tools — sandboxed Python, registered at runtime

Forged tools are agent-generated, but you can also pre-load curated
tools per tenant. Drop them under
`<corvin_home>/tenants/<tid>/forge/tools/<name>/manifest.json`
together with an `impl.py`. The agent sees them on the next turn as
`mcp__forge__<name>` and runs them under `bwrap` (no network unless
the persona opts in, no subprocess, fresh `/tmp`, read-only `/usr`).

For a regulator who asks "how do you control which tools the agent
runs": every tool is on disk, every run is audited
(`tool.created` + `tool.run` + `tool.network_share`), and every
write to the tool workspace is gated by Layer 10's path-gate hook.

### SkillForge skills — linted knowledge artefacts

Skills are markdown bodies prompt-injected into the agent's next turn
when they have earned at least one positive grade. Drop them under
`<corvin_home>/tenants/<tid>/skill-forge/skills/<name>/SKILL.md`:

```markdown
---
name: company-vacation-policy
description: When asked about vacation, refer to this section verbatim.
type: knowledge
---

Vacation accrual at ACME:
- Full-time: 28 working days / year
- Part-time: pro-rated
- Carry-over cap: 5 days into Q1
- Sabbatical: after 5 years, 4 weeks unpaid
```

The linter (NFKC + confusable folding, prompt-injection patterns,
embedded secrets, persona-boundary phrases) is fail-closed. Skills
that survive a 7-day TTL with no grade get auto-purged; skills graded
≥ 0.5 mean across three grades become eligible for promotion to the
broader scope.

### AWP workflows — declarative DAGs

`workflow.awp.yaml` files declare a multi-step DAG. Each node names
its `engine_id`, its inputs, its R17 output contract, its 5D budget
(time / tokens / cost / steps / depth). The walker reads the YAML,
walks the DAG, validates outputs node-by-node, audits each engine
call as `walker.node_complete`.

Same workflow YAML runs against any AWP-aware consumer. Corvin is
*one* implementation of the standard, not the only one — a regulator
review of the workflow against the YAML schema is independent of
Corvin' implementation.

### Engine adapters — bring your own LLM CLI

The `WorkerEngine` Protocol in `bridges/shared/agents/__init__.py` is
the contract every engine satisfies. Each adapter spawns its LLM CLI
subprocess, parses the streaming output into normalised `StreamEvent`s
(`session_started`, `text_delta`, `tool_call`, `turn_completed`,
`error`), and declares its capabilities (`mid_stream_inject`, `hooks`,
`skills_tool`, `mcp`, `permission_modes`).

In-tree today: `ClaudeCodeEngine`, `CodexCliEngine`, `OpenCodeEngine`, `HermesEngine` (local Ollama, zero egress, L34 CONFIDENTIAL-capable).
Operator-written adapters in the field: AWS Bedrock CLI, vLLM
streaming, Azure OpenAI streaming, internal model gateways. New
adapter ≈ 200 LOC + one capability declaration + a per-subtask E2E
against a fake binary. The capability matrix in the
[README](../README.md#multi-engine--pick-the-cli-that-fits-the-chat)
shows what features each engine gates.

### Packaging the lot — one signed archive per tenant

The `.corvin-pkg` format (Phase 5, described above) is the artefact
companies ship between tenants, between environments, or to a
managed-service customer. A typical `acme-base-v1.0.0.corvin-pkg`
carries:

```
payload/
├── skills/
│   ├── company-vacation-policy/SKILL.md
│   ├── company-expense-policy/SKILL.md
│   └── company-data-handling/SKILL.md
├── personas/
│   ├── support-tier1.json
│   ├── recruiter.json
│   └── auditor.json
└── tools/
    ├── crm.lookup/manifest.json
    ├── crm.lookup/impl.py
    └── hr.holiday_calc/...
```

Signed with the operator's ed25519 key. Installed into a tenant via
`corvin_gateway.cli package install --tenant <tid> --public-key <pem>`.
Audited as `package.installed`. The same archive deploys to
production, staging and on-prem development; the receiving tenant's
own `tenant.corvin.yaml` policy still gates which engines may run.

### Per-tenant deployment pattern — putting it together

A managed-service provider running Corvin for three customers:

1. Provision `tenants/acme/`, `tenants/globex/`, `tenants/initech/`
   via the gateway provisioning CLI (or the migration helper for the
   first tenant on a single-operator host).
2. Each tenant gets its own `tenant.corvin.yaml` — ACME pinned to
   `eu-west`, GLOBEX pinned to `us-east`, INITECH unrestricted.
3. Operator-built `customer-base.corvin-pkg` with shared skills and
   personas is installed into all three tenants; customer-specific
   extensions land via per-tenant `.corvin-pkg` files.
4. Auth: gateway bearer tokens for service-to-service traffic; OIDC
   integration with each customer's IdP for human users; SCIM
   provisioning so users appear in the tenant as soon as IT adds them
   to the IdP group.
5. Each customer's Prometheus + Grafana scrapes their own
   `/v1/tenants/<tid>/metrics` with their own bearer token. Cross-
   tenant visibility is impossible by construction.
6. Audit chains stay per tenant. A regulator reviewing ACME never
   sees GLOBEX activity — there is no shared chain to filter.

---

## How AWP supports the integration story

Corvin adopts the [Agent Workflow Protocol](https://github.com/veegee82/agent-workflow-protocol)
**as a declarative standard, not as a runtime**. The distinction is
load-bearing:

* AWP defines schemas — `workflow.awp.yaml`, agent identity, capability
  declarations, state model, the R17 output contract, the 5D budget
  envelope.
* Corvin reads those schemas via three modules — `awp_dag_parser`,
  `awp_validator`, `awp_walker` — and walks each DAG node through the
  *engine layer* (Claude Code / Codex CLI / Gemini CLI / Ollama / vLLM
  via the `WorkerEngine` protocol). No code path imports `awp.runtime.*`,
  enforced by a CI-linter test.

What this gives you, the integrator:

* **Standards adoption without runtime lock-in.** Workflow YAML you
  write today against Corvin works for any other AWP-aware consumer
  tomorrow. The schema is the contract.
* **Engine portability.** Each DAG node declares `engine_id`. A
  refactor that swaps `claude_code → codex_cli` is a one-line YAML
  edit — no Python touched.
* **Hybrid execution.** A node may set `execution_kind: "http"` for a
  light-weight reasoning step (fast, cheap, OpenAI-compatible direct
  call) or `execution_kind: "engine"` for tool-using steps (Claude Code
  with MCP / Forge / sandbox). The walker honours both.
* **Provider neutrality.** A regulated bank, a public broadcaster and
  a research lab can run the *same* workflow YAML against three
  different engine-policy.json files — Azure OpenAI EU, on-prem vLLM
  and Anthropic API — without forking either schema or runtime.

The technically curious can find the adoption rationale, the standards-only
constraint and the walker design in the Corvin-ADR repository.

---

## EU AI Act 2026 + GDPR posture

The four structural code paths that close the regulatory gap:

### 1. Active disclosure (EU AI Act 2026, Art. 50)

When a person interacts with an AI system, *active disclosure* is
required. Corvin implements this as a **one-time per-uid card**
(layer 19) with three opt-in actions:

* `/join` — register as observer, durable consent (default lowest role)
* `/pass` — acknowledge the disclosure without taking action
* `/leave` — drop the role at any time

The card lists the operator name, the AI nature of the bot, the
optional observer-transcript flag, and the slash-commands that exist.
Every shown card lands as `disclosure.shown` in the audit chain — the
proof that the person was informed on a specific date.

### 2. Consent gate for read-only observers (GDPR Art. 6, 7)

Group-chat participants who are not whitelisted owners cannot have
their text fed to the LLM until they grant consent (`/consent on`,
`/consent 30s`, `/share <text>`). Default is *deny*. Three modes:
durable, time-bounded (TTL clamp 60s–30d), per-message one-shot.
Owner cannot grant on someone else's behalf — identity is platform-bound
(Telegram uid, Discord author id, Slack user, WhatsApp participant).

### 3. Compliance-zone routing (DSGVO Art. 32, EU AI Act Art. 14, 15)

Operator drops `<corvin_home>/global/engine_policy.json`:

```jsonc
{
  "default_engine": "claude_code",
  "fallback_chain": ["claude_code", "vllm_eu_west"],
  "compliance_zones": {
    "personal_data": {
      "allow_engines": ["azure_openai_eu", "vllm_eu_west"],
      "deny_engines":  ["claude_code"]
    },
    "code_only": {
      "allow_engines": ["claude_code", "codex_cli"]
    }
  }
}
```

For every prompt the bridge classifies the compliance zone (PII regex
over six classes — e-mail, +49/+1/+44 phone, IBAN, credit card, US SSN,
CH AHV, DE Steuer-ID — plus persona hints, plus an explicit
`[zone:foo]` user marker). The classification picks an allowed engine
from the policy. If none is healthy, the chain's fallback engines are
walked. Result is recorded as `engine.policy_resolved` with both
`engine_id` and `compliance_zone` in the audit event.

The integrity rule, structurally testable: *every audit event with
`engine_id` has a `compliance_zone` tag that matches an allowed engine
in the active policy*. A regulator can walk the chain offline and ask
"which engine saw what data" with a single grep + verify.

### 4. Tamper-evident audit (BaFin / EBA / KRITIS expectations)

Single append-only JSONL file at `<corvin_home>/global/forge/audit.jsonl`,
each line carrying a `prev_hash` linking back to its predecessor. A
daily systemd timer runs `voice-audit verify`; on break it pushes a
CRITICAL alert through the bridge notification relay (configurable
multi-target — Telegram, Discord, WhatsApp, Slack, e-mail).

What gets audited:

| Category | Example events |
|---|---|
| Bridge | `bridge.whitelist_deny`, `bridge.read_only_drop`, `bridge.observer_appended` |
| Forge / tools | `tool.created`, `tool.run`, `tool.network_share`, `tool.secrets_injected` |
| Skill | `skill.created`, `skill.promoted`, `skill.outcome_graded` |
| Engine routing | `engine.policy_resolved`, `engine.fallback_used` |
| Compliance | `consent.observer_dropped`, `disclosure.shown`, `disclosure.joined` |
| Roles / quota | `grant.issued`, `grant.revoked`, `quota.over_limit` |
| Walker | `walker.node_complete`, `walker.r17_violation`, `walker.budget_exceeded` |
| Audit itself | `audit.chain_gap_detected` (out-of-band on chain break) |

The chain never auto-truncates. Operator rotates on their cadence
(logrotate-friendly — `cp + truncate` keeps the chain valid because
`prev_hash` of the first line in the new file is the snapshot's last
hash; verify before rotation).

---

## Three concrete integration patterns

### Pattern A — Engineering org, Slack-first

A 30-developer team wants Claude Code reachable from Slack with audit.

1. Install Corvin on a hardened Linux VM. `bash setup.sh`.
2. Wire Slack bridge (`bridges/slack/settings.json`: bot tokens,
   whitelist of internal user IDs, rate limit per hour).
3. Pin `coder` persona for the engineering channels via
   `chat_profiles[<chan>].persona = "coder"`.
4. Forge a few internal tools — `code.lint`, `code.bisect_blame`,
   `code.run_tests` — under the `coder` persona's namespace.
5. Audit chain logs every tool run, every Bash command tried, every
   path-gate denial.

Time-to-value: ~3 days from clean VM. Compliance: SOC 2 Type II
control mapping ships in the Compliance Pack tier.

### Pattern B — Customer-support ops, WhatsApp-first

A B2C company wants tier-1 support over WhatsApp with a CRM in the
loop and PII-aware routing.

1. WhatsApp bridge + `inbox` persona for the support number.
2. `mcp__crm__search` MCP server custom-built (or off-the-shelf via
   the existing CRM vendor) plumbed into the persona JSON.
3. `engine_policy.json` routes everything classified as `personal_data`
   to the EU-resident model; everything else to the default.
4. Per-user quota caps the daily message budget (Layer 20).
5. Audit chain proves to the DPO that no PII left the EU during the
   review period.

### Pattern C — Knowledge org with declarative workflows

A consultancy wants reproducible client-deliverable workflows.

1. Each workflow as a versioned `workflow.awp.yaml` in their git repo.
2. Operators trigger workflows via a custom slash-command on Slack:
   `/run-workflow weekly-report.awp.yaml`.
3. Corvin' walker reads the YAML, walks the DAG node-by-node,
   validates each output against R17, audit-tags each engine call.
4. Workflow library is portable — same YAMLs run for any other AWP
   consumer.

Storage of the resulting deliverables on the firm's existing Drive /
SharePoint via a custom MCP server.

---

## Onboarding journey — PoC → pilot → production

| Week | Phase | Exit criteria |
|---|---|---|
| 0–2 | **PoC** — one channel, one persona, five whitelisted users | Daily use for two weeks; audit chain verifies clean |
| 2–6 | **Pilot** — second channel, internal MCP servers, 2-3 org-specific skills, consent gate enabled | 20+ daily users; alerting tested via deliberate break-and-restore |
| 6–12 | **Production** — roles + quotas, daily verify timer, offsite audit-chain backup, EU-AI-Act dossier | Org-wide rollout; full audit week without unexpected events |

Ongoing: weekly review of `quota.over_limit` events, monthly skill-grade
review with promotion / demotion, quarterly drift-detection sweep,
quarterly engine version audit.

---

## Why companies pick Corvin over wiring it themselves

* **Hexagonal architecture is enforced, not promised.** Engine layer is
  the only LLM execution path; AWP is consumed as a schema, not a
  runtime; CI-linter prevents bypass.
* **No vendor lock-in at the orchestration layer.** Workflow YAML works
  for any AWP-aware platform. Engine swap is a one-line edit.
* **EU AI Act 2026 ready out of the box.** Disclosure card, consent
  gate, audit chain, compliance zones — code paths that cannot be
  bypassed without an audit event.
* **Audit-chain hash linking is offline-verifiable.** Single file, no
  database, USB-stick portable. Carries through `cp + truncate`
  rotations because `prev_hash` is content-addressed.
* **Self-hosted by default.** No phone-home telemetry, no third party
  in the data path beyond the LLM provider you choose. Operator
  controls the secret vault, the audit chain location, the policy
  file.
* **Forge sandbox is structural.** Path-gate hook blocks direct
  Write/Edit/Bash to forge / skill-forge / audit / policy paths
  regardless of permission mode. The MCP server is the only writable
  path. Tested against the heredoc / `eval` / `$(...)` escape vectors.

---

## What Corvin deliberately is NOT

To avoid disappointment downstream:

* **Not an LLM provider.** Bring your own — Anthropic, OpenAI, Azure,
  AWS Bedrock, Google Vertex, Ollama on-prem, vLLM cluster.
* **Not a multi-agent orchestrator at runtime.** AWP is the spec for
  declarative DAGs; the walker executes them sequentially with
  optional fan-out. Map-reduce-style parallel research over 50
  documents is a different tool's job.
* **Not a memory store.** Three-tier flat-file convenience layer in
  `~/.config/corvin-voice/`. No DB, no vector index. If you need
  vector search, plug it via MCP.
* **Not a sandbox of the agent itself.** Forge sandboxes *forged tools*
  at exec; the bridge agent runs with whatever permissions Claude
  Code / Codex / etc. were started under.
* **Not a billing / usage tracker.** Audit logs *what happened*, not
  *what it cost*. Provider dashboards remain authoritative.

---

## Get started

```bash
git clone https://github.com/veegee82/Corvin.git
cd Corvin
bash setup.sh
```

Then:

* `docs/overview.md` — the conceptual introduction
* `docs/layer-model.md` — the layer-by-layer reference
* `docs/security.md` — the threat model
* `docs/forge.md` — runtime tool generation in depth
* `Corvin-ADR: decisions/` — every architectural decision with its tradeoffs

For commercial inquiries, pilot proposals or partnership questions,
follow the maintainer contact in [README.md](../README.md).
