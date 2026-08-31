#!/usr/bin/env python3
"""Implementation behind the ``corvin-instance-id`` CLI wrapper.

Reads (and on first run, creates) ``<corvin_home>/global/instance_id.json``.
The file is mode 0600 and contains a stable UUID4 plus an optional
operator-friendly label.

ADR-0078 Phase 1 additions:
  register  — register this instance with CorvinOS and obtain an IAC
  status    — show instance identity + attestation trust level
  rotate    — rotate instance_id (breaking operation, requires confirmation)
  ca-init   — generate a new CA keypair (CA operator use only)

Exit codes:
  0 — success
  1 — runtime error
  2 — bad arguments
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Make the shared module importable when invoked via the bash wrapper
# from inside the repo (no install step required).
_HERE = Path(__file__).resolve()
_REPO_SHARED = _HERE.parents[2] / "bridges" / "shared"
if _REPO_SHARED.is_dir() and str(_REPO_SHARED) not in sys.path:
    sys.path.insert(0, str(_REPO_SHARED))

import instance_identity  # type: ignore[import-not-found]
import instance_attestation as att  # type: ignore[import-not-found]

# Default CorvinOS registration API endpoint.
_DEFAULT_REGISTRATION_URL = os.environ.get(
    "CORVIN_REGISTRATION_URL",
    "https://api.corvin.io/v1/instance/register",
)


# ── Existing commands ─────────────────────────────────────────────────────

def _cmd_show(_: argparse.Namespace) -> int:
    meta = instance_identity.instance_id_metadata()
    print(json.dumps(meta, sort_keys=True, indent=2))
    return 0


def _cmd_print(_: argparse.Namespace) -> int:
    print(instance_identity.get_instance_id())
    return 0


def _cmd_label(args: argparse.Namespace) -> int:
    try:
        meta = instance_identity.set_label(args.value)
    except (ValueError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(meta, sort_keys=True, indent=2))
    return 0


def _cmd_path(_: argparse.Namespace) -> int:
    print(instance_identity.instance_id_path())
    return 0


# ── ADR-0078 Phase 1: status ──────────────────────────────────────────────

def _cmd_status(_: argparse.Namespace) -> int:
    """Show identity + current attestation trust level."""
    instance_id = instance_identity.get_instance_id()
    iac = att.load_attestation()
    ca_pub = att.get_ca_pubkey_bytes()
    trust = att.effective_trust_level(iac, ca_pub)

    out: dict = {
        "instance_id": instance_id,
        "trust_level": att.trust_level_name(trust),
        "ca_configured": ca_pub is not None,
    }

    if iac is not None:
        cert = iac.get("cert", {})
        import time
        out["tier"]             = cert.get("tier", "unknown")
        out["corvin_version"]   = cert.get("corvin_version", "unknown")
        out["registered_at"]    = cert.get("registered_at")
        out["expires_at"]       = cert.get("expires_at")
        out["expired"]          = float(cert.get("expires_at", 0)) < time.time()
        out["ca_fingerprint"]   = cert.get("ca_pubkey_fingerprint")
        out["attestation_path"] = str(att.attestation_path())
    else:
        out["attestation_path"] = str(att.attestation_path())
        out["attestation"]      = "not registered"

    print(json.dumps(out, sort_keys=True, indent=2))
    return 0


# ── ADR-0078 Phase 1: register ────────────────────────────────────────────

def _cmd_register(args: argparse.Namespace) -> int:
    """Register this instance with CorvinOS and store the resulting IAC.

    Two modes:
      --api-key KEY      Call the CorvinOS registration API (production).
      --self-signed      Issue a local dev/test attestation (no CA required).

    The --self-signed flag generates a throwaway CA keypair and issues a
    community-tier IAC signed by it. This is NOT production-grade — use it
    only in development or CI environments. The IAC is clearly marked as
    self-signed via a special CA fingerprint prefix.
    """
    instance_id = instance_identity.get_instance_id()

    if args.self_signed:
        return _register_self_signed(instance_id, args)

    if not args.api_key:
        print("error: provide --api-key or --self-signed", file=sys.stderr)
        return 2

    return _register_via_api(instance_id, args)


def _register_self_signed(instance_id: str, args: argparse.Namespace) -> int:
    """Issue a self-signed dev/test attestation (no CorvinCA required)."""
    print("WARNING: --self-signed creates a development attestation only. "
          "It is not recognized by the CorvinOS network and should not be "
          "used in production.", file=sys.stderr)

    try:
        priv_hex, pub_hex = att.generate_ca_keypair()
    except ImportError:
        print("error: 'cryptography' package required for attestation "
              "(pip install cryptography)", file=sys.stderr)
        return 1

    import platform
    corvin_version = os.environ.get("CORVIN_VERSION", "dev")
    iac = att.sign_attestation(
        instance_id=instance_id,
        tier="community",
        ca_privkey_hex=priv_hex,
        corvin_version=corvin_version,
        ttl_days=getattr(args, "ttl_days", 365),
    )

    # Tag as self-signed so tooling can distinguish from CA-signed IACs.
    iac["self_signed"] = True
    iac["self_signed_pubkey_hex"] = pub_hex

    att.save_attestation(iac)
    cert = iac["cert"]
    print(json.dumps({
        "status":       "registered (self-signed)",
        "instance_id":  instance_id,
        "tier":         cert["tier"],
        "expires_at":   cert["expires_at"],
        "warning":      "self-signed — not recognized by CorvinOS network",
    }, indent=2))
    return 0


def _register_via_api(instance_id: str, args: argparse.Namespace) -> int:
    """Call the CorvinOS registration API and store the returned IAC."""
    import urllib.error
    import urllib.request

    url = getattr(args, "url", None) or _DEFAULT_REGISTRATION_URL
    api_key = args.api_key
    corvin_version = os.environ.get("CORVIN_VERSION", "dev")

    payload = json.dumps({
        "instance_id":    instance_id,
        "corvin_version": corvin_version,
        "tier":           getattr(args, "tier", "community"),
    }).encode()

    req = urllib.request.Request(
        url, data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        print(f"error: registration API returned HTTP {exc.code}: {exc.reason}",
              file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"error: could not reach registration API at {url}: {exc.reason}",
              file=sys.stderr)
        print("Tip: set CORVIN_REGISTRATION_URL or use --self-signed for "
              "development.", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # API returns {cert: {...}, sig: "..."}
    if "cert" not in body or "sig" not in body:
        print(f"error: unexpected API response: {body}", file=sys.stderr)
        return 1

    ca_pub = att.get_ca_pubkey_bytes()
    trust = att.verify_attestation(body, ca_pub)
    att.save_attestation(body)

    cert = body["cert"]
    print(json.dumps({
        "status":      "registered",
        "instance_id": instance_id,
        "tier":        cert.get("tier"),
        "trust_level": att.trust_level_name(trust),
        "expires_at":  cert.get("expires_at"),
        "path":        str(att.attestation_path()),
    }, indent=2))
    return 0


# ── ADR-0078 Phase 1: ca-init ─────────────────────────────────────────────

def _cmd_ca_init(args: argparse.Namespace) -> int:
    """Generate a new CA keypair.  CA OPERATOR USE ONLY.

    Prints the keypair as JSON. Store the private key in an HSM; distribute
    the public key via CORVIN_CA_PUBKEY_HEX or CORVIN_CA_PUBKEY_PATH.

    WARNING: This is a one-time operation. Generating a new CA keypair
    invalidates all previously issued IACs.
    """
    if not getattr(args, "yes", False):
        print("This generates a new CorvinCA keypair, which invalidates all "
              "existing attestation certificates.\nPass --yes to confirm.",
              file=sys.stderr)
        return 2

    try:
        priv_hex, pub_hex = att.generate_ca_keypair()
    except ImportError:
        print("error: 'cryptography' package required", file=sys.stderr)
        return 1

    out = {
        "ca_privkey_hex": priv_hex,
        "ca_pubkey_hex": pub_hex,
        "warning": "Store ca_privkey_hex in an HSM. Never commit it to version control.",
        "usage": {
            "set_pubkey": f"export CORVIN_CA_PUBKEY_HEX={pub_hex}",
            "sign_iac":   "corvin-instance-id register --api-key ... (server-side)",
        },
    }

    out_path = getattr(args, "out", None)
    if out_path:
        Path(out_path).write_text(json.dumps(out, indent=2) + "\n")
        os.chmod(out_path, 0o600)
        print(f"keypair written to {out_path} (mode 0600)")
    else:
        print(json.dumps(out, indent=2))
    return 0


# ── main ──────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="corvin-instance-id",
        description=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("show",  help="print full JSON record")
    subparsers.add_parser("print", help="print only the UUID (default)")
    subparsers.add_parser("status", help="show identity + attestation trust level")

    p_label = subparsers.add_parser("label", help="set the human-readable label")
    p_label.add_argument("value", help="new label (≤ 64 chars, no control chars)")

    subparsers.add_parser("path", help="print the resolved JSON file path")

    # ADR-0078 Phase 1: register
    p_reg = subparsers.add_parser(
        "register",
        help="register with CorvinOS and obtain an Instance Attestation Certificate",
    )
    p_reg.add_argument("--api-key", metavar="KEY",
                       help="CorvinOS account API key")
    p_reg.add_argument("--url", metavar="URL",
                       help=f"registration URL (default: {_DEFAULT_REGISTRATION_URL})")
    p_reg.add_argument("--self-signed", action="store_true",
                       help="issue a local dev/test attestation (no CA required)")
    p_reg.add_argument("--ttl-days", type=int, default=365, metavar="N",
                       help="certificate TTL in days (default: 365)")
    p_reg.add_argument("--tier", default="community",
                       choices=["community", "verified", "enterprise"],
                       help="requested trust tier (default: community)")

    # ADR-0078 Phase 1: ca-init (CA operator only)
    p_ca = subparsers.add_parser(
        "ca-init",
        help="[CA OPERATOR ONLY] generate a new CorvinCA keypair",
    )
    p_ca.add_argument("--yes", action="store_true",
                      help="confirm keypair generation (required)")
    p_ca.add_argument("--out", metavar="FILE",
                      help="write keypair JSON to FILE (mode 0600) instead of stdout")

    args = parser.parse_args(argv)

    handler = {
        None:        _cmd_print,
        "print":     _cmd_print,
        "show":      _cmd_show,
        "label":     _cmd_label,
        "path":      _cmd_path,
        "status":    _cmd_status,
        "register":  _cmd_register,
        "ca-init":   _cmd_ca_init,
    }.get(args.command, _cmd_print)
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
