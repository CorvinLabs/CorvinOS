# Layer 37 — Audit-at-rest Encryption + Retention (reference)

**Module:** `operator/bridges/shared/audit_sealer.py`
**ADR:** `Corvin-ADR: decisions/0044-L37-audit-at-rest.md`
**Status:** Shipped M3 (2026-05-19). RFC 3161 TSA extension M3+ (2026-05-21).

---

## What this layer does

L37 gives the L16 audit chain a lifecycle beyond a single growing file:

1. **Rotation** — when the live `audit.jsonl` crosses a size or age
   threshold, it is renamed to a timestamped segment
   (`audit.YYYY-MM-DDTHHMMSSZ.jsonl`). A fresh `audit.jsonl` starts
   with one `audit.rotation_link` entry whose `prev_hash` equals the
   rotated segment's tail — the chain stays intact across boundaries.

2. **Sealing** — the rotated segment is piped through an
   operator-chosen external binary (`age` or `gpg`) into an encrypted
   file (`<segment>.age` or `<segment>.gpg`), chmod-444. The plaintext
   is zero-filled and unlinked.

3. **External timestamping** (opt-in) — after sealing, an RFC 3161
   TSA request is sent, and the raw TimeStampResp is stored as
   `<segment>.tsr` alongside the sealed file. Provides third-party
   proof that the sealed segment existed at a specific time —
   independent of the Corvin operator. Non-fatal: a TSA failure
   logs a WARNING but does not roll back the seal.

4. **Retention** — sealed segments older than `retention_years` are
   deleted after emitting `audit.segment_retired` (INFO) — audit-first.

5. **Operator unseal** — `voice-audit unseal <segment>` decrypts into
   a tmpdir (mode 0600) for DPO / legal-hold inspection. Emits
   `audit.unseal_requested` (WARNING) *before* decryption.

---

## Tenant configuration (full schema)

```yaml
spec:
  audit:
    retention_years: 7              # float, default 7.0; 0 = keep forever

    encryption_at_rest:
      enabled: false                # true to seal rotated segments
      recipient: ""                 # age public key or gpg key id
      sealer_cmd: age               # "age" | "gpg"
      # RFC 3161 external timestamping (opt-in, non-fatal):
      tsa_enabled: false
      tsa_url: ""                   # e.g. "http://tsa.example.com/tsr"
      tsa_hash_algo: sha256         # currently only "sha256"

    rotation:
      max_size_mb: 100              # float; 0 = size-trigger disabled
      max_age_days: 30              # int;   0 = age-trigger disabled
```

**Validation rules:**
- `encryption.enabled=true` requires non-empty `recipient`
- `tsa_enabled=true` requires non-empty `tsa_url`
- `tsa_hash_algo` must be `"sha256"` (only value currently supported)
- `sealer_cmd` must be `"age"` or `"gpg"`
- All numeric limits must be ≥ 0

---

## Lifecycle diagram

```
  audit.jsonl  ──(size or age threshold)──▶  audit.<stamp>.jsonl
      │                                            │
      │  (rotation_link event written               │ encrypt via age/gpg
      │   to NEW audit.jsonl)                       ▼
      │                               audit.<stamp>.jsonl.age  (chmod 444)
      │                                            │
      │                                            │ tsa_enabled=true
      │                                            ▼
      │                               audit.<stamp>.jsonl.age.tsr (chmod 444)
      │                                            │
      │                               (after retention_years)
      │                                            │
      │                               audit.segment_retired → file deleted
      ▼
  audit.jsonl  ←─ chain continues
```

---

## API reference

### `should_rotate(audit_path, policy, *, now=None) -> RotationDecision`

Returns `RotationDecision(should, reason, size_mb, age_days)`.
No side effects. Missing file → `should=False`.

### `rotate_and_seal(audit_path, policy, *, audit_writer=None, now=None, sealer=None) -> RotationResult`

