"""corvin-id CLI — ADR-0153 M4: CorvinID instance certificate management.

Commands:
  init [--email EMAIL] [--name NAME]  Bind this instance to a CorvinID account.
  show                                 Display current cert + instance_id + pubkey.
  verify                               Check cert validity (expiry + RS256 sig).
  renew                                Renew cert (same as init but updates existing).
  rotate                               Rotate keypair then renew.
  resolve <instance_id>                Deanonymize: look up instance_id in the
                                       identity registry and print result. Emits a
                                       CRITICAL audit event — always.
  export-pubkey                        Print Ed25519 public key in PEM format.
  bind-hardware                        ADR-0145 M3: tether the IBC to this
                                       machine's hardware fingerprint.
  check-hardware                       Compare current hardware fingerprint
                                       against the bound claim (no network call).
  check-revocation [--refresh]         Check the CRL for this instance's IBC.

CI lint: this module MUST NOT ``import anthropic``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


# ── Path bootstrap ────────────────────────────────────────────────────────────
# Ensure operator/bridges/shared is on sys.path when run as a script.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


# ── Lazy imports (keep top-level import list minimal) ─────────────────────────

def _import_instance_identity():
    try:
        from instance_identity import (  # type: ignore[import-not-found]
            bind_instance,
            ensure_instance_key,
            get_ibc,
            get_instance_id,
            get_instance_pubkey_b64,
            instance_cert_path,
            instance_key_path,
            rotate,
            IBCError,
        )
    except ImportError as exc:
        _die(f"Cannot import instance_identity: {exc}")
    return (
        bind_instance, ensure_instance_key, get_ibc, get_instance_id,
        get_instance_pubkey_b64, instance_cert_path, instance_key_path,
        rotate, IBCError,
    )


def _default_corvin_home() -> Path:
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".corvin"


_IDENTITY_REGISTRY_FILE = "identity_registry.json"
_GLOBAL_DIR = "global"


def _die(msg: str, code: int = 1) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


# ── Command implementations ───────────────────────────────────────────────────

def cmd_init(args: argparse.Namespace) -> int:
    """Bind this instance to a CorvinID account via the Corvin Labs IBC endpoint."""
    (bind_instance, ensure_instance_key, get_ibc, get_instance_id,
     get_instance_pubkey_b64, instance_cert_path, instance_key_path,
     rotate_fn, IBCError) = _import_instance_identity()

    email = getattr(args, "email", None) or os.environ.get("CORVIN_ID_EMAIL", "")
    name = getattr(args, "name", None) or os.environ.get("CORVIN_ID_NAME", "")

    # Require a SesT token and license fingerprint from env or flags
    sest_token = getattr(args, "sest_token", None) or os.environ.get("CORVIN_SEST_TOKEN", "")
    license_fp = getattr(args, "license_fp", None) or os.environ.get("CORVIN_LICENSE_FP", "")

    if not sest_token:
        _die(
            "SesT token required for bind. Set CORVIN_SEST_TOKEN env var or "
            "pass --sest-token. Obtain a SesT from your Corvin Labs account."
        )
    if not license_fp:
        _die(
            "License fingerprint required for bind. Set CORVIN_LICENSE_FP env var or "
            "pass --license-fp."
        )

    # Ensure keypair exists before bind
    try:
        ensure_instance_key()
    except IBCError as exc:
        _die(str(exc))

    instance_id = get_instance_id()
    print(f"Binding instance {instance_id} to CorvinID ...")

    try:
        decoded = bind_instance(sest_token, license_fp)
    except IBCError as exc:
        _die(f"IBC bind failed: {exc}")

    # Update identity registry with email/name if provided
    if email or name:
        _update_registry(instance_id, email, name)

    print("CorvinID bind successful.")
    print(f"  instance_id : {instance_id}")
    sub = decoded.get("sub", "")
    jti = decoded.get("jti", "")
    exp = decoded.get("exp", "")
    print(f"  cert sub    : {sub}")
    if jti:
        print(f"  cert jti    : {jti}")
    if exp:
        import datetime
        exp_dt = datetime.datetime.fromtimestamp(exp, tz=datetime.timezone.utc)
        print(f"  cert expiry : {exp_dt.isoformat()}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """Display current cert + instance_id + pubkey."""
    (bind_instance, ensure_instance_key, get_ibc, get_instance_id,
     get_instance_pubkey_b64, instance_cert_path, instance_key_path,
     rotate_fn, IBCError) = _import_instance_identity()

    try:
        instance_id = get_instance_id()
    except Exception as exc:
        _die(f"Cannot read instance_id: {exc}")

    print(f"instance_id : {instance_id}")

    # Pubkey
    try:
        pubkey_b64 = get_instance_pubkey_b64()
        print(f"pubkey_b64  : {pubkey_b64}")
    except IBCError:
        print("pubkey_b64  : (no keypair — run 'corvin-id init' to generate)")

    # Cert
    cert_path = instance_cert_path()
    if not cert_path.exists():
        print("cert        : (absent — run 'corvin-id init' to bind)")
        return 0

    ibc = get_ibc()
    if ibc is None:
        print("cert        : (present but expired or unreadable)")
        return 0

    import datetime
    sub = ibc.get("sub", "")
    jti = ibc.get("jti", "")
    exp = ibc.get("exp", 0)
    exp_dt = datetime.datetime.fromtimestamp(exp, tz=datetime.timezone.utc) if exp else None

    print(f"cert sub    : {sub}")
    if jti:
        print(f"cert jti    : {jti}")
    if exp_dt:
        print(f"cert expiry : {exp_dt.isoformat()}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Check cert validity (expiry + RS256 sig)."""
    (bind_instance, ensure_instance_key, get_ibc, get_instance_id,
     get_instance_pubkey_b64, instance_cert_path, instance_key_path,
     rotate_fn, IBCError) = _import_instance_identity()

    cert_path = instance_cert_path()
    if not cert_path.exists():
        print("FAIL: cert file absent")
        return 1

    # Perform RS256 signature verification
    try:
        try:
            from instance_identity import _verify_ibc_signature  # type: ignore[import-not-found]
        except ImportError:
            _die("Cannot import _verify_ibc_signature from instance_identity")

        ibc_jwt = cert_path.read_text("utf-8").strip()
        _verify_ibc_signature(ibc_jwt)
        print("OK: cert signature valid")
    except IBCError as exc:
        print(f"FAIL: {exc}")
        return 1
    except Exception as exc:
        print(f"FAIL: unexpected error: {exc}")
        return 1

    # Check expiry via get_ibc()
    ibc = get_ibc()
    if ibc is None:
        print("FAIL: cert expired or unreadable")
        return 1

    import datetime
    exp = ibc.get("exp", 0)
    exp_dt = datetime.datetime.fromtimestamp(exp, tz=datetime.timezone.utc) if exp else None
    if exp_dt:
        print(f"OK: cert expires {exp_dt.isoformat()}")
    print("OK: cert valid")
    return 0


