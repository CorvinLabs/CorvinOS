# Layer 38 — A2A Network Membership Attestation (ADR-0103)

This document covers the A2A network membership attestation system layered on top of
the core RemoteTriggerReceiver/Sender protocol (ADR-0048).  Read
[`layer-38-a2a-flow.md`](layer-38-a2a-flow.md) for the base protocol reference.

---

## Overview

CorvinOS is Apache-2.0 open source. Without additional protection, a fork that
bypasses the local license check could build protocol-conformant A2A envelopes once
paired. ADR-0103 closes this gap by making the **pairing step** the enforcement
boundary: only instances holding a valid Corvin Labs Session Token (SesT) can join
the network.

### Trust anchor

A cryptographic trust anchor signs all A2A attestation envelopes. All four layers
below derive their security from this single root of trust.

---

## Layer 1 — Pairing Gate (M1)

**Trigger:** `corvin-a2a pair <peer> <peer-url>`

**Flow:**

```
Local instance                    features.corvinlabs.io
     │                                     │
     │── POST /v1/pair/authorize ──────────▶│
     │   { instance_id, sest_fp,           │
     │     peer_url }                      │
     │                                     │── verify SesT
     │                                     │── check revocation
     │◀── 200 { pairing_id, pairing_cert } │
     │         OR 403 { reason }           │
```

`sest_fp = SHA-256(header_b64url + "." + payload_b64url)` — the fingerprint of the
JWT header+payload, without its signature.

On success the **PairingCertificate** (30-day JWT) and `pairing_id` are written into
the origin JSON file alongside the HMAC keys.

**Fail-closed:** network errors or a 403 from the gate abort the pairing. Use
`--offline-pair` for isolated / air-gapped networks that do not connect to
Corvin Labs.

```bash
# Standard (requires network + valid SesT):
corvin-a2a pair mypeer https://remote.host:8000/v1/a2a/receive

# Isolated network:
corvin-a2a pair --offline-pair mypeer https://remote.host:8000/v1/a2a/receive
```

---

## Layer 2 — Per-Envelope Attestation (M2)

Every outbound `TaskEnvelope` (Protocol v6) carries a `network_attestation` block:

```json
{
  "task_id": "…",
  "origin_id": "…",
  "sender_instance_id": "…",
  "issued_at": 1749462000,
  "instruction": "…",
  "attachments": [],
  "network_attestation": {
    "sest_fp":     "<hex SHA-256 of JWT header.payload>",
    "sest_sig":    "<base64url signature from the JWT>",
    "pairing_id":  "<uuid from PairingCertificate>",
    "attested_at": 1749462000
  },
  "signature": "<HMAC-SHA256 over all fields including network_attestation>"
}
```

The `network_attestation` block is included in the HMAC payload, making it
tamper-evident. Replacing or stripping it invalidates the HMAC.

### Receiver validation (Step 6.8 in `_validate()`)

After HMAC verification succeeds:

| Check | Failure code |
|---|---|
| `attested_at` within ±300 s | `network_attestation_time_window` |
| Signature verify: `sest_sig` over `sest_fp` | `network_attestation_bad_sig` |
| `pairing_id` matches stored origin `pairing_id` | `network_attestation_pairing_mismatch` |
| `sest_fp` not on manifest revocation list | `network_attestation_revoked` |
| `pairing_id` not on manifest revocation list | `network_attestation_pairing_revoked` |

If `network_attestation` is absent: check `attestation_mandatory_after` from the
manifest. Before that timestamp (grace period), the envelope is accepted with a
WARNING. After it, the envelope is rejected with `network_attestation_required`.

**Disable for tests:**

```bash
CORVIN_A2A_ATTESTATION_DISABLED=1 pytest ...
```

---

## Layer 3 — Protocol Manifest (M3)

On every adapter restart, CorvinOS fetches a signed manifest:

```
GET https://corvinlabs.io/a2a/manifest.json
Mirror: https://github.com/CorvinLabs/CorvinOS/releases/latest/download/a2a-manifest.json
Cache: <corvin_home>/global/a2a_manifest.json  (mode 0600)
```

### Manifest schema

```json
{
  "schema_version": 1,
  "issued_at": 1749462000,
  "min_protocol_version": "3.0",
  "current_protocol_version": "6.0",
  "revoked_instance_ids": ["<uuid>"],
  "revoked_sest_fps":     ["<hex fp>"],
  "revoked_pairing_ids":  ["<uuid>"],
  "attestation_mandatory_after": 1752054000,
  "signature": "<signature over canonical JSON without this field>"
}
```

The manifest is cryptographically signed over the canonical JSON (all fields except
`"signature"`, sorted keys, no whitespace). The receiver verifies it against the
embedded trust anchor.

### Staleness policy

| Manifest age | Behaviour |
|---|---|
| < 3 days | Normal |
| 3–7 days | `a2a.manifest_stale` WARNING to audit chain |
| > 7 days | Treated as absent; revocation list cleared (fail-open) |

Operators can set `a2a_manifest_required: true` in `tenant.corvin.yaml` to make
a stale / absent manifest fail-closed (A2A reception disabled until refresh succeeds).

### Python API

```python
from a2a_manifest import load_manifest, clear_cached

manifest = load_manifest()
# manifest.revoked_sest_fps: set[str]
# manifest.revoked_instance_ids: set[str]
# manifest.attestation_mandatory_after: float  (unix timestamp)
# manifest.is_stale: bool
# manifest.sig_verified: bool

# Force re-fetch:
clear_cached()
manifest = load_manifest(force_refresh=True)
```

---

## Layer 4 — Self-Test (M4)

`operator/bridges/shared/self_test.py` runs `_check_a2a_network_membership()` as
part of `run_self_test()`.

