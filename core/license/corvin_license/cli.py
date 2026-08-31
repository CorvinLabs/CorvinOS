"""Operator CLI for license management.

Usage:
  python -m corvin_license.cli show
  python -m corvin_license.cli install /path/to/license.jwt
  python -m corvin_license.cli revoke [--reason <text>]
  python -m corvin_license.cli keygen <priv.pem> <pub.pem>
  python -m corvin_license.cli issue <priv.pem> --customer-id X --tier pro --employee-count-max 250 --seats 50 --days 365 [--flags compliance_reports_premium,sso_wizard]
  python -m corvin_license.cli issue <priv.pem> --customer-id X --tier pro ... --trial-type community --trial-days 30 [--machine-fp <hex>]
  python -m corvin_license.cli issue <priv.pem> --customer-id X --tier business ... --trial-type business --trial-days 90

The `keygen` and `issue` subcommands are Maintainer-side utilities
(used to issue licenses); the others are Operator-side (used to
install or remove an issued license).
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from pathlib import Path

from . import audit as _audit
from . import grace as _grace
from . import tier_flags as _tier_flags
from . import verifier as _verifier


def _cmd_show(args: argparse.Namespace) -> int:
    """Print current license status as JSON."""
    from . import app as _app
    _app._flush_cache()
    print(json.dumps(_app._compute_status(), indent=2, sort_keys=True))
    return 0


def _cmd_install(args: argparse.Namespace) -> int:
    """Copy a license.jwt into the canonical location."""
    src = Path(args.path)
    if not src.exists():
        print(f"error: file not found: {src}", file=sys.stderr)
        return 1

    token = src.read_text(encoding="utf-8").strip()
    if not token:
        print("error: source file is empty", file=sys.stderr)
        return 1

    # Verify BEFORE installing — operator should not be allowed to drop
    # in a bogus token.
    try:
        pubkey = _verifier.load_pubkey()
    except FileNotFoundError:
        print(
            "error: no pinned pubkey found. "
            "Re-run: pip install --force-reinstall corvin-license",
            file=sys.stderr,
        )
        return 2
    except RuntimeError as exc:
        print(f"error: pubkey integrity check failed — {exc}", file=sys.stderr)
        return 2
    try:
        lic = _verifier.verify_token(token, pubkey_pem=pubkey)
    except _verifier.LicenseError as exc:
        print(f"error: token rejected: {exc}", file=sys.stderr)
        return 3

    dest = _verifier.license_file_path()
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Audit BEFORE file mutation (audit-first invariant — GDPR Art. 30).
    fp = _verifier.fingerprint_customer_id(lic.customer_id)
    try:
        _audit.license_activated(
            tier=lic.tier,
            customer_fp=fp,
            valid_until=lic.valid_until,
            issued_at=lic.issued_at,
            employee_count_max=lic.employee_count_max,
            seats=lic.seats,
            feature_flags=list(lic.feature_flags),
        )
    except Exception as exc:
        print(f"warning: audit emit failed: {exc}", file=sys.stderr)

    dest.write_text(token, encoding="utf-8")
    os.chmod(dest, 0o600)

    # Anchor grace state with the new expiry.
    _grace.remember_valid_license(
        valid_until=lic.valid_until,
        customer_fingerprint=fp,
    )

    print(f"installed license for customer={fp} tier={lic.tier} "
          f"valid_until={lic.valid_until}")
    return 0


def _cmd_revoke(args: argparse.Namespace) -> int:
    """Remove the installed license. Drops grace state too."""
    dest = _verifier.license_file_path()
    fp = "unknown"
    if dest.exists():
        try:
            pubkey = _verifier.load_pubkey()
            lic = _verifier.verify_token(
                dest.read_text(encoding="utf-8").strip(),
                pubkey_pem=pubkey,
            )
            fp = _verifier.fingerprint_customer_id(lic.customer_id)
        except Exception:
            pass

    # Audit BEFORE file mutation (audit-first invariant — GDPR Art. 30).
    # An invalid reason code raises LicenseAuditFieldNotAllowed and MUST abort
    # the revoke — swallowing it would let the file mutation proceed without a
    # chain entry, breaking the audit-first invariant. argparse `choices` is the
    # first line of defence (rejects bad reasons pre-mutation); this narrowed
    # except is the structural backstop. Only a genuine emit failure (chain I/O)
    # is warned-and-continued, mirroring _cmd_install.
    try:
        _audit.license_revoked(customer_fp=fp, reason=args.reason or "operator-revoke")
    except _audit.LicenseAuditFieldNotAllowed as exc:
        print(f"error: revoke aborted — {exc}", file=sys.stderr)
        return 4
    except Exception as exc:
        print(f"warning: audit emit failed: {exc}", file=sys.stderr)

    if dest.exists():
        dest.unlink()
    _grace.reset_state()

    print(f"revoked license (customer_fp={fp})")
    return 0


def _cmd_keygen(args: argparse.Namespace) -> int:
    """Generate an RS256 keypair for license signing (Maintainer-side)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    priv_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    priv_path = Path(args.privpath)
    pub_path = Path(args.pubpath)
    priv_path.write_bytes(priv_pem)
    pub_path.write_bytes(pub_pem)
    os.chmod(priv_path, 0o600)
    os.chmod(pub_path, 0o644)

    print(f"wrote private key → {priv_path}")
    print(f"wrote public key  → {pub_path}")
    print(
        f"NEXT STEP: copy {pub_path} into "
        f"core/license/corvin_license/pubkey.pem "
        f"and commit. Keep the private key OFFLINE."
    )
    return 0


