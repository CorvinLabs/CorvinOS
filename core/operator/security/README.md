# operator/security/ — Layer Integrity Protocol (ADR-0141)

This directory holds the **signed layer manifest** that pins the SHA-256 of every
mandatory security layer (Tier 1), plus the offline signing tool.

## Files

| File | Role |
|---|---|
| `layer-manifest.json` | Signed manifest pinning each mandatory layer's hash. **Not committed until a release ships one** — see rollout note below. |
| `sign_layer_manifest.py` | Offline signing tool. Corvin Labs runs it at release time. |

## Trust anchor

The manifest is cryptographically signed by Corvin Labs at release time. The signing
key lives only at Corvin Labs and is **never** in this repo. This is why a valid
manifest cannot be produced from a normal checkout.

## Rollout / severity (load-bearing)

The boot check (`self_test._check_layer_integrity`) classifies:

| State | Severity |
|---|---|
| manifest **absent** (no release manifest yet) | `WARNING` — pre-rollout, does not block boot |
| manifest present, **bad signature** | `CRITICAL` — forged / tampered manifest |
| manifest valid, a layer **hash mismatch** | `CRITICAL` — tampered layer |
| everything matches | `INFO` |

A *present* manifest is fully fail-closed; only the not-yet-shipped state is
advisory. Once a release commits a signed manifest, the absent case can no
longer occur on a genuine install.

## Signing (release process)

```bash
python3 operator/security/sign_layer_manifest.py \
    --mandatory-after <unix-ts-for-protocol-v7-deadline>

# verify the result against the committed public key
python3 operator/security/sign_layer_manifest.py --verify
```

Re-sign and re-commit `layer-manifest.json` on **every** release — any change to
a pinned layer file changes its hash and invalidates the old manifest.

**Do NOT** hand-edit `layer-manifest.json`; that invalidates the signature and
the boot check rejects it as `CRITICAL`.