def cmd_renew(args: argparse.Namespace) -> int:
    """Renew cert (same flow as init — updates existing cert in-place)."""
    return cmd_init(args)


def cmd_rotate(args: argparse.Namespace) -> int:
    """Rotate Ed25519 keypair then renew the IBC cert."""
    (bind_instance, ensure_instance_key, get_ibc, get_instance_id,
     get_instance_pubkey_b64, instance_cert_path, instance_key_path,
     rotate_fn, IBCError) = _import_instance_identity()

    # Delete existing private key to force generation of a new one
    key_path = instance_key_path()
    if key_path.exists():
        try:
            key_path.unlink()
            pub_path = key_path.parent / "instance_pubkey.pem"
            if pub_path.exists():
                pub_path.unlink()
        except OSError as exc:
            _die(f"Cannot remove existing keypair: {exc}")

    try:
        ensure_instance_key()
    except IBCError as exc:
        _die(f"Keypair generation failed: {exc}")

    print("Keypair rotated. Proceeding with cert renewal ...")
    return cmd_init(args)


def cmd_resolve(args: argparse.Namespace) -> int:
    """Deanonymize an instance_id by looking it up in the identity registry.

    ALWAYS emits identity.resolution_requested (CRITICAL) to the audit chain.
    """
    target_id: str = args.instance_id

    # Emit CRITICAL audit event BEFORE any data is shown — non-negotiable.
    _emit_resolution_audit(target_id)

    registry_path = _default_corvin_home() / _GLOBAL_DIR / _IDENTITY_REGISTRY_FILE
    if not registry_path.exists():
        print(f"identity_registry.json not found at {registry_path}")
        return 1

    try:
        with open(registry_path, "r", encoding="utf-8") as fh:
            data: dict[str, Any] = json.load(fh)
    except Exception as exc:
        _die(f"Cannot read identity registry: {exc}")

    if not isinstance(data, dict):
        _die("identity_registry.json has unexpected format")

    entry = data.get(target_id)
    if entry is None:
        print(f"instance_id {target_id!r} not found in identity registry")
        return 1

    print(f"instance_id   : {target_id}")
    email = entry.get("email", "")
    name = entry.get("name", "")
    registered_at = entry.get("registered_at", "")
    if email:
        print(f"email         : {email}")
    if name:
        print(f"name          : {name}")
    if registered_at:
        print(f"registered_at : {registered_at}")
    return 0