| Check name | Severity | Condition |
|---|---|---|
| `a2a.network_pubkey` | CRITICAL | `a2a_network_pubkey.pem` missing or malformed |
| `a2a.network_pubkey` | WARNING | `cryptography` package not installed |
| `a2a.manifest_age` | WARNING | Cached manifest ≥ 3 days old |
| `a2a.sest_not_revoked` | CRITICAL | Local SesT fingerprint on revocation list |

---

## Audit events (ADR-0103)

Registered in `operator/forge/forge/security_events.py`:

| Event | Severity | When |
|---|---|---|
| `a2a.pairing_authorized` | INFO | M1 gate returns a valid PairingCertificate |
| `a2a.pairing_denied` | WARNING | M1 gate returns 403 or is unreachable |
| `a2a.manifest_fetched` | INFO | Fresh manifest successfully fetched + verified |
| `a2a.manifest_stale` | WARNING | Cached manifest ≥ 3 days old, or no manifest available |
| `a2a.attestation_failed` | WARNING | Any M2 validation failure |

**Audit allow-list** — never include in `details`:
- SesT bytes or full JWT
- Instruction or result payload
- Full `sest_fp` (use first 16 hex chars: `sest_fp_prefix`)
- Pairing cert body

**Allowed in `details`:** `instance_id`, `sest_fp_prefix`, `pairing_id`,
`origin_id`, `endpoint_id`, `reason`, `grace_days_remaining`, `manifest_age_days`.

---

## Key files

| File | Role |
|---|---|
| `operator/security/a2a_network_pubkey.pem` | Embedded trust anchor public key |
| `operator/bridges/shared/a2a_manifest.py` | M3 manifest fetch / cache / expose |
| `operator/voice/scripts/corvin_a2a.py` | M1 pairing gate (`_authorize_pairing_m1`) |
| `operator/bridges/shared/remote_trigger_sender.py` | M2 build `network_attestation` |
| `operator/bridges/shared/remote_trigger_receiver.py` | M2 validate `network_attestation` |
| `operator/bridges/shared/self_test.py` | M4 CRITICAL checks |
| `operator/forge/forge/security_events.py` | New A2A audit event types |

---

## Licence quota enforcement — pairing routes

All console pairing paths enforce `a2a_peers_max` (ADR-0094) before writing
any origin/endpoint config files. Exceeding the limit returns HTTP 402.

| Route | Quota check added |
|---|---|
| `POST /remote-trigger/pair/redeem` | Yes (original ADR-0094 implementation) |
| `POST /remote-trigger/pair/accept` | Yes — issuer side (review fix 2026-06-17) |
| `POST /remote-trigger/pair/cli-accept` | Yes (review fix 2026-06-17) |
| `POST /remote-trigger/pair/friendship/import` | Yes (review fix 2026-06-17) |

The shared helper `_check_a2a_peers_max()` (in `a2a_pair.py`) counts existing
`*.json` files in the origins directory and raises HTTP 402 when the count
meets or exceeds the licence limit.

---

## Per-connection rights + connection names (2026-07-20)

Pairing is bidirectional (each side stores an inbound *origin* and an outbound
*endpoint*), and **each side owns its own inbound policy**: what a peer may do
here is decided exclusively by the local origin file — the peer has no say in
it, and every field can be changed retroactively from the console (Agent Hub →
Peers → Edit connection) via `PATCH /v1/console/remote-trigger/origins/{id}`.

Editable per-connection fields (`OriginPatchRequest`, `a2a_pair.py`):

| Field | Meaning |
|---|---|
| `enabled` | connection on/off |
| `spawn_worker` | Observer (validate-only) vs Executor (M2 worker runs the instruction) |
| `allowed_personas` | persona allow-list; `[0]` is the active persona |
| `max_ttl_s` | cap on envelope TTL (10–86400 s) |
| `label` | human-readable connection name (≤80 chars, control chars stripped) |
| `allow_bash` / `allow_network` / `allow_read_files` / `allow_write_files` / `allow_subagents` | M2 tool policy opt-ins — **deny-by-default** (ADR-0144); enforced in `remote_trigger_receiver._spawn_and_filter()` |

The tool policy is compiled into a `--disallowedTools` denylist (built-ins:
Bash · WebFetch/WebSearch · Read/Grep/Glob/LS/NotebookRead · Write/Edit/… ·
Task/Todo*). The A2A worker also spawns with `--strict-mcp-config` (no
`--mcp-config`), so it loads **zero** MCP servers — the operator's user-scoped
MCP tools (`~/.claude.json`) can't be used to sidestep `allow_network=false` /
`allow_read_files=false`. Persona-scope narrowing beyond the denylist is
advisory (prompt text), not a security boundary.

**Honest limits of the checkbox model (2026-07-20):**

- `allow_subagents=true` unblocks the Task tool, and the engine does **not**
  contractually guarantee that the other per-connection denies bind inside
  subagent workers: claude-CLI subagents inherit the parent session's
  permission context, but the bare-name `--disallowedTools` form used here is
  a context-removal mechanism whose propagation into Task subagents is only
  inferred from documentation, and non-claude engines guarantee nothing.
  Because of that gap, `_spawn_and_filter()` **force-restricts** (A5, 2026-07-20):
  if `allow_subagents=true` is combined with **any** of `allow_bash` /
  `allow_network` / `allow_write_files` **denied**, the subagent grant is
  ignored — Task/Todo* stay on the denylist and the downgrade is audited as
  `A2A.subagents_force_restricted` (WARN). So a dangerous capability that is
  switched off can no longer be re-reached through a Task subagent. Only when
  every dangerous capability is granted does `allow_subagents=true` actually
  enable Task — where, by definition, there is no stricter deny left to leak.
- `allow_bash=true` factually includes network egress (`curl`/`wget` run in
  the shell). The Network checkbox only gates the built-in WebFetch/WebSearch
  tools — checkbox independence between Shell and Network is a fiction.