def _cmd_issue(args: argparse.Namespace) -> int:
    """Sign a license JWT with a Maintainer private key (Maintainer-side).

    Feature-flag selection contract (ADR-0019):

    * If ``--flags`` is OMITTED, the canonical flag set for ``--tier``
      is used (the common path — Maintainer says "issue an enterprise
      license", every enterprise flag rides along).
    * If ``--flags`` is EXPLICIT, it MUST match the tier's canonical
      flag set exactly. A mismatch fails with a precise diagnostic;
      the operator cannot accidentally ship a pro license with
      enterprise flags, nor a "stripped" enterprise license with one
      flag dropped — both would produce silent commercial drift.
    """
    import jwt as _pyjwt
    priv = Path(args.privpath).read_bytes()
    now = int(time.time())
    exp = now + args.days * 24 * 3600

    # Tier→Flag validation. Default to the canonical set when the
    # caller didn't pass --flags; otherwise enforce parity.
    if args.flags is None or args.flags == "":
        flags = sorted(_tier_flags.flags_for_tier(args.tier))
    else:
        requested = [f.strip() for f in args.flags.split(",") if f.strip()]
        try:
            canonical = _tier_flags.validate_flags_for_tier(args.tier, requested)
        except _tier_flags.TierFlagMismatch as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 4
        flags = sorted(canonical)

    payload = {
        "iss": _verifier.EXPECTED_ISSUER,
        "iat": now,
        "exp": exp,
        "customer_id": args.customer_id,
        "tier": args.tier,
        "employee_count_max": args.employee_count_max,
        "seats": args.seats,
        "feature_flags": flags,
    }

    # Trial token: inject extra claims when --trial-type is set.
    if getattr(args, "trial_type", None):
        trial_type = args.trial_type.lower()
        if trial_type not in ("community", "business"):
            print(f"error: --trial-type must be 'community' or 'business'", file=sys.stderr)
            return 5
        trial_days = getattr(args, "trial_days", None) or (30 if trial_type == "community" else 90)
        trial_exp = now + trial_days * 24 * 3600
        if trial_exp > exp:
            print(
                f"error: trial_expires_at ({trial_exp}) would exceed license exp ({exp}). "
                f"Increase --days or reduce --trial-days.",
                file=sys.stderr,
            )
            return 6
        trial_id = f"t_{secrets.token_hex(8)}"
        payload["trial_type"] = trial_type
        payload["trial_expires_at"] = trial_exp
        payload["trial_id"] = trial_id
        # Community trials bind to a specific machine fingerprint.
        machine_fp = getattr(args, "machine_fp", None)
        if trial_type == "community":
            if not machine_fp:
                print(
                    "error: --machine-fp is required for community trials. "
                    "Run `corvin trial init` on the target machine to get the fingerprint.",
                    file=sys.stderr,
                )
                return 7
            payload["machine_fp"] = machine_fp
        print(
            f"issuing {trial_type} trial: trial_id={trial_id} "
            f"trial_expires_at={trial_exp} ({trial_days} days)",
            file=sys.stderr,
        )

    token = _pyjwt.encode(payload, priv, algorithm="RS256")
    if args.out:
        Path(args.out).write_text(token, encoding="utf-8")
        os.chmod(args.out, 0o600)
        print(f"wrote signed license → {args.out}")
    else:
        print(token)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="corvin-license",
        description="Corvin license-gate CLI",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_show = sub.add_parser("show", help="Print current license status")
    p_show.set_defaults(func=_cmd_show)

    p_install = sub.add_parser("install", help="Install a license.jwt file")
    p_install.add_argument("path", help="Path to a signed license.jwt file")
    p_install.set_defaults(func=_cmd_install)

    p_revoke = sub.add_parser("revoke", help="Remove the installed license")
    # Constrain --reason to the controlled reason codes so argparse rejects an
    # invalid reason BEFORE any file mutation — the audit chain stays
    # metadata-only (no operator free-text / PII) and the audit-first invariant
    # holds. Mirrors audit.license_revoked()'s _VALID_REVOKE_REASONS check.
    p_revoke.add_argument(
        "--reason",
        default="operator-revoke",
        choices=sorted(_audit._VALID_REVOKE_REASONS),
    )
    p_revoke.set_defaults(func=_cmd_revoke)

    p_keygen = sub.add_parser("keygen", help="Generate an RS256 keypair")
    p_keygen.add_argument("privpath")
    p_keygen.add_argument("pubpath")
    p_keygen.set_defaults(func=_cmd_keygen)

    p_issue = sub.add_parser("issue", help="Sign a license JWT (Maintainer)")
    p_issue.add_argument("privpath")
    p_issue.add_argument("--customer-id", required=True)
    p_issue.add_argument("--tier", required=True,
                         choices=["personal", "pro", "member", "business", "enterprise"])
    p_issue.add_argument("--employee-count-max", type=int, required=True)
    p_issue.add_argument("--seats", type=int, required=True)
    p_issue.add_argument("--days", type=int, default=365)
    p_issue.add_argument("--flags", default="")
    p_issue.add_argument("--out", default=None)
    p_issue.add_argument(
        "--trial-type", default=None, choices=["community", "business"],
        help="Issue a trial token (community=30d machine-bound, business=90d deployment-wide)",
    )
    p_issue.add_argument(
        "--trial-days", type=int, default=None,
        help="Override trial duration in days (default: 30 for community, 90 for business)",
    )
    p_issue.add_argument(
        "--machine-fp", default=None,
        help="Machine fingerprint for community trials (from `corvin trial init` on target)",
    )
    p_issue.set_defaults(func=_cmd_issue)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
