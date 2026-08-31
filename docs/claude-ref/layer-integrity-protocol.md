# Layer Integrity Protocol (ADR-0141)

The Layer Integrity Protocol (LIP) makes the presence of CorvinOS's mandatory
security layers cryptographically verifiable at the network boundary. It closes
the residual gap from ADR-0140: a fork that strips security layers but presents a
valid A2A credential.

> You cannot prevent a fork from running modified code. You can make it
> impossible for a tampered fork to participate in the legitimate Corvin
> network, receive delegated tasks, or be trusted by peers.

LIP is **network-membership enforcement**, NOT a commercial feature gate — single-node
Apache-core operation is never gated on it.

## Four tiers

| Tier | Mechanism | Module | Severity |
|---|---|---|---|
| **3** | `SecurityCapabilityRegistry` — each layer self-registers at import; spawn-gate asserts presence | `security_capabilities.py` | CRITICAL (spawn blocked / boot) |
| **1** | Signed `layer-manifest.json` pins each layer's SHA-256; boot check compares | `layer_integrity.py`, `self_test._check_layer_integrity` | CRITICAL (present+tampered) / WARNING (absent) |
| **2** | A2A `network_attestation` carries `layer_integrity_hash`; receiver verifies vs manifest | `remote_trigger_sender.py`, `remote_trigger_receiver.py` | reject envelope (WARNING audit) |
| **4** | `/v1/a2a/audit-head` exposes chain head; peers flag non-advancing chains | `a2a_audit_head.py`, `a2a_http_server.py` | advisory WARNING |

Deployment order (independently shippable): Tier 3 → Tier 1 → Tier 2 → Tier 4.

## Tier 3 — Capability registry

`security_capabilities.py` holds a process-local `_REGISTRY`. Canonical names
(`MANDATORY_CAPABILITIES`) and their versions (`CAP_VERSIONS`) are the single
source of truth shared with Tier 1.

- Each in-process layer (`audit`, `consent`, `data_classification`,
  `egress_gate`, `erasure_orchestrator`, `self_test`, `remote_trigger_receiver`)
  calls `register_capability(...)` at module import.
- `path_gate` is an out-of-process hook, so it is registered by **file
  presence** in `bootstrap_core_capabilities()` (parses `CAPABILITY_VERSION`
  without importing the hook).
- `bootstrap_core_capabilities()` runs at adapter boot; it registers
  deterministically from `CAP_VERSIONS` (idempotent, survives a registry reset —
  does NOT rely on import side-effects).
- `assert_capabilities_present()` runs before **every** engine spawn:
  - claude path → after the CLAG gate in `_call_claude_streaming_via_engine`
  - codex/opencode/hermes → first gate in `_run_pre_dispatch_gates`
  - Fail-closed: a missing capability OR an unimportable registry blocks the spawn
    and emits `security.capability_missing` (CRITICAL).

## Tier 1 — Signed manifest + boot check

`operator/security/layer-manifest.json` (cryptographically signed; private signing
key held offline at Corvin Labs).

`layer_integrity.py`:
- `compute_layer_hashes()` → `{name: sha256:...}` for every mandatory file.
- `aggregate_hash()` → single `sha256:` over canonical JSON of the sorted map.
- `compute_layer_integrity_hash()` (local files, sender/self) and
  `manifest_layer_integrity_hash(manifest)` (receiver expected) — equal for an
  honest node.
- `verify_manifest_signature()` verifies the signed manifest against the embedded
  trust anchor.
- `verify_integrity()` → `IntegrityResult` with status VERIFIED /
  MANIFEST_ABSENT / MANIFEST_INVALID / MISMATCH.

**Rollout severity (load-bearing).** A valid manifest cannot exist until a
release ships one. To avoid bricking pre-manifest installs while still detecting
tampering:

| State | Severity |
|---|---|
| manifest absent | WARNING (pre-rollout) |
| manifest present, bad signature | CRITICAL |
| manifest valid, layer hash mismatch | CRITICAL |
| all match | INFO |

A *present* manifest is fully fail-closed; only the not-yet-shipped state is
advisory. Signing tool: `operator/security/sign_layer_manifest.py`
(`--key`, `--mandatory-after`, `--verify`).

## Tier 2 — A2A attestation (Protocol v7 marker)

Sender `_build_network_attestation()` folds `layer_integrity_hash` (cached per
boot, mtime-invalidated) + `protocol_version: 7` into the `network_attestation`
block — which is already HMAC-covered by `_build_envelope`, so the hash cannot
be altered in transit.

Receiver step 6.85 (`_check_layer_integrity_attestation`):
- Receiver has no valid signed manifest → cannot enforce → grace (allow).
- Hash present → must equal the receiver's `manifest_layer_integrity_hash`;
  mismatch → `a2a.layer_integrity_mismatch` + reject.
- Hash absent → required iff origin's `require_layer_integrity` OR manifest's
  `mandatory_after` deadline passed; else grace.

**Accepted residual (ADR-0141):** an open-source fork can hard-code the public
manifest aggregate while running modified code. The HMAC binds the value in
transit; it does not prove the sender ran the pinned code. Tier 1 (sender boot
check) + L10/bwrap/LSAD cover the local threat. Note: the envelope already uses
"Protocol v7/v8" for `sender_chain_tail`/`sender_genesis_hash` (ADR-0116/0117);
the `protocol_version` here is a marker *inside* the attestation block.

## Tier 4 — Audit chain transparency (advisory)

`GET /v1/a2a/audit-head` → `{chain_head, event_count, latest_ts, instance_id,
signature}`. `?origin_id=<id>` selects the recv_key for an HMAC signature the
requester can verify. `a2a_audit_head.check_peer_audit_head()` flags a peer
whose chain has not advanced across ≥2 consecutive observations
(`a2a.peer_audit_anomaly`, WARNING). Never hard-blocks.

## Audit events (metadata only)

`security.capability_missing` (CRITICAL), `layer_integrity.verified` (INFO),
`layer_integrity.manifest_invalid` (CRITICAL), `layer_integrity.manifest_absent`
(WARNING), `layer_integrity.mismatch` (CRITICAL), `a2a.layer_integrity_mismatch`
(WARNING), `a2a.peer_audit_anomaly` (WARNING). Allow-lists registered in
`security_events._EVENT_ALLOWLIST`. NEVER file paths, file bytes, or mtimes.

## Must NOT do

- Skip manifest signature verification / fall back to a local-only hash.
- Put file paths, file content, or mtimes in audit details.
- Make Tier 4 `peer_audit_anomaly` CRITICAL (advisory by design).
- Cache `layer_integrity_hash` across restarts without mtime re-check.
- Gate Apache-core single-node features on LIP.
- `import anthropic` from any LIP module (CI AST lint).

## Tests

`test_security_capabilities.py` (Tier 3), `test_layer_integrity.py` (Tier 1),
`test_layer_integrity_a2a.py` (Tier 2), `test_a2a_audit_head.py` (Tier 4) — all
registered in `run-all-tests.sh`.
