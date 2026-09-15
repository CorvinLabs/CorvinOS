#!/usr/bin/env python3
"""sign_layer_manifest.py — ADR-0141 Tier 1 offline manifest signing tool.

Corvin Labs runs this at release time with the A2A network *private* key (the
counterpart of ``operator/license/a2a_network_pubkey.pem``, which is never in
the repo). It hashes the current mandatory security-layer files, assembles the
manifest body, RS256-signs it, and writes ``operator/security/layer-manifest.json``.

Usage:
    python3 operator/security/sign_layer_manifest.py \
        --key /secure/offline/a2a_network_privkey.pem \
        [--mandatory-after 1790000000] \
        [--out operator/security/layer-manifest.json] \
        [--issued-at 1781782035]      # defaults to current wall clock

Verify a freshly-written (or committed) manifest against the public key:
    python3 operator/security/sign_layer_manifest.py --verify

This tool is the ONLY supported way to produce a valid manifest. Hand-editing
``layer-manifest.json`` invalidates the signature and the boot check rejects it
as CRITICAL.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# operator/ shadows the stdlib 'operator' module, so add the shared dir to path
# and import layer_integrity directly (mirrors the ops/launcher/*_entry shims).
_SHARED = Path(__file__).resolve().parents[1] / "bridges" / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

import layer_integrity as li  # type: ignore  # noqa: E402


def _cmd_sign(args: argparse.Namespace) -> int:
    key_path = Path(args.key)
    if not key_path.is_file():
        print(f"error: private key not found: {key_path}", file=sys.stderr)
        return 2
    issued_at = int(args.issued_at) if args.issued_at is not None else int(time.time())
    body = li.build_manifest_body(
        issued_at=issued_at,
        mandatory_after=args.mandatory_after,
    )
    sig = li.sign_manifest(body, key_path.read_bytes())
    manifest = {**body, "manifest_sig": sig}

    out = Path(args.out) if args.out else li.manifest_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", "utf-8")

    # Self-verify against the public key before declaring success.
    if not li.verify_manifest_signature(manifest):
        print("error: freshly-signed manifest fails public-key verification "
              "(key mismatch?)", file=sys.stderr)
        return 3
    print(f"wrote signed manifest: {out}")
    print(f"  layers:          {len(manifest['mandatory_layers'])}")
    print(f"  issued_at:       {issued_at}")
    print(f"  mandatory_after: {manifest['mandatory_after']}")
    print(f"  integrity hash:  {li.manifest_layer_integrity_hash(manifest)}")
    return 0


def _cmd_verify(_args: argparse.Namespace) -> int:
    result = li.verify_integrity()
    print(f"status: {result.status.value} — {result.detail}")
    if result.mismatched:
        print(f"  mismatched layers: {result.mismatched}")
    return 0 if result.ok else 1


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description="ADR-0141 layer-manifest signing tool")
    sub = ap.add_subparsers(dest="cmd")

    ap.add_argument("--verify", action="store_true",
                    help="verify the on-disk manifest instead of signing")
    ap.add_argument("--key", help="path to the RS256 private key PEM")
    ap.add_argument("--out", help="output manifest path (default: operator/security/layer-manifest.json)")
    ap.add_argument("--mandatory-after", type=int, default=None,
                    help="unix ts after which Protocol v7 attestation is mandatory")
    ap.add_argument("--issued-at", type=int, default=None,
                    help="override issued_at unix ts (default: now)")
    args = ap.parse_args(argv)

    if args.verify:
        return _cmd_verify(args)
    if not args.key:
        ap.error("--key is required to sign (or pass --verify)")
    return _cmd_sign(args)


if __name__ == "__main__":
    raise SystemExit(main())