`GET /remote-trigger/origins` returns the same fields (never keys); stored
labels are re-sanitized read-side on **every** delivery surface (console
origin/endpoint listings, `GET /pair/friendship/connections`, the `PATCH`
origin/endpoint responses when the body omits `label`, MCP
`a2a_list_endpoints`, `peek_label()`) so pre-sanitizer records cannot carry
ANSI/bidi content (e.g. a U+202E override) to the UI or agent (A4
defense-in-depth). The
outbound side edits `label` / `url` / `enabled` / `default_ttl_s` via
`PATCH /remote-trigger/endpoints/{id}`; a patched `url` is schema-checked
(http/https only, non-empty host, no embedded credentials) but deliberately
NOT passed through an L35/danger-category egress gate — outbound A2A POSTs
never traverse L35 (documented egress honesty, see
`a2a_friendship.update_endpoint_url`). PATCH-route ids additionally reject
`:` (Windows drive-relative path escape).

**Concurrency (2026-07-20):** every read-modify-write on origin/endpoint
files — console PATCH routes, `friendship_set_url`/`activate_connection`,
the reconnect-driven `update_endpoint_url` in the bridge receiver, and the
`corvin-a2a` CLI writers `label-endpoint` and `migrate-attestation` (A2
residual) — runs under `a2a_friendship.config_file_lock` (per-directory
`.a2a_config.lock`, `fcntl.flock` on POSIX / `msvcrt.locking` on Windows) in
addition to the console's in-process `_pair_lock`. This closes the
cross-process lost-update window where a peer could time reconnect
notifications — or a concurrent CLI edit could overwrite a lock-holding
console PATCH — to silently revert a fresh operator edit such as
`enabled: false`.

**Windows parity (ADR-0265, 2026-08-01):** `RemoteEndpointRegistry.load()` and
`OriginRegistry.load()` both reject a world-readable config file via a POSIX
`st_mode & (S_IRWXG|S_IRWXO)` check. NTFS has no separate owner/group/other bits, so
CPython's `nt` stat mirrors the owner bits onto group/other — this bitmask was
unconditionally true for every existing, readable file on Windows, so the check rejected
every endpoint/origin file unconditionally, making A2A send AND receive 100%
non-functional on any Windows-hosted instance (send/receive to ANY peer, including
another Windows instance). Fixed with a `sys.platform.startswith("win")` guard mirroring
`instance_identity.py::_validate_mode_strict`'s existing, correct precedent for the same
class of check — no-op on Windows rather than a half-implemented ACL check.

**Connection names for delegation:** `RemoteEndpointRegistry.resolve(name)`
(`remote_trigger_sender.py`) maps a reference to an endpoint_id — exact id →
unique case-insensitive label → unique id-prefix. An **exact endpoint_id
match wins deterministically** (ids are operator-assigned and unique; a
peer-controlled label equal to another peer's id must not make the victim
unaddressable — peer-triggerable DoS otherwise). Below that, an ambiguous
label raises `EndpointError("ambiguous_endpoint_ref")` instead of silently
picking a peer. Both the CLI (`corvin-a2a send <name>`) and the MCP tool
`a2a_send` (`corvin_orchestration.mcp_server`) accept a connection name; the
agent discovers names via `a2a_list_endpoints` (labels are returned for
disabled peers too, via `peek_label()`, sanitized read-side).

---

## Proactive Reconnect (ADR-0198, dynamic-IP peers)

**Problem:** an instance behind a dynamic-IP connection (e.g. an LTE router)
changes its public address at runtime. Every peer that already holds it as an
ACTIVE friendship endpoint (`operator/cowork/remote_endpoints/<kid>.json`)
keeps the stale URL until either an operator manually re-runs
`activate_connection`, or the next real task send fails with a
`TransportError` — a purely passive, timeout-driven recovery path.

**Mechanism:** the changed instance pushes a signed reconnect notification
to each known peer instead of waiting to be called. It travels as an
ordinary `TaskEnvelope` carrying a new optional
`reconnect: {"new_url": "<base url>"}` field (additive — the wire version
stays at the actual `PROTOCOL_VERSION = 8`; the constant is reserved for
capability-discovery milestones, not every additive field), HMAC-covered by
the *same* per-pairing key already established at pairing time — no new
credential, no new trust root. `RemoteTriggerReceiver.receive()`
short-circuits on this field, BEFORE attachment/classification/
worker-dispatch machinery, validates the URL, then rewrites the peer's own
`remote_endpoints/<kid>.json` `url` field via
`a2a_friendship.update_endpoint_url()`.

**Backward compatibility (honest statement, corrected 2026-07-19):**
pre-ADR-0198 receivers do NOT silently ignore the `reconnect` field — their
canonical payload omits the unknown key, so the sender's HMAC no longer
matches and the envelope is hard-rejected with `bad_signature`. That is
accepted fail-closed behaviour: an old peer never half-applies a reconnect,
it visibly rejects it; the fail-soft `send_reconnect()` logs
`A2A.reconnect_send_failed` and normal task traffic is unaffected.

**Fail-closed guarantees:**

