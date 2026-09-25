# Layer 38 — A2A Network Membership Attestation (ADR-0103)

This document covers the A2A network membership attestation system layered on top of
the core RemoteTriggerReceiver/Sender protocol (ADR-0048).  Read
[`docs/agent-communication.md` § Protocol Architecture](../agent-communication.md#protocol-architecture-layer-38--protocol-v4) for the base protocol reference.

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

`corvin_operator/bridges/shared/self_test.py` runs `_check_a2a_network_membership()` as
part of `run_self_test()`.

| Check name | Severity | Condition |
|---|---|---|
| `a2a.network_pubkey` | CRITICAL | `a2a_network_pubkey.pem` missing or malformed |
| `a2a.network_pubkey` | WARNING | `cryptography` package not installed |
| `a2a.manifest_age` | WARNING | Cached manifest ≥ 3 days old |
| `a2a.sest_not_revoked` | CRITICAL | Local SesT fingerprint on revocation list |

---

## Audit events (ADR-0103)

Registered in `corvin_operator/forge/forge/security_events.py`:

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
| `corvin_operator/security/a2a_network_pubkey.pem` | Embedded trust anchor public key |
| `corvin_operator/bridges/shared/a2a_manifest.py` | M3 manifest fetch / cache / expose |
| `corvin_operator/voice/scripts/corvin_a2a.py` | M1 pairing gate (`_authorize_pairing_m1`) |
| `corvin_operator/bridges/shared/remote_trigger_sender.py` | M2 build `network_attestation` |
| `corvin_operator/bridges/shared/remote_trigger_receiver.py` | M2 validate `network_attestation` |
| `corvin_operator/bridges/shared/self_test.py` | M4 CRITICAL checks |
| `corvin_operator/forge/forge/security_events.py` | New A2A audit event types |

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
addition to the console's in-process `_pair_lock`. The POSIX acquire is
BOUNDED (`LOCK_TIMEOUT_SECONDS`, 2 s, `LOCK_EX | LOCK_NB` + retry): a wedged
holder raises `FriendshipLockBusy` and `friendship_set_url` answers 503
`lock_busy` instead of hanging forever. The module's advisory fail-soft still
covers a lock that cannot be *obtained*; a *contended* lock refuses, because
proceeding unlocked is the very lost update this section is about. This closes the
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
ACTIVE friendship endpoint (`corvin_operator/cowork/remote_endpoints/<kid>.json`)
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
| `corvin_operator/bridges/shared/remote_trigger_receiver.py` | `TaskEnvelope.reconnect` field, `_handle_reconnect()` |
| `corvin_operator/bridges/shared/remote_trigger_sender.py` | `RemoteTriggerSender.send_reconnect()` |
| `corvin_operator/bridges/shared/a2a_friendship.py` | `update_endpoint_url()`, `detect_local_ip()`, `check_and_broadcast_reconnect()` |
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
  `corvin_operator/cowork/remote_pending_friendships/` (env override
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
- Tests: `corvin_operator/bridges/shared/test_a2a_friendship_handshake.py` (real
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
  `corvin_console/_vendor/corvin_operator/bridges/shared/`) no marker exists anywhere up the tree,
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
  `corvin_operator/bridges/shared/test_a2a_installed_path_consistency.py` simulates an installed
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

## Link robustness — self-healing handshake, relay fan-out, LAN proxy (ADR-2057, 2026-09-24)

Measured live between two LAN instances (`shumway` ↔ `gpu-server`): the pairing
showed "peer can't reach you back" permanently. Six independent defects stacked;
each is fixed at its origin.

| Defect | Fix | Where |
|---|---|---|
| Recheck retried the reciprocal ack only after a successful ping — but the issuer answers our ping only once it has processed our ack. Deadlock. | Recheck retries the ack whenever `_peer_knows_us` is false; a signed ack response settles `reachable` itself (`via` reports direct/relay). | `a2a_pair._recheck_connection`, `a2a_friendship._ack_round_trip` |
| The issuer consumed its pending record on the first ack and answered every later ack with the opaque 403 — a lost first response stuck the redeemer forever. | Repeat ack accepted for an ESTABLISHED, ENABLED `_friendship` origin, verified against that origin's channel key (same key the first ack used — `_derive_channel_keys`). It refreshes the redeemer's endpoint URL (self-heals an IP change), rebuilds a missing endpoint record, pings back and re-records `state`. Opaque 403 before signature verification, ±30 s freshness, a disabled friendship is never resurrected. | `a2a_friendship._process_repeat_ack`, `_ack_ping_back_and_respond` |
| DHCP moved the host; the persisted `my_a2a_url` kept the dead address and the IP-change broadcast re-announced it. | `heal_my_url()` rewrites a persisted URL whose host is a literal RFC 1918 IPv4 that no local interface carries anymore (scheme/port/path kept; hostnames, public, mesh and still-assigned addresses untouched; `CORVIN_A2A_URL` wins). Runs every heartbeat tick before the `last_known_ip` early-return. | `a2a_friendship.heal_my_url`, `check_and_broadcast_reconnect` |
| A friendship kid (and its relay auth key) is identical on both peers, and a relay slot held ONE connection — whoever registered last received both directions (an ack sent over the relay came back to its own sender). | A slot holds up to `_MAX_CONNECTIONS_PER_KID` (4) live connections that proved the pinned credential; delivery fans out to all of them; each listener drops its own traffic via the existing `_relay_sender_instance_id` / `sender_instance_id` guard, so exactly the peer answers. A dead socket does not block the live one. **The public relay must be redeployed for this to take effect** — `ops/a2a-relay/deploy.sh` stages exactly `a2a_relay.py` + `requirements.txt` + `Procfile` and runs `railway up` for project `corvin-a2a-relay`, then waits for `/healthz` (deployed 2026-09-24; fan-out verified against production with two same-kid connections both receiving one delivery). | `a2a_relay.RelayState` |
| The relay listener ran the ack core (blocking 5 s ping-back whose relay fallback calls `asyncio.run()`) inside its event loop — the fallback raised, so a relay-only redeemer was never reported reachable, and the ping stalled every other delivery. | Ping and ack dispatch run via `asyncio.to_thread`, like task envelopes already did. | `a2a_relay.RelayListener._handle_deliver` |
| `corvin-webui.service` and `bridge.sh console` hard-coded `--host 127.0.0.1`, ignoring `a2a_lan_bind`, while `corvin serve`/`corvin-service`/the installer honoured it. | Both resolve the host through `python -m corvin_core.bind_host` (loopback on any failure, never 0.0.0.0). | `core/console/corvin_core/bind_host.py`, `core/gateway/systemd/corvin-webui.service`, `bridge.sh` |

**Recommended LAN topology (no `a2a_lan_bind`).** Keep the console on loopback and
expose only the three HMAC-signed routes through a user-space reverse proxy on a
separate port (e.g. nginx on `0.0.0.0:8775`, `location ~ ^/v1/a2a/(receive|ping|friendship-ack)$`,
POST only, every other path 404, `X-Forwarded-For`/`Forwarded`/`X-Real-IP` cleared —
uvicorn trusts forwarding headers from 127.0.0.1, and `local-login` treats a loopback
peer as the owner, so the proxy must never forward anything else). Set My URL to
`http://<lan-ip>:8775`. Binding the whole console to `0.0.0.0` exposes every
unauthenticated surface to the LAN.

**Pairing records are runtime state, never repo content.**
`corvin_operator/cowork/{remote_endpoints,remote_origins,remote_pending_friendships,pending_invites}/`
carry live channel keys; they are gitignored, and
`tests/security/test_no_tracked_a2a_runtime_secrets.py` fails if one is tracked
(commit cce86a13 had force-added a friendship's keys to this public repo — that
pairing must be treated as compromised and re-paired).

Tests: `test_a2a_friendship_handshake.py::TestRepeatAck` (real HTTP, two instances),
`test_a2a_relay.py::TestSharedKidFanOut` + `TestSharedKidAckOverRealRelay` (real relay
server on a socket, two real listeners, redeemer registering last — red on the old
relay), `core/console/tests/test_a2a_relay_config.py::TestRecheckAckDeadlock`.

## Zero-config connectivity — the token is the only input (ADR-2059, 2026-09-24)

Builds on ADR-2057. Operator requirement: a user enters the friendship token and
nothing else. Concept + root-cause analysis: `Corvin-ADR/concepts/a2a-robust-connectivity-concept.md`.

| Piece | What it does | Where |
|---|---|---|
| Connectivity manager | One task per host process (all work off the loop). Keeps the ingress listener in line with its config; keeps an auto-managed `my_a2a_url` on the current mesh/LAN address + ingress port (hostnames, https, public IPs, foreign ports and `CORVIN_A2A_URL` are never touched); starts / stops / re-points the relay listener at runtime (was boot-only); runs hello (= the idempotent ack) + ping per connection with backoff 10 s → 5 min while unhealthy and a 10 min keepalive while healthy. Transitions audited (`A2A.connection_state`, `A2A.ingress_state`, `A2A.relay_listener_state`, `A2A.my_url_updated`). | `a2a_connectivity.py` |
| Dedicated A2A ingress | Default on, port 8775, its own socket. Exactly `POST /v1/a2a/{receive,ping,friendship-ack}`; every other path/method 404. Served only to loopback / RFC 1918 / RFC 4193 / RFC 6598 (Tailscale) peers unless `allow_public`; per-address token bucket; shares the host's receiver (one nonce store). Config `<CORVIN_HOME>/global/remote_trigger/ingress.json` (`enabled`, `port`, `allow_public`), env `CORVIN_A2A_INGRESS=off|on`, `CORVIN_A2A_INGRESS_PORT`. Replaces the hand-written LAN proxy above; the console stays loopback. | `a2a_ingress.py` |
| Relay in the token | `create` embeds the issuer's relay URL (`rly`, signed, ignored by older parsers). `import` adopts it unless the operator chose a relay (or `off`) explicitly. No explicit choice → `DEFAULT_RELAY_URL` (project relay). `a2a_relay_fallback` stays default-off; create/import turn it on through the audited tenant overlay (`a2a.relay.enabled_for_pairing`). Relay URL `off` opts out completely. | `a2a_friendship.create_friendship_token/parse_and_verify/get_my_relay_url`, `a2a_pair.friendship_create/friendship_import` |
| Handshake completion | A verified ack (first or repeat) sets `_peer_knows_us` / `_peer_reports_reachable` on the RECEIVING side too (bug #5: the issuer showed "peer can't reach you back" forever). An ack with no direct URL goes straight to the relay; 404/5xx on the direct path fall back to the relay (400/402/403 stay authoritative). | `a2a_friendship._ack_round_trip`, `_ack_ping_back_and_respond` |
| Relay listener refresh | Re-reads registrations every 15 s and on a console nudge (`nudge_listeners()` after create/import/relay change); registers PENDING connections; `status` snapshot for diagnostics. | `a2a_relay.RelayListener` |
| Diagnostics | `GET /v1/console/remote-trigger/a2a/diagnostics`: ingress (running, port, counters), relay (connected, registered, source), advertised URL, per-connection handshake state. Metadata only. `enable-relay` also accepts a pending issued token. | `a2a_pair.a2a_diagnostics`, `friendship_enable_relay` |

Tests: `test_a2a_ingress.py` (peer gate, config, rate limit),
`core/console/tests/test_a2a_relay_config.py::TestZeroConfigPairingSurface`,
`test_a2a_zero_config_e2e.py` — a real relay process and two instance processes
(`a2a_e2e_host.py`, separate CORVIN_HOMEs, own audit chains, NO inbound route):
token-only pairing in both directions, issuer offline during import, address
change, revocation.

## Agent Hub live feed — A2A messages with media (ADR-2063, 2026-09-25)

The audit chain stays metadata-only for A2A (instruction text, worker output
and attachment bytes never enter it). The Agent Hub's **Live Feed** tab
(`/console/app/agent-hub`, now the default tab) is the readable view of the
same exchanges: a chat of every task this instance sent or received, the
peer's reply, and the attachments, rendered inline (images, audio, video,
PDF, text previews). The old metadata list moved to the **Audit trail** tab.

| Piece | What it does | Where |
|---|---|---|
| Content store | Tenant-local, append-only `messages.jsonl` + content-addressed `blobs/<sha256>` (the digest is recomputed, never the declared one). Dir 0o700, files 0o600. 30-day retention + 32 MiB cap, compacted with orphan-blob GC. Best-effort: a store failure never changes an A2A result. Override `CORVIN_A2A_FEED_DIR` (tests). | `corvin_operator/bridges/shared/a2a_feed.py` → `<tenant>/global/a2a_feed/` |
| Outbound hook | `RemoteTriggerSender.send()` is a thin wrapper over `_send_impl()`: it assigns the `task_id`, records the task BEFORE sending (so the feed shows it while the peer works), then records the response or failure on every return path. | `remote_trigger_sender.py::send`, `_record_feed_task`, `_record_feed_response` |
| Inbound hook | Records the task only AFTER HMAC, nonce, TTL, consent and the CLAG chain gate passed — an unauthenticated sender can never write into the store — then the signed response (including the injection-rejection path). | `remote_trigger_receiver.py::receive`, `_feed_record` |
| Console API | `GET /v1/console/a2a/feed?after=<seq>` or `?before=<seq>&limit=` (append-ordered page + `has_more` + `last_seq` + peer directory; `since=` kept as a legacy ts filter), `GET /a2a/feed/blob/{sha256}`, `POST /a2a/feed/send` (202; runs on a bounded send pool, result lands in the feed; > 16 KiB after NFKC → 422), `DELETE /a2a/feed` (audit-FIRST `A2A.feed_cleared`, counts only; no chain record → 503, nothing deleted). Router-level session + CSRF guard. | `core/console/corvin_console/routes/a2a_feed.py` |
| Blob serving | Only passive media types are served inline; SVG/HTML and every unknown type go out as `application/octet-stream` attachments. Always `nosniff` + `Content-Security-Policy: sandbox`. | `routes/a2a_feed.py::_INLINE_MIME` |
| UI | Agent rail with state dots + last message, chat bubbles (this instance right, peers left), reply quotes the task, typing indicator for tasks without a response, composer with attach / drag-drop / paste (1 MiB, 16 files — the protocol caps), 2 s polling with a `seq` cursor (`after`, drained while `has_more`), per-agent history via `before`. A detail-less `rejected` is explained, never shown as "delivered". | `web-next/src/components/agent-hub/live-feed.tsx`, logic in `src/lib/a2a-feed.ts` |

A rejection's reason stays on the answering side by protocol design (the
signed `rejected` response carries no reason); the feed says so instead of
guessing.

Tests: `corvin_operator/bridges/shared/test_a2a_feed.py` (real sender → real
receiver over HTTP: four records per exchange, forged envelope stores nothing,
broken store never breaks a send, retention, clear),
`web-next/tests/unit/a2a-feed.test.ts`,
`tests/e2e/test_agent_hub_live_feed_e2e.py` against the running console
(`CORVIN_E2E_A2A_LIVE=1` additionally sends a real envelope with a PNG to the
paired peer and verifies feed, blob serving and the `task_id` link to the chain).

## Adversarial review + hardening (ADR-2064, 2026-09-25)

Five parallel adversarial reviews of the whole A2A stack found 44 defects
(≈37 distinct), all fixed with regression tests, then a second review round
on the diff. The product goal they were measured against: *the user creates
a friendship token, any agent imports it, and the two talk — text and media —
while the user watches everything in the Agent Hub live feed, for many
connections at once.* `test_a2a_hub_ten_peers_e2e.py` proves exactly that
with ten agents (below).

**Root causes that blocked the goal outright**

| Defect | Effect | Fix |
|---|---|---|
| Empty `result_schema` → receiver releases `{}` (by design) and no sender declared one | every chat reply arrived empty (`filtered`) | `send()` defaults to `CHAT_RESULT_SCHEMA` (`{"output": string}`) when the caller passes none; the receiver invariant is unchanged |
| `_load_sest()` used the `CORVIN-` **EdDSA** licence as the RS256 network-attestation SesT | every licensed (Member) instance's tasks were rejected by every peer: `network_attestation_bad_sig` | the block is built only from a token whose header says `alg: RS256`; otherwise omitted (manifest grace period) |
| The connectivity manager (`start_manager`) had no production caller; the relay listener started once at boot, only if the flag was already on | a fresh install that paired by token was unreachable over the relay until a restart; no ACTIVE/UNREACHABLE transitions | both hosts (`corvin_gateway.app`, `corvin_console.standalone`) start/stop the manager in their lifespan |
| `/v1/a2a/receive` + `/ping` ran the sync receiver on the event loop; relay listener handled deliveries serially | one worker run froze the whole console and every other peer | `asyncio.to_thread` in both hosts; relay deliveries run concurrently (16 heavy slots, pings bypass) |
| Revocation could not reach the peer (keys + relay slot gone first) | the other side showed "peer knows us" forever | signed **revoke notice** on the friendship-ack channel, sent before the keys are deleted (`send_revoke_notice` / `_process_revoke_notice`); older receivers reject it as `missing_fields` |

**Security + integrity** — token `kid`/invite `oid` validated as filenames
(path traversal); repeat acks bound to the peer instance (`signature_v2`,
`_peer_instance_id`) and gated by the reconnect URL rule; ack reflection
rejected; redeemer-side SSRF gate on the token URL; unredeemed tokens
revocable; `a2a_peers_max` checked under the config lock after the
signature; invite registry claim is atomic (console + CLI); pairing is
audited first (`A2A.friendship_paired`, `A2A.friendship_url_updated`,
`A2A.friendship_peer_revoked`); the sender rejects responses signed by its
own instance and TOFU-pins the peer instance id (`A2A.instance_pinned`;
recover from a peer that lost its home by re-pairing); `receive()` never
raises on malformed input (no origin-existence oracle); nonce store keyed
`(origin_id, nonce)` with a per-origin cap and refuse-when-full (no
eviction of live nonces); a replay no longer spends a rate token; ingress
gates peers and rate at accept time with a whole-request read deadline and
thread caps; ping pre-auth bucket per source address; worker deny list
covers `Monitor`, `PowerShell`, `RemoteTrigger`, `CronCreate`, `Workflow`,
`TaskStop`/`KillShell`/`KillBash` and `Agent` when `allow_bash`/subagents
are off; silent workers are killed at `ttl_s` (watchdog) and reported as
`timeout`; relay fallback only when the direct request provably was not
delivered (no duplicate execution); relay frames up to a 4 MiB plaintext
with `task_id` on error frames (the public relay needs a redeploy for >512 KB).

**Audit completeness** — every `A2A.*` event now has an allowlist listing
all fields its emitter sends (`corvin_operator/forge/forge/security_events.py`);
attachment file names are no longer emitted (peer-chosen, may carry personal
data). The six compliance modules whose `_audit_path()` fell back to the
legacy `<home>/global/forge/audit.jsonl` without a tenant now resolve the
process tenant's canonical chain — the boot tripwire's own consent probe was
the writer behind `audit_chain_split` on fresh installs.

**Live feed** — the store takes one sidecar lock (`store.lock`) across
processes for blob write + append, compaction and clear; `seq` (allocated
under that lock) replaces wall-clock `ts` as the cursor; `GET /a2a/feed`
takes `after` (oldest page past a cursor + `has_more`) and `before`
(history); the UI drains `has_more`, loads a peer's history on selection,
offers "Load older", renders peer Markdown without auto-loading remote images,
keeps the conversation of a removed connection under its last name, and the
route is bound to the host A2A tenant.

**Proof** — `corvin_operator/bridges/shared/test_a2a_hub_ten_peers_e2e.py`:
a real relay, the user's instance as the real gateway + console (own
CORVIN_HOME, Member licence copy) driven only over HTTP with session + CSRF,
and ten agent processes running the real worker path with a scripted model.
Half the agents are direct over the LAN ingress, half relay-only; the hub is
relay-only. Steps: 10 tokens → 20 linked connection ends → user sends text +
image to every agent, each reply carries the marker, the input hash and a
rendered image → every agent writes to the user → feed integrity + gap-free
seq cursor → 30 concurrent round trips → one revocation cuts exactly that
agent off and it sees the revocation → the UI shows all agents with images →
no A2A audit field dropped in any of the 11 chains. Run:
`python3 test_a2a_hub_ten_peers_e2e.py` (`E2E_PEERS=N` to scale).