Core function. Steps:
1. Rename live → rotated segment.
2. Extract tail hash of rotated segment.
3. Write fresh `audit.jsonl` with `audit.rotation_link` entry. The link is
   **MAC'd under the audit anchor key** and, still under the rotation flock,
   `security_events.note_chain_rotation()` re-anchors the out-of-tree chain
   identity record to the new genesis (`rotation_genesis`). See
   [Rotation must be an out-of-tree fact](#rotation-must-be-an-out-of-tree-fact).
4. If `encryption.enabled`: seal → emit `audit.segment_sealed`.
5. If `encryption.tsa_enabled`: POST TSA request → write `.tsr`
   (chmod 444) → emit `audit.segment_timestamped`. On failure:
   emit `audit.tsa_request_failed` (WARNING), continue — seal stands.
6. **Re-anchor chain (ADR-0135 M2):** call `write_chain_anchor(audit_path, anchor_path)`
   AFTER all audit events so `stored_tail` equals the current chain tail.
   Writing the anchor before `audit.segment_sealed` / `segment_timestamped` caused
   a false `tail_mismatch` CRITICAL at next boot (the anchor captured an intermediate
   tail, while boot saw the post-sealed tail). Best-effort — failure yields "absent"
   anchor (WARNING). Skipped automatically if sealing fails (exception propagates).
   `audit_rotate.py` loads the license before calling this function so the anchor
   HMAC uses the same paid-tier key that `verify_chain_anchor()` uses at boot.
7. **Signed segment manifest (FND-14):** append one continuity record to
   `audit.segments.manifest.jsonl` (next to `audit.jsonl`, mode 0600) recording
   the rotated segment's `first_prev_hash`, `last_hash`, on-disk name and
   `sha256`, MAC'd with the out-of-tree audit anchor key (ADR-0137 M2 —
   the same key as the per-record MAC). Best-effort: a manifest-write failure
   is a WARNING, never a rotation failure (the chain itself is intact).
   The manifest lets the **default** `voice-audit verify` confirm
   cross-segment continuity **without decrypting** any segment — it checks that
   each recorded segment still exists with a matching sha256, that segments form
   a contiguous chain (`segment[i].last_hash == segment[i+1].first_prev_hash`),
   and that the live chain links to the newest sealed tail. Deleting or swapping
   a sealed segment is detected on the daily timer; rehashing the manifest to
   hide it requires the anchor key a filesystem attacker lacks. The heavyweight
   `--include-sealed` walk (which decrypts every segment) remains for full
   historical verification.

Returns `RotationResult`:

| Field | Type | Description |
|---|---|---|
| `rotated` | `bool` | Whether rotation happened |
| `rotated_path` | `Path \| None` | Plaintext rotated segment; `None` when sealed (plaintext removed) |
| `sealed_path` | `Path \| None` | Sealed segment; `None` when encryption disabled |
| `timestamp_token_path` | `Path \| None` | RFC 3161 `.tsr` file; `None` when TSA disabled or failed |
| `new_live_path` | `Path` | Fresh `audit.jsonl` |
| `last_hash` | `str` | Tail hash of rotated segment |
| `reason` | `str` | Human-readable rotation reason |

### `enforce_retention(audit_dir, policy, *, audit_writer=None, now=None) -> list[Path]`

Lists all segments (plaintext + sealed). For each older than
`retention_seconds`: emit `audit.segment_retired` (INFO), chmod
0644, unlink. Returns removed paths. Per-file errors are swallowed.

### `unseal_to_temp(sealed_path, *, tmpdir=None, identity_file=None, sealer_cmd=None, audit_writer=None, requester="") -> Path`

Emits `audit.unseal_requested` (WARNING) **before** decryption.
Decrypts into `tmpdir / <segment_name_without_ext>` (mode 0600).
Sealer kind inferred from suffix (`.age` / `.gpg`) or explicit kwarg.
Caller must remove the plaintext file when done.

### `policy_from_tenant_config(tenant_config: dict | None) -> AuditPolicy`

Parses `spec.audit` from a loaded `tenant.corvin.yaml` dict.
Missing or malformed sub-keys use module defaults. Raises
`ValueError` on bad values.

### `_build_tsa_request(file_hash_bytes: bytes) -> bytes`

Builds a minimal RFC 3161 `TimeStampReq` DER structure. For a
32-byte SHA-256 hash the result is exactly 59 bytes. Pure stdlib,
no external dependency.

```
TimeStampReq SEQUENCE {
    version         INTEGER 1
    messageImprint  MessageImprint {
        hashAlgorithm  AlgorithmIdentifier (SHA-256 OID 2.16.840.1.101.3.4.2.1 + NULL)
        hashedMessage  OCTET STRING (32 bytes)
    }
    certReq         BOOLEAN TRUE
}
```

### `_request_timestamp_token(sealed_path, *, tsa_url, hash_algo="sha256", timeout_s=15) -> bytes`

Hashes `sealed_path` with SHA-256, builds the TSA request via
`_build_tsa_request`, POSTs to `tsa_url` with
`Content-Type: application/timestamp-query`. Returns raw
`TimeStampResp` bytes. Raises `RuntimeError` on failure.