| Guard | Effect |
|---|---|
| Signature/origin/time-window/replay checks run in `_validate()` first | An unauthenticated or replayed reconnect is rejected before it is even parsed as a reconnect |
| Endpoint must already be `state == "ACTIVE"` and `enabled` | A PENDING (never-yet-connected) or disabled peer cannot be reconnected into existence — reconnect can only *update* trust that already exists |
| Danger-category SSRF gate (2026-07-19 redesign; `a2a_friendship._reconnect_url_rejection_reason`) | Shape checks (`http(s)://`, ≤512 chars, printable, no whitespace) PLUS no https→http downgrade (`http` only if the previously stored URL was `http`). Then EVERY resolved address **and every embedded-IPv4** it carries (`.ipv4_mapped`, `.sixtofour`, NAT64 `64:ff9b::/96` + `64:ff9b:1::/48`) is classified: **forbidden** (loopback, link-local incl. `169.254.169.254` metadata + `fe80::/10`, unspecified, multicast, reserved) → `reconnect_url_forbidden_host` unconditionally; **private/LAN** (RFC1918, CGNAT `100.64/10`, ULA `fc00::/7`) → allowed ONLY if the previous stored host was ALSO private/LAN (LAN renumbering), else `reconnect_url_global_to_private`; **global** → allowed. Resolution failure rejects; `localhost`/`.onion` reject outright. This closes the NAT64/6to4/v4-mapped bypass (e.g. `[64:ff9b::7f00:1]` = 127.0.0.1) that the earlier `is_global`-only rule missed, while re-permitting the legitimate LAN/hotspot (`172.20.10.x`, `192.168.x`) reconnect it wrongly banned |
| No redirect-following on outbound POST/ping (2026-07-19; `remote_trigger_sender._NO_REDIRECT_OPENER`) | `_http_post` routes through a `HTTPRedirectHandler` that refuses to follow — a paired peer's `302` to `http://127.0.0.1`/`169.254.169.254` becomes a `http_3xx` `TransportError`, not a silent internal fetch |
| Write-first, audit-reflects-reality (`_handle_reconnect`, redesigned 2026-07-19 — the earlier build audited `reconnect_applied` BEFORE the write, so a subsequent write failure left the chain asserting an application that never happened) | Order is: read-only validation → on rejection audit `A2A.reconnect_rejected` (no write; audit-failure rolls back the nonce and rejects) → on pass, DURABLE endpoint write (temp+fsync+rename) FIRST, then audit the *real* outcome: `A2A.reconnect_applied` only when the write truly succeeded, else `A2A.reconnect_failed`. Nonce invariant: every path either audits a definitive outcome with the nonce consumed, or rolls the nonce back |
| Egress-control honesty (2026-07-19 — corrected) | Outbound A2A peer POSTs do **NOT** pass the L35 `check_engine_egress` gate (that gate is an engine-spawn control, never applied to A2A peer URLs — the earlier "still pass the L35 egress gates" claim was false). The real controls are the two rows above: no-redirect + the danger-category host gate. A **DNS-rebinding residual** remains (a compromised paired peer using short-TTL DNS that resolves global at check-time and private at send-time) — accepted for this release: the peer must already be a cryptographically-paired ACTIVE friend and redirects are blocked |
| No IP/URL in audit `details` | Mirrors the existing A2A audit allow-list (`endpoint_id`, `task_id`, `reason`, `status`, `duration_ms`) — the new URL itself is never logged. Numeric audit values are magnitude-bounded (≤ 10^13) so a Discord-UID-shaped int cannot leak past the backstop the way its string form is redacted (2026-07-19) |

**Trigger (sender side):** `RemoteTriggerSender.send_reconnect(endpoint_id, new_url)`
builds and POSTs the signed envelope, fail-soft (never raises). It is polled
from `a2a_friendship.check_and_broadcast_reconnect()`, which compares the
local outbound-interface IP (pure local UDP-connect trick, no external
egress call) against a cached last-known value at
`<corvin_home>/global/remote_trigger/last_known_ip`; on change, it
re-announces the operator-configured `get_my_url()` to every ACTIVE
friendship endpoint. This is polled from the existing 5-minute presence-
heartbeat thread (`aco/heartbeat.py::_heartbeat_loop`) rather than a new
dedicated thread — deliberately independent of `ping_enabled` (opting out of
the anonymous telemetry ping says nothing about wanting dead A2A peer links).
Since 2026-07-19 the heartbeat thread ALWAYS starts; `ping_enabled` gates
only the telemetry `send_heartbeat` inside the loop (re-checked per
iteration), so boot-time opt-outs still get the A2A reconnect poll.
Re-announce semantics (2026-07-19 fixes): the new IP is persisted once the
reconnect was **delivered** to ≥ 1 peer — i.e. that peer returned a
cryptographically SIGNED response, accept OR reject — or when there is nothing
to announce. Delivery, not acceptance, drives persistence: a peer that
signs-rejects will reject again on retry, so re-broadcasting to it every tick
is pointless and previously grew both audit chains unbounded (`send_reconnect`
now returns True on any signed response, False only for a genuinely
unreachable/unsigned peer). An all-peers-unreachable cycle still retries on
the next tick instead of silently losing the change. Broadcast cost is bounded
(10 s per peer, 180 s wall-clock budget per cycle).

**Scope note:** local-interface-IP-changed is a *proxy* trigger, not true
public-IP detection (which would require an external STUN/echo call and a
new L35 egress allowlist entry — out of scope here). Operators behind
NAT/CGNAT must still keep `my_a2a_url` (`CORVIN_A2A_URL` env var or
`<corvin_home>/global/remote_trigger/my_a2a_url`) current, e.g. via a DDNS
updater; this mechanism only makes the *push* proactive once that URL is
known to have changed, instead of leaving peers to time out.

### Audit events (ADR-0198)

| Event | Severity | When |
|---|---|---|
| `A2A.reconnect_sent` | INFO | Sender's `send_reconnect()` received a signed `"ok"` response (accepted). Note: a signed `"rejected"` also counts as *delivered* for persistence, but is logged under `reconnect_send_failed` for visibility |
| `A2A.reconnect_send_failed` | WARNING | Transport error, bad/absent signature, unsigned response, or the peer signed-rejected the reconnect |
| `A2A.reconnect_applied` | INFO | Receiver validated the reconnect AND the durable endpoint write succeeded; event lands AFTER the write (write-first, audit-reflects-reality) |
| `A2A.reconnect_rejected` | WARNING | Receiver validated the envelope but declined to apply (bad URL shape, danger-category SSRF gate, no matching ACTIVE endpoint) — no write attempted |
| `A2A.reconnect_failed` | WARNING | Validation passed but the durable endpoint write failed (transient: disk full / race); the nonce is rolled back so a later push retries (replaces the former `reconnect_rollback`, which the write-first ordering makes unnecessary) |