def cmd_bind_hardware(args: argparse.Namespace) -> int:
    """ADR-0145 M3: tether the current IBC to this machine's hardware fingerprint."""
    try:
        from instance_identity import bind_hardware, IBCError  # type: ignore[import-not-found]
    except ImportError as exc:
        _die(f"Cannot import instance_identity: {exc}")

    print("Computing hardware fingerprint and binding to IBC ...")
    try:
        decoded = bind_hardware()
    except IBCError as exc:
        _die(f"Hardware bind failed: {exc}")

    print("Hardware binding successful.")
    hw = decoded.get("hardware_fp", "")
    if hw:
        print(f"  hardware_fp : {hw[:16]}...")
    jti = decoded.get("jti", "")
    if jti:
        print(f"  cert jti    : {jti}")
    return 0


def cmd_check_hardware(args: argparse.Namespace) -> int:
    """Compare the current hardware fingerprint against the bound IBC claim.

    Purely local — no network call, no CLI-level enforcement. Operators
    decide what to do with a mismatch (e.g. a legitimately swapped NIC).
    """
    try:
        from instance_identity import check_hardware_binding  # type: ignore[import-not-found]
    except ImportError as exc:
        _die(f"Cannot import instance_identity: {exc}")

    result = check_hardware_binding()
    if not result["bound"]:
        print("hardware binding : not set (run 'corvin-id bind-hardware' to enable)")
        return 0
    if result["matches"]:
        print("hardware binding : OK (matches bound fingerprint)")
        return 0
    print("hardware binding : MISMATCH")
    print(f"  current fingerprint : {result['current_fp'][:16]}...")
    print(f"  bound fingerprint   : {result['claimed_fp'][:16]}...")
    print("  This can be legitimate (hardware upgrade/dock change) or indicate")
    print("  the IBC was copied to another machine. Investigate before trusting.")
    return 1


def cmd_check_revocation(args: argparse.Namespace) -> int:
    """ADR-0145 M3: check the CRL for this instance's current IBC."""
    try:
        from instance_identity import get_ibc, is_ibc_revoked  # type: ignore[import-not-found]
    except ImportError as exc:
        _die(f"Cannot import instance_identity: {exc}")

    if get_ibc() is None:
        print("No valid local IBC — nothing to check.")
        return 0

    force_refresh = bool(getattr(args, "refresh", False))
    revoked = is_ibc_revoked(force_refresh=force_refresh)
    if revoked:
        print("REVOKED: this instance's IBC has been revoked. Re-run 'corvin-id init' "
              "after resolving the revocation reason with Corvin Labs support.")
        return 1
    print("OK: IBC is not on the revocation list.")
    return 0


def cmd_export_pubkey(args: argparse.Namespace) -> int:
    """Print Ed25519 public key in PEM format."""
    (bind_instance, ensure_instance_key, get_ibc, get_instance_id,
     get_instance_pubkey_b64, instance_cert_path, instance_key_path,
     rotate_fn, IBCError) = _import_instance_identity()

    key_path = instance_key_path()
    if not key_path.exists():
        _die("No keypair found — run 'corvin-id init' to generate one")

    pub_path = key_path.parent / "instance_pubkey.pem"
    if pub_path.exists():
        print(pub_path.read_text("utf-8"), end="")
        return 0

    # Reconstruct from private key via cryptography
    try:
        from cryptography.hazmat.primitives import serialization as _ser
        priv_pem = key_path.read_bytes()
        privkey = _ser.load_pem_private_key(priv_pem, password=None)
        pub_pem = privkey.public_key().public_bytes(
            encoding=_ser.Encoding.PEM,
            format=_ser.PublicFormat.SubjectPublicKeyInfo,
        )
        print(pub_pem.decode("utf-8"), end="")
        return 0
    except ImportError:
        _die("cryptography package not installed — cannot export pubkey PEM")
    except Exception as exc:
        _die(f"Cannot export pubkey: {exc}")
    return 1


# ── Helpers ───────────────────────────────────────────────────────────────────

