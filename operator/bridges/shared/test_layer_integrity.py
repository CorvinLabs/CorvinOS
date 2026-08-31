"""Per-subtask E2E — ADR-0141 Tier 1: layer manifest + boot verification.

Exercises the full verify_integrity() path against a fully-synthetic repo root
(controlled layer files + a locally-generated RS256 keypair), plus the Tier-2
aggregate-hash consistency property and the no-brick MANIFEST_ABSENT behaviour
on the real checkout.

Runnable standalone: ``python3 operator/bridges/shared/test_layer_integrity.py``
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(REPO / "operator" / "forge"))

PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    suffix = f" — {detail}" if detail else ""
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{suffix}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


import layer_integrity as li  # noqa: E402


def _gen_keypair() -> tuple[bytes, bytes]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv, pub


def _build_synthetic_root(tmp: Path) -> None:
    """Create a layer file at every MANDATORY_LAYER_FILES path under tmp."""
    for i, rel in enumerate(li.MANDATORY_LAYER_FILES.values()):
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# synthetic layer file {i}\n")


def test_aggregate_consistency_real_repo() -> None:
    # The two aggregate paths must agree for an honest node: hashing local files
    # vs folding the manifest's pinned hashes (Tier-2 sender/receiver match).
    body = li.build_manifest_body(issued_at=1781782035, root=REPO)
    local_agg = li.compute_layer_integrity_hash(root=REPO)
    manifest_agg = li.manifest_layer_integrity_hash(body)
    t("aggregate(local files) == aggregate(manifest hashes)",
      local_agg == manifest_agg and local_agg.startswith("sha256:"),
      detail=local_agg[:24])


def test_real_repo_not_critical() -> None:
    # ADR-0141 rollout: a signed manifest IS committed -> VERIFIED. If a checkout
    # has none yet (pre-rollout), the status is ABSENT. Either way an honest repo
    # must never brick boot — that is the load-bearing no-brick invariant.
    res = li.verify_integrity(root=REPO)
    t("real repo: manifest present+valid (VERIFIED) or pre-rollout (ABSENT)",
      res.status in (li.IntegrityStatus.VERIFIED, li.IntegrityStatus.MANIFEST_ABSENT),
      detail=res.status.value)
    t("honest repo never bricks boot (not critical)", not res.is_critical)


def test_full_sign_verify_roundtrip() -> None:
    priv, pub = _gen_keypair()
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _build_synthetic_root(root)
        (root / li.PUBKEY_REL_PATH).parent.mkdir(parents=True, exist_ok=True)
        (root / li.PUBKEY_REL_PATH).write_bytes(pub)

        body = li.build_manifest_body(issued_at=1781782035, mandatory_after=1790000000,
                                      root=root)
        sig = li.sign_manifest(body, priv)
        manifest = {**body, "manifest_sig": sig}
        mp = li.manifest_path(root)
        mp.parent.mkdir(parents=True, exist_ok=True)
        import json
        mp.write_text(json.dumps(manifest, sort_keys=True, indent=2))

        # 1. clean state -> VERIFIED
        res = li.verify_integrity(root=root)
        t("synthetic root verifies clean", res.status == li.IntegrityStatus.VERIFIED,
          detail=res.detail)

        # 2. tamper a layer file -> MISMATCH (CRITICAL)
        victim = root / next(iter(li.MANDATORY_LAYER_FILES.values()))
        victim.write_text("# TAMPERED\n")
        res2 = li.verify_integrity(root=root)
        t("tampered layer -> MISMATCH", res2.status == li.IntegrityStatus.MISMATCH)
        t("MISMATCH is critical", res2.is_critical)
        t("mismatch names the victim", len(res2.mismatched) == 1)

        # restore the layer, corrupt the signature -> MANIFEST_INVALID (CRITICAL)
        _build_synthetic_root(root)
        bad = {**manifest, "manifest_sig": "AAAA" + sig[4:]}
        mp.write_text(json.dumps(bad, sort_keys=True, indent=2))
        res3 = li.verify_integrity(root=root)
        t("corrupt signature -> MANIFEST_INVALID",
          res3.status == li.IntegrityStatus.MANIFEST_INVALID)
        t("MANIFEST_INVALID is critical", res3.is_critical)


def test_signature_covers_layer_hashes() -> None:
    # Flipping a pinned hash after signing must break verification (the hash map
    # is inside the signed payload).
    priv, pub = _gen_keypair()
    body = li.build_manifest_body(issued_at=1, root=REPO)
    sig = li.sign_manifest(body, priv)
    manifest = {**body, "manifest_sig": sig}
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / li.PUBKEY_REL_PATH).parent.mkdir(parents=True, exist_ok=True)
        (root / li.PUBKEY_REL_PATH).write_bytes(pub)
        t("untampered manifest verifies", li.verify_manifest_signature(manifest, root))
        tampered = {**manifest}
        tampered["mandatory_layers"] = {**body["mandatory_layers"], "audit": "sha256:deadbeef"}
        t("hash-tampered manifest fails", not li.verify_manifest_signature(tampered, root))


def main() -> int:
    test_aggregate_consistency_real_repo()
    test_real_repo_not_critical()
    test_full_sign_verify_roundtrip()
    test_signature_covers_layer_hashes()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