**Monkeypatching for tests:**
```python
import unittest.mock as mock
import audit_sealer as _mod

with mock.patch.object(_mod, "_request_timestamp_token", return_value=b"..."):
    result = rotate_and_seal(audit, policy, sealer=fake_sealer)
```

---

## Rotation must be an out-of-tree fact (R3-A1, 2026-09-07)

A rotation is the ONE legitimate way a live chain's genesis changes. Everything
else that changes it is a whole-file replacement, which `verify_chain` reports as
`chain_replaced` and the boot tripwire refuses.

Telling the two apart used to be done by SHAPE: record 1 of `audit.jsonl` is an
`audit.rotation_link` whose `prev_hash` equals the tail in the out-of-tree path
record. That argument ("a rewriter cannot read the path record") was **false** —
the recorded tail is set to the hash of the last chained record on every write, so
it is byte-identical to the last `hash` in the file the attacker is rewriting. A
forged rotation_link that borrows the file's own tail therefore suppressed
`chain_replaced`, `tail_truncated` and `mac_stripped_chain` in one move, leaving
only a line-1 `broken_chain` that `audit_chain_intact` classifies as historical
once the file exceeds `TAIL_RECORDS` (200) — a length the attacker chooses.

Two facts an in-tree rewriter cannot mint now gate the recognition
(`security_events._rotation_link_authenticated`), and **either** suffices:

| Fact | Written by | Lives |
|---|---|---|
| `rotation_genesis` in the chain-identity record | `note_chain_rotation()`, called by `rotate_and_seal()` under the rotation flock | `<key dir>/chain_ids/<sha256(resolved path)>` |
| the link's `mac` (HMAC under the anchor key) | `rotate_and_seal()` | inside the link record, verifiable only with the key |

Neither present → not a rotation. Fail-closed: no key and no record means "no",
never "probably".

Independently, a genesis whose `prev_hash` is neither `initial_prev` nor an
authenticated rotation link is reported as **`unanchored_genesis`** — a LINE-LESS
(current-state) problem, so `audit_chain_intact` blocks the boot regardless of
how long the attacker made the file.

Regression: `tests/security/test_chain_replacement_regression.py`
(`TestLegitimateRotationStaysClean`, `TestForgedRotationLink`) — every verify runs
in a FRESH SUBPROCESS, because `_GENESIS_CACHE` masks an in-place rewrite in the
process that seeded it.

---

## The boot verify is incremental; `voice-audit verify` is not (R4, 2026-09-07)

Verifying an append-only chain costs O(n), and the chain only grows. On the
maintainer install one walk is 315 MB / 588 827 records / ~5.7 s, and
`assert_all()` pays it twice (`audit_chain_intact`, then again after the seam
write, then `audit_chain_history_clean` off the cache).

The chain is append-only, so a prefix that verified once and is **provably**
unchanged need not be re-walked:

| | |
|---|---|
| Witness | `<anchor key dir>/chain_witness/<sha256(resolved chain path)>`, mode 0600, MAC'd under the anchor key |
| Holds | prefix byte length + line count, **SHA-256 of the prefix bytes**, the walk's loop-carried state (chain position, MAC-epoch flags, tail window) and the prefix's LINE-NUMBERED problems |
| Admitted only if | the prefix bytes are re-hashed **in the same call** and match `digest`, the witness MAC verifies under the current anchor key, the recorded path/`initial_prev`/`CORVIN_AUDIT_VERIFY_NO_KEY_OK` match, and the file is at least as long as the prefix |
| Otherwise | **full walk** — missing, unreadable, malformed, wrong version, bad MAC, rotated/absent/refused key, shorter file, any digest mismatch |
| Never memoised | current-state problems (`tail_truncated`, `chain_replaced`, `records_prepended`, `unanchored_genesis`, `mac_stripped_chain`, `anchor_key_insecure_mode`) — recomputed from the out-of-tree anchors after every walk |
| Used by | `tripwire._verify_chain` (the boot path) only, via `audit.verify_audit_incremental()` |
| NOT used by | `security_events.verify_chain()` — `voice-audit verify`, `verify --all` and the daily `corvin-audit-verify.service` stay unconditional full walks |

**Why the digest and not the tail hash.** A tail-hash-at-offset witness catches an
edit that rehashes forward and misses the one that does not — and "does not"
is exactly the `tampered` / `mac_missing` class the verifier exists to find,
because an unrehashed edit leaves every later record's hash untouched. Detecting
an arbitrary silent byte change in an n-byte file cannot cost less than reading n
bytes; what the witness removes is the per-record JSON parse, canonicalisation,
two SHA-256s and HMAC — roughly 30× the cost of the raw read (5.7 s → 0.19 s).

