# Layer 35 — Network Egress Lockdown + EU_PRODUCTION preset (ref doc)

Companion to the short CLAUDE.md section. Full operational details
live here.

→ **ADR:** Corvin-ADR: decisions/0043-L35-egress-lockdown.md
→ **ADR-0167 M1:** Corvin-ADR: decisions/0167-entangled-license-ratchet.md (ELR integration)
→ **Modules:** 
  - `operator/bridges/shared/egress_gate.py` (policy enforcer + ratchet integration)
  - `operator/license/elr.py` (Entangled License Ratchet core, M1)
→ **Tests:** 
  - `operator/bridges/shared/test_egress_gate.py`
  - `operator/license/tests/test_elr_m1.py` (34 comprehensive ELR tests)
→ **Presets:** `operator/bundle/config-templates/tenant.corvin.eu-production-{ollama,http}.yaml`

---

## What this layer does

Refuses to spawn an engine whose target host is not on the tenant's
egress allowlist. **Structural** defence; the perimeter firewall is
the *operational* defence — both are required for the EU claim to
hold.

| Defence | Layer | Mechanism |
|---|---|---|
| Engine identity | ADR-0007 | `data_residency.allowed_engines` |
| Sensitivity grading | L34 | `data_classification.matrix` × locality |
| **Network egress** | **L35** | **`egress.allowed_hosts` × `forbidden_hosts`** |
| Perimeter firewall | operator | iptables / docker / cloud SG |

A determined attacker would need to weaken **all three** of L34, L35,
and the tenant's `allowed_engines` simultaneously — and the
perimeter still stands. That's the three-layer property.

---

## Core API

```python
from egress_gate import (
    EgressPolicy,        # frozen dataclass: enabled / default_action / hosts
    EgressGate,          # construct once per tenant
    EgressDecision,      # validate() return
    EgressDenied,        # raised by validate_or_raise()
    canonicalise_host,   # lowercase + strip + shape-check
    make_forge_audit_writer,
)
```

### Construction

```python
import yaml
from pathlib import Path
from egress_gate import EgressGate, make_forge_audit_writer

tenant_cfg = yaml.safe_load(Path(tenant_yaml_path).read_text())
audit_path = Path(corvin_home) / "tenants" / tid / "global" / "forge" / "audit.jsonl"
gate = EgressGate.from_tenant_config(
    tenant_cfg,
    audit_writer=make_forge_audit_writer(audit_path),
)
```

Alternative for standalone deployments:

```python
gate = EgressGate.from_file(
    "/path/to/egress_policy.json",
    audit_writer=make_forge_audit_writer(audit_path),
)
# Returns None when the file is missing (pre-L35 deployment).
```

### Validation

```python
decision = gate.validate(
    "api.anthropic.com",
    engine_id="claude_code",
    persona="coder",
    channel="discord",
    chat_key="dm:42",
)
if not decision.allowed:
    raise RuntimeError(f"egress denied: {decision.reason}")
```

Strict variant:

```python
try:
    gate.validate_or_raise(host, engine_id=eid, persona=p, channel=c, chat_key=k)
except EgressDenied as e:
    return f"refused: {e.decision.host} ({e.decision.matched_rule})"
```

### Preset consistency

The boot self-test calls this once per tenant:

```python
warnings = gate.validate_preset_consistency(
    expected_engines=tenant_cfg["spec"]["data_residency"]["allowed_engines"],
    engine_compliance=data_flow_guard.engine_compliance,  # from L34
)
for w in warnings:
    log.warning(w)
```

---

## Tenant configuration

```yaml
spec:
  egress:
    enabled: true              # default false (back-compat)
    default_action: deny       # allow | deny
    allowed_hosts:
      - localhost
      - 127.0.0.1
      - ollama.lan
    forbidden_hosts:
      - api.anthropic.com
      - api.openai.com
      - api.mistral.ai
```

Loader rules:

* `default_action` must be `allow` or `deny`; anything else raises.
* `allowed_hosts` and `forbidden_hosts` must be lists of strings.
* Every host is canonicalised (lowercase + strip).
* A host that appears in **both** lists raises `ValueError` (operator
  confusion).
* A host that fails the hostname regex raises `ValueError`.
* Missing `spec.egress` block → disabled-default gate (back-compat).

---

## EU_PRODUCTION presets

Two shipped templates, both under
`operator/bundle/config-templates/`:

### `tenant.corvin.eu-production-ollama.yaml` (recommended default)

* `allowed_engines: [opencode_ollama]`
* `forbid_engines: [claude_code, codex_cli, opencode]`
* `data_classification.matrix.*: [local]` (every classification row)
* `egress.{enabled: true, default_action: deny, allowed_hosts:
  [localhost, 127.0.0.1]}`