def _emit_resolution_audit(target_instance_id: str) -> None:
    """Emit identity.resolution_requested CRITICAL audit event.

    This call MUST happen before any data is returned to the caller.
    Never skipped — non-negotiable per ADR-0153 M4 spec.
    """
    try:
        from audit import audit_event  # type: ignore[import-not-found]
        audit_event(
            "identity.resolution_requested",
            severity="CRITICAL",
            details={"target_instance_id_prefix": target_instance_id[:8]},
        )
    except Exception:  # noqa: BLE001
        # Best-effort: if audit is unavailable, print a WARNING to stderr
        # but do NOT suppress the resolution result (operator has already
        # initiated the command; blocking would be worse than logging).
        print(
            "WARNING: audit chain unavailable — identity.resolution_requested "
            "could not be written to the audit chain. Investigate before relying "
            "on this output.",
            file=sys.stderr,
        )


def _update_registry(instance_id: str, email: str, name: str) -> None:
    """Add or update an entry in identity_registry.json (mode 0600)."""
    import datetime
    from _compat_fcntl import fcntl  # POSIX real / Windows no-op shim

    registry_path = _default_corvin_home() / _GLOBAL_DIR / _IDENTITY_REGISTRY_FILE
    registry_path.parent.mkdir(parents=True, exist_ok=True)

    lock_path = registry_path.with_suffix(".lock")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        data: dict[str, Any] = {}
        if registry_path.exists():
            try:
                data = json.loads(registry_path.read_text("utf-8"))
                if not isinstance(data, dict):
                    data = {}
            except Exception:  # noqa: BLE001
                data = {}

        data[instance_id] = {
            "email": email,
            "name": name,
            "registered_at": datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat(timespec="seconds"),
        }
        tmp = registry_path.with_suffix(".tmp")
        with open(tmp, "w", opener=lambda p, f: os.open(p, f, 0o600)) as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, registry_path)
    finally:
        from _compat_fcntl import fcntl as _fcntl  # POSIX real / Windows no-op
        _fcntl.flock(fd, _fcntl.LOCK_UN)
        os.close(fd)
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass


# ── Argument parser ───────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="corvin-id",
        description="CorvinID instance certificate management (ADR-0153 M4)",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    # init / renew share the same flags
    for cmd_name in ("init", "renew"):
        p = sub.add_parser(cmd_name, help=f"{'Bind' if cmd_name == 'init' else 'Renew'} CorvinID cert")
        p.add_argument("--email", default="", help="Operator email (stored in identity registry)")
        p.add_argument("--name", default="", help="Operator name (stored in identity registry)")
        p.add_argument("--sest-token", dest="sest_token", default="", help="SesT token from Corvin Labs")
        p.add_argument("--license-fp", dest="license_fp", default="", help="License fingerprint")

    sub.add_parser("show", help="Display current cert + instance_id + pubkey")
    sub.add_parser("verify", help="Check cert validity (expiry + RS256 sig)")

    # rotate: same flags as init/renew for rebind after key rotation
    p_rotate = sub.add_parser("rotate", help="Rotate keypair then renew cert")
    p_rotate.add_argument("--email", default="")
    p_rotate.add_argument("--name", default="")
    p_rotate.add_argument("--sest-token", dest="sest_token", default="")
    p_rotate.add_argument("--license-fp", dest="license_fp", default="")

    p_resolve = sub.add_parser("resolve", help="Deanonymize an instance_id (CRITICAL audit event emitted)")
    p_resolve.add_argument("instance_id", help="Instance UUID to resolve")

    sub.add_parser("export-pubkey", help="Print Ed25519 public key in PEM format")

    sub.add_parser("bind-hardware", help="Tether the current IBC to this machine's hardware fingerprint")
    sub.add_parser("check-hardware", help="Compare current hardware fingerprint against bound claim (local only)")

    p_check_revocation = sub.add_parser("check-revocation", help="Check the CRL for this instance's IBC")
    p_check_revocation.add_argument(
        "--refresh", action="store_true", help="Bypass the 24h CRL cache and force a network fetch"
    )

    return parser


_COMMAND_MAP = {
    "init": cmd_init,
    "show": cmd_show,
    "verify": cmd_verify,
    "renew": cmd_renew,
    "rotate": cmd_rotate,
    "resolve": cmd_resolve,
    "export-pubkey": cmd_export_pubkey,
    "bind-hardware": cmd_bind_hardware,
    "check-hardware": cmd_check_hardware,
    "check-revocation": cmd_check_revocation,
}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    fn = _COMMAND_MAP.get(args.command)
    if fn is None:
        _die(f"Unknown command: {args.command!r}")
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