### Key files (ADR-0198 additions)

| File | Role |
|---|---|
| `operator/bridges/shared/remote_trigger_receiver.py` | `TaskEnvelope.reconnect` field, `_handle_reconnect()` |
| `operator/bridges/shared/remote_trigger_sender.py` | `RemoteTriggerSender.send_reconnect()` |
| `operator/bridges/shared/a2a_friendship.py` | `update_endpoint_url()`, `detect_local_ip()`, `check_and_broadcast_reconnect()` |
| `core/console/corvin_console/aco/heartbeat.py` | polls `check_and_broadcast_reconnect()` each 5-min tick |

---

## Typed error taxonomy (ADR-0197, sender-side)

`RemoteTriggerSender.send()` and `ping()` classify every failure into a
closed `error_category` enum (`remote_trigger_sender.ErrorCategory`),
surfaced on `SendResult` / `PingResult` alongside the legacy `ok`/`status`
fields:

| `error_category` | Meaning for the caller |
|---|---|
| `unreachable` | Nothing answered — DNS, refused, or TLS failure (`connection_failed`) |
| `timeout_transport` | Sender-side connect/read/total-transfer deadline |
| `timeout_remote` | Peer answered; its own worker/engine timed out — **`ok=False`** (fixes the pre-ADR bug where this read as success) |
| `rejected` | Peer explicitly refused (validation, TTL, revocation, rate limit) |
| `filtered` | House-rules (L44) blocked the instruction |
| `auth_failed` | Something answered but did not prove it was the paired peer (`bad_signature` / `missing_signature` / `task_id_mismatch`, unsigned ping response, instance-pin mismatch) |
| `http_error` | Peer's HTTP layer rejected before A2A logic (`http_status` carried alongside) |
| `protocol_error` | Unparseable/oversized response, invalid attachments |
| `internal_error` | Catch-all |

**Corrected `ok` semantics:** `SendResult.ok = (status not in ("rejected", "timeout"))`
— `ok=True` means the instruction actually ran and returned a receiver-signed result.

**Template-only `error_detail` (ADR-0197 §2, hardened 2026-07-19):**
`error_detail` is ALWAYS drawn from the fixed template set
(`_ERROR_DETAIL_TEMPLATES`) or the closed exception-type-name allowlist
(`_ALLOWED_EXC_TYPE_NAMES`) — never `str(exc)` verbatim, never interpolated
peer-controlled text (a malicious receiver's `status` string is mapped to
the fixed `"unexpected_receiver_status"` template). Reason strings are
closed at the raise sites (`transport_error:<TypeName>`,
`invalid_response_json`, `canonical_encode_failed`, `bad_recv_key` — no
embedded exception text).

**Audit fields + fail-closed backstop:** every audited `details` dict passes
through `_assert_audit_details_safe` (analogous to telemetry's
`_assert_safe`): only allowlisted keys (`endpoint_id`, `task_id`,
`instance_id_match`, `status`, `duration_ms`, `reason`, `ttl_s`,
`nonce_prefix`, `http_status`, `error_category`, `error_detail`,
`attachments_count`, `our_chain_tail`, `peer_chain_tail`, `match`,
`reachable`, `source`) with enum/typename-shaped values; free-form values
are dropped and replaced with `"redacted"` — never raised on, never sent.

---

## Lightweight peer liveness — `a2a_ping` (ADR-0199)

**Status: sender + receiver implemented in ALL THREE hosts (2026-07-29)** —
`POST /v1/a2a/ping` is served by `a2a_http_server.py`, the gateway
(`corvin_gateway/app.py`), AND `corvin_console.standalone` (added 2026-07-29,
ADR-0257 — see below; `corvinos-serve`, the default autostart target on every
OS, runs `corvin_console.standalone` and had NO A2A listener at all before
this). All three delegate to the shared core
`a2a_http_server.process_ping_request()` so the backends cannot drift
(ADR-0199's parity requirement holds by construction).

Receiver-side behavior (2026-07-22 adversarial-review hardening):
- **Anti-oracle ordering:** the HMAC signature is verified BEFORE the
  freshness check, and unknown-origin / bad-signature both return one opaque
  `403 ping_rejected` — an unauthenticated caller cannot enumerate paired
  origin_ids. `400 stale_ping` is only reachable with a valid signature.
- **Rate limit:** a ping-only, separately-bounded per-origin token-bucket map
  (60 rpm, `_PING_RATE_BUCKETS`) is checked BEFORE any disk work, so ping
  floods cannot burn CPU/disk via `OriginRegistry.load`. It is deliberately
  NOT the receiver's `/receive` bucket map: that map's invariant is
  "populated post-HMAC only", and sharing it would let unauthenticated fake
  origin_ids evict real `/receive` buckets — ping floods can at worst evict
  other ping buckets.
- **task_id echo:** the signed response carries `task_id = ping_id`
  (Decision 3); a valid ping also records a receiver-side endpoint heartbeat
  (`a2a_friendship.record_endpoint_heartbeat`).

`RemoteTriggerSender.ping(endpoint_id, timeout_s)` — timeout clamped to
`[2, 10]` s (default 5), far below `a2a_send`'s `[5, 120]` window.

- **Request:** `{ping_id: uuid4, issued_at, origin_id}` + HMAC-SHA256
  signature with the pairing's `hmac_key`. POSTed to `<base>/v1/a2a/ping`,
  where `<base>` is the endpoint URL with its `/v1/a2a/receive` suffix
  stripped.
- **Response (contract):** `{ok, instance_id, protocol_version, server_time,
  task_id}` signed with the pairing's `recv_key`; **`task_id` MUST echo the
  request's `ping_id`** (anti-replay binding — settled 2026-07-19, see
  ADR-0199). The sender verifies via
  `_verify_response(..., expected_task_id=ping_id)`.