**Threat model.** The witness lives in the anchor-key directory. An attacker who
can write there can also read the anchor key and forge every `mac` in the chain,
so the witness adds no attack surface — it is protected by exactly the boundary
the MAC already depends on. An attacker who can only edit `audit.jsonl` (the one
this whole layer defends against) cannot reach it, and cannot make a stale
witness match a file they changed.

Measured on the maintainer chain: `assert_all()` **13.0 s → 6.3 s** (first boot,
witness written) **→ 0.66 s** (every later boot, fresh process).

Regression: `tests/security/test_chain_witness_regression.py` — a break edited
into the memoised prefix, a truncation, a whole-file replacement, a prepend, a
MAC-stripped record, a forged witness, a witness under a different key and a
witness from another path are all still reported; every check runs in a FRESH
SUBPROCESS and every verdict is compared against an unconditional full walk of
the same file.

---

## Audit events (complete table)

| Event | Severity | Details keys | When |
|---|---|---|---|
| `audit.rotation_link` | INFO | `rotated_segment` | First entry of each fresh live segment; carries `mac` (anchor key) — R3-A1 |
| `audit.rotation_started` | INFO | — | Reserved (not emitted currently) |
| `audit.rotation_failed` | CRITICAL | `rotated_segment`, `reason` | Sealer failure; also raises |
| `audit.segment_sealed` | INFO | `sealed_segment`, `sealer_cmd`, `rotated_size_bytes` | Successful seal |
| `audit.segment_timestamped` | INFO | `sealed_segment`, `tsa_url`, `timestamp_token_path` | TSA token written (opt-in) |
| `audit.tsa_request_failed` | WARNING | `sealed_segment`, `tsa_url`, `reason` | TSA call failed; non-fatal |
| `audit.segment_retired` | INFO | `sealed_segment`, `age_days` | Pre-removal in retention sweep |
| `audit.unseal_requested` | WARNING | `sealed_segment`, `requester`, `sealer_cmd` | Pre-decrypt in `unseal_to_temp` |

**Allow-list** (`_AUDIT_ALLOWED`):
`rotated_segment`, `sealed_segment`, `sealer_cmd`, `rotated_size_bytes`,
`age_days`, `reason`, `requester`, `tsa_url`, `timestamp_token_path`.

Smuggled keys raise `ValueError` at emission time.

**Never** in any `details` field: audit content, encryption key
material, TSA response body, file paths beyond filenames, absolute
paths, or inspector identity beyond the free-form `requester` string.

---

## RFC 3161 TSA — setup and verification

### Why it matters

L16 + L37 sealing provide *internal* tamper-evidence. A regulator or
external auditor still cannot independently prove that a sealed segment
from 2026-Q1 was not fabricated in 2026-Q4. RFC 3161 TSA responses
provide a cryptographically signed third-party attestation, independent
of the Corvin operator.

This closes ADR-001 open item #1 (external timestamping for L37).

### Enabling

```yaml
spec:
  audit:
    encryption_at_rest:
      enabled: true
      recipient: "age1xyz..."
      sealer_cmd: age
      tsa_enabled: true
      tsa_url: "http://tsa.example.com/tsr"
```

The TSA hook fires only when `encryption.enabled=true` — it timestamps
the *sealed* file, not the plaintext.

### TSA choices for EU regulated deployments

| TSA | URL | Notes |
|---|---|---|
| DigiCert | `http://timestamp.digicert.com` | Commercial, widely trusted |
| GlobalSign | `http://timestamp.globalsign.com/tsa/r6advanced1` | Commercial, EU presence |
| Sectigo | `http://timestamp.sectigo.com` | Commercial |
| FreeTSA | `https://freetsa.org/tsr` | Free, acceptable for dev/non-critical use |
| Self-hosted | operator-configured | Full control; Bouncy Castle or OpenSSL |

**Do not** use `api.anthropic.com` or `api.openai.com` as TSA URLs —
the EU_PRODUCTION preset explicitly forbids these in `egress.forbidden_hosts`.

### Verifying a .tsr file

```bash
# Hash of the sealed segment for cross-check:
sha256sum audit.2026-01-15T120000Z.jsonl.age

# Verify the RFC 3161 token against the sealed file:
openssl ts -verify \
  -in  audit.2026-01-15T120000Z.jsonl.age.tsr \
  -data audit.2026-01-15T120000Z.jsonl.age \
  -CAfile tsa-ca.pem
```

Corvin does not ship a built-in TSA verifier. Verification is an
operator-run offline step (audit, incident investigation, regulatory
inspection). `voice-audit verify --include-sealed` (shipped M3.5)
walks all sealed segments; when a `.tsr` file is present alongside a
segment, operators verify it offline using `openssl ts -verify`.