* `egress.forbidden_hosts: [api.anthropic.com, api.openai.com,
  api.mistral.ai, generativelanguage.googleapis.com]`
* Forward-declared `spec.audit.{retention_years: 7,
  encryption_at_rest, rotation}` for L37 (M3).

### `tenant.corvin.eu-production-http.yaml` (self-hosted)

Same as above, but:

* `allowed_engines: [opencode_http]`
* `forbid_engines` also includes `opencode_ollama`.
* `egress.allowed_hosts` adds `opencode-llm` (the docker-compose
  service name).

Operator picks one and installs it as
`<corvin_home>/tenants/_default/global/tenant.corvin.yaml`. Hot-reload
applies on mtime change.

---

## Audit contract

Three event types on the L16 hash chain:

```jsonc
// egress.approved (INFO)
{
  "event_type": "egress.approved",
  "severity": "INFO",
  "details": {
    "host": "localhost",
    "engine_id": "opencode_ollama",
    "matched_rule": "allowed_explicit",
    "reason": "host on allowed_hosts list",
    "persona": "coder",
    "channel": "discord",
    "chat_key": "dm:42"
  }
}

// egress.blocked (CRITICAL)
{
  "event_type": "egress.blocked",
  "severity": "CRITICAL",
  "details": {
    "host": "api.anthropic.com",
    "engine_id": "claude_code",
    "matched_rule": "forbidden_explicit",
    "reason": "host on forbidden_hosts list",
    "persona": "coder",
    "channel": "discord",
    "chat_key": "dm:42"
  }
}

// egress.preset_loaded (INFO) — emitted by boot self-test
{
  "event_type": "egress.preset_loaded",
  "severity": "INFO",
  "details": {
    "matched_rule": "preset_consistent",
    "reason": "EU_PRODUCTION preset loaded with 0 warnings"
  }
}
```

Audit allow-list keys: `host`, `engine_id`, `persona`, `channel`,
`chat_key`, `reason`, `matched_rule`. URL paths, request bodies,
response content are NEVER permitted in details — module enforces at
emission time; regression test asserts the set.

---

## Decision precedence

```
0. forbidden_hosts (explicit deny)   ← ALWAYS wins, even over a ratchet allow
1. ratchet-derived policy (ADR-0167) ← when a ratchet + descriptor are present
2. allowed_hosts   (explicit allow)  ← static-policy fallback
3. default_action  (allow | deny)    ← static-policy fallback
```

`forbidden_hosts` is evaluated **before** the ADR-0167 ratchet so an
issuer-signed (or misconfigured) capability descriptor can never re-permit a
statically-forbidden host — the operator's explicit deny is a hard deny. When a
ratchet is configured but a descriptor fails to unwrap, evaluation falls back to
the static `allowed_hosts` / `default_action` rules (steps 2–3).

Disabled policy (the back-compat default) is a pass-through with
`matched_rule="egress_disabled"` and no audit emission.

Malformed host strings (passed as the validation argument, not in
the policy) are denied (fail closed) and audit-logged.

---

## Wiring status

* **M2 (done):** Module + tests + two presets +
  EVENT_SEVERITY + ADR + ref doc. Standalone; opt-in.
* **M2.5 (done):** Adapter `_compliance_gate()` wiring alongside
  L34 guard. Same construct-once-per-tenant pattern.
* **M2.6 (done):** `bridge.sh doctor` (via `self_test.py`) adds the
  `egress.preset_loaded` / `egress.preset_consistency` check group via
  `validate_preset_consistency()`.

The standalone shape mirrors L34 deliberately — both ship in
isolation, both wire into the same adapter compliance-gate point.

---

## Must NOT do

* Don't enable `default_action=deny` without listing legitimate hosts.
* Don't add `api.anthropic.com` / `api.openai.com` to `allowed_hosts`
  in any EU_PRODUCTION preset.
* Don't put URL paths, query strings, or request/response content in
  audit `details`.
* Don't make L35 the *only* network defence — perimeter firewall
  rules are operator responsibility.
* Don't make `egress.blocked` advisory; fail-closed contract.
* Don't `import anthropic` from `egress_gate.py` (CI lint).
* Don't add a Python `socket` monkeypatch — fragile, by-passable by
  any subprocess; would create false security claim.

---

## Tests

```bash
python3 operator/bridges/shared/test_egress_gate.py
```

33 tests covering:

