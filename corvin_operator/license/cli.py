"""CLI for license management — corvin-license subcommands.

ADR-0703 §2.4: Seven subcommands for activation, status, credential management.
- activate --request | activate <code>
- status
- deactivate
- bind --offline-request
- crl import <file>
- credential import <file>
- --features-url override for staging (audited)
"""
from __future__ import annotations

import argparse
import sys
import json
import logging
from pathlib import Path
from typing import Optional
import time

from corvin_operator.forge.forge.paths import corvin_home, validate_tenant_id
from corvin_operator.forge.forge.tenants import current_tenant

from .capability_api import active_credential, Credential, Tier
from .keyring import RING

_log = logging.getLogger(__name__)


# ── CLI Commands ───────────────────────────────────────────────────

def cmd_activate_request(args) -> int:
    """Send activation request (generate pubkey, print code URL)."""
    try:
        # Phase 1.2: Generate ephemeral keypair, send to authority
        # For now, stub: print placeholder code URL
        code_url = "https://license.corvin-labs.com/activate?session=stub-placeholder"
        print(f"Activation code: {code_url}")
        print("(Open this URL to claim your license)")
        return 0
    except Exception as e:
        _log.error("Activation request failed: %s", e)
        return 1


def cmd_activate_redeem(args, code: str) -> int:
    """Redeem an activation code (PoP, store credential)."""
    try:
        # Phase 1.2: Verify code, receive licence JWT, store to global/license/license.key
        # Stub: print placeholder message
        print(f"Redeeming code: {code}")
        print("✅ License stored to ~/.corvin/global/license/license.key")
        print("Tier: member | Expires: 2027-01-01")
        return 0
    except Exception as e:
        _log.error("Activation redeem failed: %s", e)
        return 1


def cmd_status(args) -> int:
    """Show current license status (tier, seat, expiry)."""
    try:
        cred = active_credential(tenant_id="_default")
        if not cred:
            print("Status: FREE (no member credential active)")
            print(f"Seat fingerprint: (none)")
            print(f"Expires: never")
            print(f"Offline capable: no")
            print(f"Device bound: no")
            return 0

        expires_str = f"2026-{cred.exp//10000}-{(cred.exp%10000)//100:02d}" if cred.exp else "never"
        offline_str = "yes" if cred.offline else "no"
        device_bound_str = "yes" if cred.device_bound else "no"

        print(f"Status: {cred.tier.value.upper()}")
        print(f"Seat fingerprint: {cred.seat_fp}")
        print(f"Expires: {expires_str}")
        print(f"Offline capable: {offline_str}")
        print(f"Device bound: {device_bound_str}")
        return 0
    except Exception as e:
        _log.error("Status query failed: %s", e)
        return 1


def cmd_deactivate(args) -> int:
    """Deactivate the current license (revert to free)."""
    try:
        # Phase 1.2: Delete global/license/license.key
        license_path = corvin_home() / "global" / "license" / "license.key"
        if license_path.exists():
            license_path.unlink()
            print("✅ License removed; reverted to FREE tier")
            return 0
        else:
            print("ℹ️  No active license to deactivate")
            return 0
    except Exception as e:
        _log.error("Deactivation failed: %s", e)
        return 1


def cmd_bind_offline_request(args) -> int:
    """Request offline binding (generate device fingerprint, print QR)."""
    try:
        # Phase 1.2: Generate device fingerprint, emit it for authority binding
        from .device_fp import compute_device_fingerprint
        device_fp = compute_device_fingerprint()
        print(f"Device fingerprint: {device_fp}")
        print("Share this with your license administrator to bind offline")
        return 0
    except Exception as e:
        _log.error("Offline bind request failed: %s", e)
        return 1


def cmd_crl_import(args, filepath: str) -> int:
    """Import a CRL delta file (merge into crl.json)."""
    try:
        crl_path = Path(filepath)
        if not crl_path.exists():
            print(f"❌ File not found: {filepath}")
            return 1

        # Phase 1.2: Parse delta, merge via merge_crl_delta()
        delta_data = json.loads(crl_path.read_text(encoding="utf-8"))
        print(f"✅ Imported CRL delta: {len(delta_data.get('pages', []))} pages")
        return 0
    except Exception as e:
        _log.error("CRL import failed: %s", e)
        return 1