- **Authenticated, non-negotiable:** only a *signed*, verified response
  yields `reachable=true`. The legacy unsigned-rejection tolerance
  (ADR-0077 C-5) never confers liveness — an unsigned `ok:true` is
  `auth_failed` (forgeable-liveness fix, 2026-07-19).
- **No nonce store** — pings are side-effect-free; ±30 s `issued_at`
  freshness suffices (enforced receiver-side).
- **Failures reuse the ADR-0197 enum** (one taxonomy, two producers).
- **Audit:** one `A2A.ping_result` event per call (INFO when reachable,
  WARNING otherwise) with closed-enum details only (`endpoint_id`,
  `reachable`, `source`, `error_category`, `duration_ms`).
- The ADR-0199 §2 heartbeat-cache fast path is deliberately NOT implemented
  sender-side yet — it needs receiver-side last-seen records that do not
  exist; `source` is always `"network_probe"` for now.

---

## Reciprocal friendship handshake (ADR-0257, 2026-07-29)

The friendship-token flow (`create_friendship_token` → `import`) previously
produced two INDEPENDENT one-way trust records, not one bidirectional
pairing — the issuer had no record of the redeemer until the WHOLE token
exchange was repeated a second time in reverse, and `state="ACTIVE"` was set
purely from url-presence, never a reachability check. Fixed via a new
signed callback, one round trip, no extra operator step:

- `create_friendship_token()` call sites now also call
  `a2a_friendship.save_pending_friendship()` — a short-lived, single-use
  record (`kid` + the shared key) under a NEW directory,
  `operator/cowork/remote_pending_friendships/` (env override
  `REMOTE_PENDING_FRIENDSHIPS_DIR`, same 0600/atomic-write convention as
  `remote_origins`/`remote_endpoints`).
- `friendship_import` (redeemer B), after writing its local files as before,
  calls `a2a_friendship.send_friendship_ack()` — a signed `POST
  {issuer_url}/v1/a2a/friendship-ack`, authenticated with the SAME
  `hmac_key` both sides derive independently from the token's shared key
  (`_derive_channel_keys` — no new credential).
- The issuer (A)'s `a2a_friendship.process_friendship_ack_request()` (shared
  core, all three hosts — same "ships together" invariant as ping above)
  verifies the ack against its pending record (anti-oracle 403, same pattern
  as ping's unknown-origin/bad-signature), writes ITS OWN origin+endpoint
  files for B under the SAME `kid` (reusing `to_origin_dict`/
  `to_endpoint_dict` via a reconstructed `FriendshipToken`), PINGS B back
  (ADR-0199 `sender.ping()`) before ever reporting `state="ACTIVE"`, and
  deletes the pending record (single-use).
- New route: `POST /remote-trigger/pair/friendship/{kid}/recheck` —
  re-verify an existing connection (ADR-0199 ping) without repeating the
  token exchange.
- `state` now has THREE values: `PENDING` (no url known yet — unchanged),
  `UNREACHABLE` (a url is known but this side's own probe failed — **new**,
  replaces the old "ACTIVE by url-presence"), `ACTIVE` (this side's own
  probe succeeded). The connection record also carries `_peer_knows_us` /
  `_peer_reports_reachable` — separate from `state` — so the UI can tell "I
  can reach them" apart from "they can also reach me back" (the latter needs
  Settings → A2A → "My URL" configured; without it, an ack can never be
  sent and the pairing stays one-way).
- **Host gate for the ack's declared URL** (`a2a_friendship._ack_url_rejection_reason`)
  is DELIBERATELY more permissive than the ADR-0198 reconnect gate above: a
  first-time pairing has no "previous" stored URL to compare against, so
  private/LAN addresses are allowed unconditionally (only the "forbidden"
  category — loopback, link-local incl. cloud metadata, unspecified,
  multicast, reserved, `.onion` — is rejected). Two LAN machines pairing for
  the first time is the common case this whole feature exists for.
- **`origin_id_for_send` fix (found while implementing the above):**
  `to_endpoint_dict()` previously never set this field, so every
  `send()`/`ping()`/`send_reconnect()` for a friendship-token pairing fell
  back to the sender's own random `instance_id` as the outbound `origin_id`
  — which could never match the receiver's origin file (named `<kid>.json`).
  Every authenticated call from a friendship-token-paired endpoint was
  rejected as "unknown origin" REGARDLESS of `state`. Now set to `token.kid`.
- Tests: `operator/bridges/shared/test_a2a_friendship_handshake.py` (real
  HTTP, two instances, same harness style as `test_a2a_bidirectional.py`).
- Existing pre-2026-07-29 friendship connections lack `origin_id_for_send`
  and were never reciprocal — delete and re-pair them.

### LAN pairing usability (2026-08-02)

Reported live: pairing a Windows and a Linux instance on the same home
network got stuck at `PENDING`/`UNREACHABLE` with no visible cause, and the
operator had no idea their own LAN IP was even relevant. Two fixes:

- **`POST /remote-trigger/pair/friendship/create` never issues a token with
  no URL when one is inferable.** Previously a blank "own URL" form field
  produced `url=None` in the token — permanent `PENDING` on the importer's
  side, with no recovery short of the issuer discovering their own LAN IP by
  hand and re-pairing. It now falls back to the already-configured
  `a2a_friendship.get_my_url()`, then to `suggest_my_url()` (the same
  mesh-VPN-then-local-interface auto-detection `GET /my-url` already offers)
  — so a same-LAN pairing, the case this whole feature exists for (see
  `_ack_url_rejection_reason` note above), works without the operator ever
  needing to know or type their own address. The auto-detected value is
  persisted via `set_my_url()` so it shows under Settings → A2A afterward.