### Pairing binding keys (ADR-2064, review rounds 4–5)

A friendship token is a bearer secret and instance ids are public (every
signed ping response carries one), so neither can authenticate "the bound
peer". Each instance therefore has a long-term **X25519 binding key**
(`<CORVIN_HOME>/global/remote_trigger/a2a_bind_key`, 0600, fsync'd on
creation, a corrupt file is replaced; `a2a_binding.py`).

- **The issuer's public key travels in the signed token** (`bpk`, ignored by
  older parsers). The redeemer stores it at import as `_peer_bind_pub`.
- **The redeemer's public key arrives with the first ack** (`bind_pub`,
  covered by `signature_v3`). The issuer stores it with the pairing, and its
  ack response carries its own `bind_pub`.
- **A key is never adopted from any later response.** After pairing, every
  response path can be spoofed by someone holding the token: relay fan-out,
  or a URL moved on a legacy pairing. Keys come only from the token and the
  first ack.
- **Legacy pairings** keep the instance-id rule until they are re-paired.
- **Once the peer's key is stored, every change needs a MAC** under
  HKDF(X25519, kid):
  - a repeat ack that moves the URL or rebuilds the endpoint (`bind_mac`);
  - a revoke notice (`bind_mac`);
  - an ADR-0198 reconnect push (`reconnect.bind_mac` over kid, new URL and
    nonce). A reconnect also requires a bound peer and a matching
    `sender_instance_id`.