* `canonicalise_host` (lowercase, strip, IPv4, reject empty / non-string / invalid chars).
* Disabled policy passthrough (no audit emission).
* Enabled policy with forbid precedence.
* Enabled policy with default_action=deny (EU_PRODUCTION stance).
* Malformed host fail-closed at validate time.
* `validate_or_raise` exception shape.
* Audit allow-list enforcement (smuggled keys rejected).
* Tenant config loader (empty / full / bad default_action / overlap / malformed).
* `from_file` (missing file / flat shape / wrapped shape / malformed JSON).
* Preset consistency warnings (disabled-with-forbidden, deny-all, external-egress engine).
* CI lint (no `import anthropic`).

---

## ADR-0167 — Entangled License Ratchet (ELR) Integration

On paid licenses, entangled capabilities (egress-preset, A2A/SesT, MCP, CLS) 
are derived from an offline ratchet: non-precomputable, forward-only, advanced 
by the L16 audit chain head. With optional networked entropy (M3), true 
forward-secrecy for distributed tier.

**Status (2026-06-27): M1–M3 PRODUCTION-READY** (64 tests green)
- **M1 (done):** Ratchet core, AEAD descriptors, fail-closed fallback
- **M2 (done):** 8 entangled capabilities (egress/A2A/MCP/CLS), unwrap sites, policy application
- **M3 (done):** Networked external-entropy layer (ADR-0103), offline-fallback E2E
- **M4 (roadmap):** Red-team hardening (cost analysis, CLAG detection, decay proof)

### Core components (M1)

**`operator/license/elr.py`:**
- `EntangledRatchet` — forward-only state machine, tile derivation, commitments
- `WrappedCapabilityDescriptor` — wire format (nonce || ciphertext)
- `CapabilityEnvelope` — ChaCha20-Poly1305 wrap/unwrap (fail-closed)
- `make_root_from_license_token()` — HKDF-Extract with domain separation

**`operator/bridges/shared/egress_gate.py` (integration stub):**
- `EgressGate(ratchet=..., capability_label=...)` — optional ratchet binding
- `_try_ratchet_policy_check()` — returns None in M1 (M2 will unwrap descriptor)
- Fallback to static policy when ratchet unavailable or fails (fail-closed)

**New audit events:**
- `egress.ratchet_decision` (INFO) — ratchet attempted, result logged
- `egress.ratchet_committed` (INFO) — commitment hash to chain
- `egress.policy_disabled` (WARNING) — explicit policy disable (per G-013 / ADR-0073)

### Threat model

**Raised to costly:** casual patching is worthless (no boolean to NOP);
one-time key extraction expires (forward ratchet); attacker must 
reimplement derivation and hold key material; tampering visible in audit.

**Residual ceiling (unchanged):** full in-process control allows extraction 
of root and ratchet re-implementation. ELR does not claim impossibility 
(mirrors ADR-0139 boundary).

### Testing

`operator/license/tests/test_elr_m1.py` — **34 tests, 100% green:**
- Ratchet core (init, advance, state immutability, cache coherence)
- AEAD (wire format, roundtrip, encryption/decryption, corruption detection)
- Root derivation (deterministic, domain-separated, different tokens)
- Commitments (tile hashing, all_commitments dict format)
- Full integration scenario (init → derive → wrap → commit → advance → unwrap)
- Fail-closed defense (no key material in audit, tampering detection)
- Input validation (advance() rejects short/None/non-bytes)

### Milestones — All Production-Ready (M1–M3)

**M1 (✅ done):** Ratchet core, AEAD, L16 integration setup, fail-closed fallback.
- 34 tests green (M1 baseline: ratchet mechanics, AEAD wrap/unwrap, root derivation, commitments, fail-closed defense)

**M2 (✅ done):** 8 entangled capabilities, descriptor storage, unwrap sites, per-cap E2E.
- egress-paid-preset (L35), a2a-sestoken (ADR-0103), mcp-auth-* (3 services), cls-tier-*-key (2 tiers)
- CapabilityRegistry (tenant config loader), per-capability unwrap + fallback, 20 tests green
- Functional egress gate integration + descriptor wrapping

**M3 (✅ done):** Networked external-entropy layer, offline-fallback E2E.
- Optional entropy parameter to advance(); offline ratchet always works (fail-closed)
- True forward-secrecy when ADR-0103 membership entropy available
- Graceful degrade: network down → offline ratchet, no crash
- 10 tests green (offline, networked, fallback, intermittent, integration)

**M4 (📋 roadmap):** Red-team hardening (post-release).
- Root-extraction cost analysis, epoch-freeze detection via CLAG, decay proof
- 8-scenario test matrix, anticipated findings, TTL matrix
- Reference: `docs/claude-ref/adr-0167-m4-redteam.md`