- **`MyUrlBanner`'s "Use this URL" button was hidden for private/RFC1918
  addresses** — exactly the addresses correct for LAN pairing — forcing the
  operator to retype the same value manually via "Enter a different URL…".
  Fixed: the button now always shows; the warning copy for a private address
  was reworded from "not reachable by external peers" (which read as "this
  is wrong") to explain it works for same-network pairing and only needs a
  VPN/public domain for peers outside the network.
- **Windows Firewall / Linux `ufw`**: `install.ps1` now adds a best-effort
  inbound allow-rule for the console/A2A port (`Install-CorvinFirewallRule`,
  idempotent, never fatal, no elevation check — mirrors
  `Install-CorvinAutostart`'s try/catch idiom); `install.sh` does the Linux
  equivalent via `ufw allow 8765/tcp`, but ONLY if `ufw` is already active
  and NEVER via `sudo` (this installer only elevates on the explicit
  `--always-on` flag) — Windows' and (when active) ufw's default inbound-
  block policy otherwise silently drops the peer's reachability probe,
  which looks identical to a misconfigured URL from the UI.

---

## Threat model

| Threat | Mitigated by |
|---|---|
| Fork bypasses pairing attestation | M1 (pairing gate) + M2 (per-envelope signed attestation) |
| Stolen HMAC keys without SesT | M2 (fork has no signing key) |
| Compromised legitimate instance | M3 (manifest revocation effective on next restart) |
| MitM on manifest fetch | Manifest is cryptographically signed; MitM cannot forge |
| Stale manifest attack | 7-day TTL; `a2a_manifest_required` for strict mode |
| Free-tier quota bypass via alternative pairing paths | `_check_a2a_peers_max()` called by all 4 pairing routes |
| Unauthenticated reconnect hijack (redirect a peer's outbound calls to an attacker URL) | `_validate()`'s HMAC/origin/time-window/replay checks run before `reconnect` is even inspected — no distinct/weaker trust path (ADR-0198) |
| Reconnect-as-bootstrap (spoof a PENDING/never-connected peer into existence) | `update_endpoint_url()` refuses any file that is not already `state == "ACTIVE"` and `enabled` (ADR-0198) |

**Out of scope:** Operator with valid license who deliberately modifies source.
The network enforces *valid license*, not *unmodified binary*.

---

| Threat (2026-07-19 additions) | Mitigated by |
|---|---|
| Compromised peer repoints our outbound A2A traffic at internal infrastructure via reconnect (SSRF/stored redirect) | Danger-category host gate (forbidden hosts incl. NAT64/6to4/v4-mapped embedded IPv4 + global→private) and no-scheme-downgrade in `update_endpoint_url` / `validate_endpoint_url_change`, PLUS no-redirect-following on the outbound POST (ADR-0198 hardening, 2026-07-19 redesign). Residual: DNS-rebinding by an already-paired peer (accepted this release) |
| Compromised peer 302-redirects our signed POST to an internal address | `_http_post` uses a no-redirect opener — a 3xx is a `TransportError`, never followed (2026-07-19) |
| Forged liveness: anyone answering the port returns unsigned `ok:true` to a ping | `reachable=true` requires a recv_key-signed response echoing `task_id=ping_id` (ADR-0199) |
| Peer-controlled text injected into audit records (status strings, exception reprs) | Template-only `error_detail`, closed reason strings, `_assert_audit_details_safe` backstop (ADR-0197) |

---

## ADR

Full decision records:
- `Corvin-ADR: decisions/0103-a2a-network-membership-attestation.md`
- `Corvin-ADR: decisions/0197-a2a-send-typed-error-taxonomy.md` (error taxonomy)
- `Corvin-ADR: decisions/0198-a2a-reconnect-broadcast.md` (proactive reconnect)
- `Corvin-ADR: decisions/0199-a2a-ping-lightweight-peer-liveness.md` (a2a_ping)
- `Corvin-ADR: decisions/0257-a2a-reciprocal-friendship-handshake.md`
- `Corvin-ADR: decisions/0258-a2a-location-independent-connectivity.md` (relay fallback)
- `Corvin-ADR: decisions/0261-a2a-relay-hardening.md` (self-delivery guard, slot reaper, byte budget, off-loop ack)

---

## Relay fallback hardening (ADR-0261, 2026-07-30)

The relay fallback (`a2a_relay.py`, behind `a2a_relay_fallback` — **default-OFF**; the
relay *server* is mounted nowhere in the shipped hosts, only `python -m a2a_relay`) was
hardened after an adversarial review:

- **Self-delivery guard.** The pairing `kid` and derived relay keys are identical on both
  peers, so a relay could route an instance's own outbound task back to it. `RelayListener`
  refuses any envelope whose HMAC-covered `sender_instance_id` is this instance's own UUID.
  (The residual shared-`kid` routing ambiguity degrades to a send-side timeout+retry, not a
  wrong execution; an instance-scoped routing key is a tracked follow-up.)
- **Slot reaper + byte budget.** `RelayState._prune()` drops expired queue items and evicts
  offline, drained slots past an idle TTL (aggressively for ephemeral `*:reply:*` slots);
  a global `_MAX_TOTAL_QUEUE_BYTES` caps queued bytes across all slots. This closes the
  memory-exhaustion DoS and the self-wedge after `_MAX_TOTAL_SLOTS` legitimate sends.
- **Registration results are read.** The listener now logs `register_rejected`
  (capacity / >64 kids / auth mismatch) instead of silently listening to nothing.
- **Off-loop ack.** Both hosts' friendship-ack routes run the sync
  `process_friendship_ack_request` via `asyncio.to_thread` so one ack cannot freeze the
  event loop.

## Relay config surface + path visibility (ADR-0258, 2026-08-03)

Previously the `a2a_relay_fallback` flag's own description promised a Console UI
("Settings -> A2A -> Relay URL") that did not exist — the relay URL could only be set via
`CORVIN_A2A_RELAY_URL` or by hand-editing `~/.corvin/global/remote_trigger/my_a2a_relay_url`.
This closed that gap, plus two related ones (no visibility into which transport actually
answered, no single contextual action to enable relay for a struggling peer):

- `GET`/`POST /remote-trigger/pair/relay-url` (`a2a_pair.py`) — Console-facing relay URL
  config, validated for `ws://`/`wss://` scheme, non-empty host, no embedded credentials.
- `POST /remote-trigger/pair/friendship/{kid}/enable-relay` — one-click opt-in surfaced in
  the pairing/recheck UI exactly when a direct connection to that peer fails. Sets the relay
  URL if given, flips `a2a_relay_fallback` via the SAME tenant overlay `feature_flags.
  set_enabled` / the Settings toggle both use (audited as `a2a.relay.enabled_for_peer`,
  fully visible afterward with `source="console"` — nothing hidden), then re-verifies
  reachability. The flag stays instance-wide and OFF by default on every fresh install —
  this endpoint only collapses the number of manual steps once an operator has decided, for
  one peer, to allow it.
- `PingResult.via` (`"direct"` | `"relay"`) — `remote_trigger_sender.ping()` now reports
  which transport actually answered; `friendship_recheck`/`friendship_connections` persist it
  as a sticky `_last_via` field (survives a subsequent failed recheck) and expose it as
  `via`. The Console shows a "via relay" badge next to the state badge.
- A 60 s client-side poll re-runs recheck for any `UNREACHABLE` connection automatically
  (self-healing), deliberately slower than the read-only 15 s list refetch since each poll is
  a real network probe — and, when it falls back, real traffic through a third party.

**Explicitly NOT built here** (see ADR-0258's 2026-08-03 status entry): a CorvinOS-Labs-
operated public default relay. That would remove the "you must supply a relay host"
step entirely, but is a separate infrastructure/hosting/liability decision left undecided —
self-hosting a relay (`python -m a2a_relay`) remains the only supported path.

## Gateway RelayListener wiring + origin/endpoint path consolidation (2026-08-04)

Two structural bugs found live-debugging a real installed (uv-tool) deployment where a
freshly paired peer failed closed with `unknown_origin` on every single request, despite a
demonstrably successful friendship-ack handshake:

- **`corvin_gateway/app.py` never started the ADR-0258 Stage 3 RelayListener at all** —
  `corvin_console/standalone.py` had the wiring (added 2026-07-29), `corvin_gateway/app.py`
  did not, even though both hosts mount the identical `/v1/a2a/receive` /
  `/v1/a2a/ping` / `/v1/a2a/friendship-ack` routes and are meant to never drift. Fixed:
  identical lifespan block added to `corvin_gateway/app.py`, same inert-unless-flag-and-URL
  guard, same start/stop symmetry. Note: `corvin serve` (the CLI entry point most installs
  actually run) launches `corvin_console.standalone:create_app`, not `corvin_gateway.app` —
  this fix matters for deployments that run the gateway directly (`corvin-webui.service`,
  `ops/launcher/service_entry.py`), a different, less common path than `corvin serve`.
- **Four independently-computed "default origin/endpoint directory" functions** —
  `remote_trigger_receiver.py::_default_repo_relative()`,
  `remote_trigger_sender.py::_default_endpoints_dir()`, `a2a_http_server.py::
  _default_cowork_dir()`, and `a2a_google_sender.py`'s inline default — all walk up from
  their OWN `__file__` for a `.corvin_repo`/`plugins` marker (a 2026-08-01/02 fix for a prior
  `IndexError` crash), with a "directory next to this file" fallback when no marker is
  found. In an installed/vendored deployment (these files live under
  `corvin_console/_vendor/operator/bridges/shared/`) no marker exists anywhere up the tree,
  so all four silently fall back to a bogus location — DIFFERENT from
  `core/console/corvin_console/routes/a2a_pair.py`'s own default (a fixed
  `Path(__file__).resolve().parents[3]`, which happens to still land correctly in this
  layout by coincidental nesting depth). The friendship-ack handler (via `a2a_pair.py`)
  writes to one directory; the receiver's `OriginRegistry` (via the four functions above)
  reads from another. Fixed: all four now anchor off the INSTALLED `corvin_console`
  package's own location first (`Path(corvin_console.__file__).resolve().parents[3]` —
  fixed depth relative to site-packages/venv-root regardless of how deep the calling file
  itself is nested, so it agrees with `a2a_pair.py` by construction), falling back to the
  marker-walk only when `corvin_console` genuinely isn't importable (the original minimal-
  standalone-deployment scenario). Regression test:
  `operator/bridges/shared/test_a2a_installed_path_consistency.py` simulates an installed
  layout and asserts all four resolvers agree.

**LAN bind toggle (`a2a_lan_bind`, 2026-08-04).** Deliberately did NOT change the default
bind to `0.0.0.0` (would silently change the security posture of every install). Instead
added a single feature flag, off by default, that all three places a bind host gets decided
now read:

- `ops/launcher/corvin/cli.py::_default_bind_host()` — `corvin serve` with no explicit
  `--host` flag.
- `corvinOS/installer/core.py::_webui_bind_host()` — Stufe-1 login-autostart command,
  read when the autostart entry is (re-)registered.
- `ops/launcher/service_entry.py::_webui_bind_host()` — Stufe-2 opt-in always-on service,
  read at `corvin-service install` time.

An explicit `--host` on `corvin serve` always overrides the flag in either direction. The
flag shows up automatically in Settings -> Features (generic `GET`/`PUT
/settings/features` — no custom frontend needed) since every registered `FeatureFlag`
renders there by construction. Flipping the flag does NOT retroactively rebind an
already-running process or an already-registered autostart service — it only changes what
the NEXT `corvin serve` / next (re-)registration binds to; the operator still restarts (or
re-installs the autostart entry) once, same as any other bind-address change would require
in any server. Tests: `ops/launcher/corvin/tests/test_lan_bind_flag.py` (7, both flag
states + explicit-override + fail-closed-on-resolution-error).