- **Keep-alives from the bound sender need no MAC.**
- **Inbound acks never bind anything.** A binding comes only from our own
  verified outbound round trip or from the token.
- **Recovery after a peer reinstalls** (new instance id and binding key):
  re-pair with a new token, because keys are never re-learned. Endpoint PATCH
  `reset_instance_pin` (clears the instance pin, `_peer_instance_id` and
  `_peer_bind_pub`) only unblocks a legacy pairing's instance-id pin.

### Executors (review rounds 2–4)

A2A task work runs on a dedicated executor (`run_a2a_work`, 24 threads). It is
used by both hosts' `/receive` and by relay task deliveries. Pairing acks and
revoke notices over the relay use their own control executor
(`run_a2a_control`) and skip the heavy-slot semaphore.

Pings stay on the default pool, so worker runs never starve liveness checks.
ContextVars are carried like `to_thread`. At most 4 worker runs per origin at
a time; beyond that the task gets a signed `rejected` whose data carries
`reason: "busy"` (the UI says "busy — try again"), and it is never queued.
The receiver's own feed record of that refusal carries the same
`data.reason`, not an `error` string, so its UI shows "your instance was busy"
(round 8).

### Messages with files and no text (round 8)

A message with attachments and an empty text is legitimate. The sender puts
the stand-in instruction `ATTACHMENTS_ONLY_INSTRUCTION` ("Please look at the
attached file(s).") on the wire and keeps the empty text in its feed. The
worker applies the same substitution for older senders. Without it, the
receiver refused the message as an `injection_attempt:empty_instruction` and
left a false security signal in its audit chain. Text-less AND file-less stays
refused.

### Legacy invite routes (round 8)

`POST /remote-trigger/pair/redeem` host-gates the invite's `accept_url` and
`issuer_url` (`_ack_url_rejection_reason`) before any request or write, and
refuses with 409 when the invite's `origin_id` already names a connection. An
invite can therefore neither take over nor, through the failure rollback,
delete another peer's pairing. `POST /remote-trigger/pair/accept` restores the
invite on its correctable refusals (400 URL, 409 id), so the redeemer can
retry, and deletes it on a licence refusal (402). No `.used` file holding the
pairing keys is left behind. `corvin-a2a import` rolls back on the issuer's
402 exactly like the console import, and prints the connection's stored name
(the issuer's `nam`).

### Round 9

- **Reconnect MAC names its direction.** Both ends of a pairing derive the
  same binding secret, so a push A sent to B also verified at A. A token
  holder could reflect it and re-point A's endpoint for B at A itself.
  `reconnect_bind_canonical(kid, new_url, nonce, bind_ts, sender_pub)` now
  covers the sender's binding public key. The sender uses its own key, the
  receiver the stored `_peer_bind_pub`. Repeat acks and revoke notices
  already covered the sender's instance id.
- **ADR-0063 invite accept is host-gated.** `parse_invite` requires `rp` to
  be a plain path (no `@`, `?`, `#` or `..`), so `url + rp` cannot move the
  real host. Accepting a FOREIGN invite runs `_ack_url_rejection_reason`
  (`a2a_invite.endpoint_url_rejection_reason`) before any claim or write, in
  the console route (400) and the CLI (exit 1). `generate_invite` refuses a
  non-plain `receive_path`.
- **Relay queue budget per kid.** Besides the global 64 MiB ceiling, one
  recipient kid may hold at most one full-size frame
  (`_MAX_QUEUE_BYTES_PER_KID = _MAX_MESSAGE_BYTES`). Residual: first-use
  registration is credential-free, so an attacker with many fake kids can
  still fill the global budget for the queue TTL. That limit is availability
  only and applies to the standalone relay process.

### Round 10

- **Task direction.** A friendship's HMAC keys are identical on both ends, so
  a task we signed for the peer also verified against our own origin file.
  Anyone who saw it on the wire (plain `http` on a LAN) could POST it back
  and have us run our own instruction as the peer's, with no key at all.
  `_validate()` now refuses, right after the HMAC check and before the nonce
  is consumed, a task whose HMAC-covered `sender_instance_id` is our own id
  (`sender_is_self`) or differs from the bound `_peer_instance_id`
  (`sender_not_bound_peer`). This runs inside `receive()`, so every
  transport is covered. An empty sender id (legacy sender) passes. The
  Google A2A adapter builds its in-process envelopes with an empty
  `sender_instance_id`: it speaks for no Corvin peer and must not claim the
  host's own identity (round 11).
- **Attachment names** use `fullmatch`; `$` admitted a trailing newline.

### Live feed store — seq allocation (review rounds 4–5)

`seq` is allocated under `store.lock` and never lowered, not even by `clear()`.
Records without a `seq` get one from the same counter through the sidecar
`seq_overrides.json`, under the lock. Such records come from the first store
version, or from a process still running it during a rolling restart.
`messages.jsonl` is never rewritten on a read. A reader whose file parse
overlaps a compaction re-reads the file and the sidecar together under the
lock. `compact()` folds the sidecar into the records.

Residual: during a rolling restart, a compaction can still race an old-version
writer, which locks only the data file. Restart all feed writers together
(gateway, adapter, MCP server).