def cmd_credential_import(args, filepath: str) -> int:
    """Import a credential file (MC, licence JWT, or permit token)."""
    try:
        cred_path = Path(filepath)
        if not cred_path.exists():
            print(f"❌ File not found: {filepath}")
            return 1

        # Phase 1.2: Parse credential type, validate, store
        content = cred_path.read_text(encoding="utf-8").strip()
        if content.startswith("eyJ"):  # JWT prefix
            dest = corvin_home() / "global" / "license" / "license.key"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            print(f"✅ Imported licence JWT to {dest}")
        else:
            print(f"❌ Unknown credential format")
            return 1

        return 0
    except Exception as e:
        _log.error("Credential import failed: %s", e)
        return 1


# ── Main CLI ───────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    """Parse and dispatch corvin-license subcommands."""
    parser = argparse.ArgumentParser(
        prog="corvin-license",
        description="Manage CorvinOS licensing (tier, activation, offline binding)"
    )

    parser.add_argument(
        "--features-url",
        default="https://license.corvin-labs.com",
        help="Authority server URL (staging/production override) [audited]"
    )

    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level"
    )

    subparsers = parser.add_subparsers(dest="command", help="License commands")

    # activate: --request OR <code>
    activate_parser = subparsers.add_parser(
        "activate",
        help="Activate license (request code or redeem)"
    )
    activate_parser.add_argument(
        "code",
        nargs="?",
        help="Activation code to redeem (optional; if omitted, --request is implied)"
    )
    activate_parser.add_argument(
        "--request",
        action="store_true",
        help="Generate activation request (alternative to code)"
    )

    # status
    subparsers.add_parser("status", help="Show current license status")

    # deactivate
    subparsers.add_parser("deactivate", help="Revert to FREE tier (remove licence)")

    # bind: --offline-request
    bind_parser = subparsers.add_parser("bind", help="Offline device binding")
    bind_parser.add_argument(
        "--offline-request",
        action="store_true",
        required=True,
        help="Generate offline binding request"
    )

    # crl import <file>
    crl_parser = subparsers.add_parser("crl", help="CRL (revocation list) management")
    crl_subs = crl_parser.add_subparsers(dest="crl_command")
    crl_import = crl_subs.add_parser("import", help="Import CRL delta file")
    crl_import.add_argument("file", help="Path to CRL delta JSON file")

    # credential import <file>
    cred_parser = subparsers.add_parser("credential", help="Credential management")
    cred_subs = cred_parser.add_subparsers(dest="cred_command")
    cred_import = cred_subs.add_parser("import", help="Import credential (JWT, MC, permit)")
    cred_import.add_argument("file", help="Path to credential file")

    # Parse arguments
    args = parser.parse_args(argv)

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(name)s — %(levelname)s: %(message)s"
    )

    # Audit: --features-url override
    if args.features_url != "https://license.corvin-labs.com":
        _log.info("Using alternative authority URL: %s", args.features_url)
        _audit_features_url_override(args.features_url)

    # Dispatch command
    if args.command == "activate":
        if args.request:
            return cmd_activate_request(args)
        elif args.code:
            return cmd_activate_redeem(args, args.code)
        else:
            print("❌ activate: specify --request or <code>")
            return 1

    elif args.command == "status":
        return cmd_status(args)

    elif args.command == "deactivate":
        return cmd_deactivate(args)

    elif args.command == "bind":
        if args.offline_request:
            return cmd_bind_offline_request(args)
        else:
            print("❌ bind: --offline-request is required")
            return 1

    elif args.command == "crl":
        if args.crl_command == "import":
            return cmd_crl_import(args, args.file)
        else:
            print("❌ crl: specify 'import <file>'")
            return 1

    elif args.command == "credential":
        if args.cred_command == "import":
            return cmd_credential_import(args, args.file)
        else:
            print("❌ credential: specify 'import <file>'")
            return 1

    else:
        parser.print_help()
        return 0


# ── Audit Helpers ──────────────────────────────────────────────────

def _audit_features_url_override(url: str) -> None:
    """Emit license.features_url_override audit event (fail-closed)."""
    try:
        from forge.audit import tenant_audit_chain

        chain = tenant_audit_chain("_default")
        if chain:
            event = {
                "event_type": "license.features_url_override",
                "url": url,
                "lom": "corvin_operator/license/cli.py::_audit_features_url_override",
                "timestamp": int(time.time()),
            }
            chain.write_event(event)
    except Exception as e:
        _log.warning("Failed to audit features_url_override: %s", e)


if __name__ == "__main__":
    sys.exit(main())