---

## Operator procedures

### Manual rotation + seal

```python
from pathlib import Path
from audit_sealer import (
    rotate_and_seal, AuditPolicy, EncryptionConfig,
    RotationPolicy, RetentionPolicy, make_forge_audit_writer,
)

audit = Path("~/.corvin/tenants/_default/global/audit.jsonl").expanduser()
policy = AuditPolicy(
    rotation=RotationPolicy(max_size_mb=100, max_age_days=30),
    encryption=EncryptionConfig(
        enabled=True, recipient="age1xyz...", sealer_cmd="age",
        tsa_enabled=True, tsa_url="http://tsa.example.com/tsr",
    ),
    retention=RetentionPolicy(retention_years=7),
)
writer = make_forge_audit_writer(audit)
result = rotate_and_seal(audit, policy, audit_writer=writer)
print(f"Sealed: {result.sealed_path}")
print(f"TSA token: {result.timestamp_token_path}")
```

### DPO unseal (legal hold / regulatory inspection)

```python
from audit_sealer import unseal_to_temp, make_forge_audit_writer
from pathlib import Path

sealed = Path("~/.corvin/tenants/_default/global/"
              "audit.2026-01-15T120000Z.jsonl.age").expanduser()
audit  = Path("~/.corvin/tenants/_default/global/audit.jsonl").expanduser()

plaintext = unseal_to_temp(
    sealed,
    identity_file=Path("~/.config/corvin-voice/audit-identity.key").expanduser(),
    audit_writer=make_forge_audit_writer(audit),
    requester="dpo@example.com",
)
# inspect or forward plaintext...
plaintext.unlink()  # caller cleanup — never leave plaintext on disk
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `audit.rotation_failed` CRITICAL | `age`/`gpg` missing or bad recipient | Run `bridge.sh doctor`; check sealer binary is on PATH |
| No `.tsr` file after seal | TSA disabled, or TSA call failed | Check `audit.tsa_request_failed` events; verify `tsa_url` is reachable |
| `ValueError: tsa_enabled=true requires non-empty tsa_url` | Missing `tsa_url` in config | Set `tsa_url` in `encryption_at_rest` block |
| `ValueError: tsa_hash_algo must be 'sha256'` | Unsupported algo set | Only `"sha256"` is currently supported |
| TSA request returns HTTP 400 | Malformed DER request | Do not modify `_build_tsa_request`; file a bug |
| Sealed segment not chmod 444 | Manual chmod or bug | Re-run rotation; chmod 444 is enforced post-seal |
| Chain verification fails after rotation | `prev_hash` mismatch | Check no external tool wrote to `audit.jsonl` between rotation rename and `rotation_link` write |
| `unanchored_genesis` on a live chain rotated BEFORE 2026-09-07 | its `rotation_link` predates the MAC + `rotation_genesis` (R3-A1), so nothing out of tree vouches for it | One-off: `python -c "from forge.security_events import note_chain_rotation; note_chain_rotation(PATH, link_hash=<hash of record 1>)"` run by the operator (the key dir is only writable by them). Sealed segments are unaffected — `voice-audit --include-sealed` passes each segment's real `initial_prev`. |
| `.tsr` verify fails with `openssl ts -verify` | TSA CA cert missing | Provide correct `tsa-ca.pem` for the TSA used |

---

## Must NOT do

- Don't break `voice-audit verify` — each segment must verify standalone after unseal.
- Don't lose the chain link across rotation boundaries.
- Don't store the encryption key alongside sealed segments.
- Don't auto-decrypt on read — `unseal_to_temp` is operator-initiated only.
- Don't put TSA response body, key material, or audit content in `details`.
- Don't make `audit.rotation_failed` advisory; it raises.
- Don't make `audit.tsa_request_failed` CRITICAL — TSA is non-fatal; the seal stands.
- Don't change `_build_tsa_request` DER encoding without testing against a real RFC 3161 TSA.
- Don't make the segment-manifest append (FND-14) chain-breaking — it is best-effort
  (WARNING on failure); the per-record chain + anchor are the authoritative integrity layer.
- Don't gate the manifest continuity check behind `--include-sealed` — it must run on the
  default verify (its whole purpose is to catch segment deletion without decryption).
- Don't store the manifest MAC key alongside the manifest — it reuses the out-of-tree
  ADR-0137 anchor key; co-locating it would let a filesystem attacker re-sign a forged manifest.
- Don't `import anthropic` from `audit_sealer.py` (CI AST lint).
