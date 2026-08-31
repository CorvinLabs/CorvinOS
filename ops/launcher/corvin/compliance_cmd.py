"""`corvin audit …` and `corvin consent …` — headless compliance surface (ADR-0352 P2.4).

Gives a headless operator (``corvinos-run``, no browser Console) CLI access to the
two compliance operations the Console's compliance panel exposes:

  * verifying the hash-chained audit log (GDPR Art. 30/32), and
  * inspecting / revoking per-user consent (GDPR Art. 6/7).

These are READ + REVOKE only, deliberately. The CLI never GRANTS consent — a grant
is a per-user in-band act (the disclosure card + ``/consent`` in the user's own
channel), never something an operator does on a user's behalf — and it never mutates
the audit chain (append-only, hash-chained; only the running platform writes it).

    corvin audit verify [--path P]              verify the audit hash-chain; exit 1 if broken
    corvin audit health [--path P]              boot-style health check (chain ok + record count)
    corvin consent list  CHANNEL CHATKEY        list who has consented in a room
    corvin consent status CHANNEL CHATKEY UID   one user's consent status
    corvin consent revoke CHANNEL CHATKEY UID   revoke one user's consent (GDPR Art. 7(3))

Exit codes: 0 = ok, 1 = a real negative result (chain broken / no such consent),
2 = the operation could not run (module missing, store corrupted, I/O error).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)


def _ensure_bridge_shared_on_path() -> None:
    """Put ``operator/bridges/shared`` on sys.path so the bridge top-level modules
    (``audit``, ``consent``) import even when the CLI runs WITHOUT the bridge/forge
    bootstrap (the `corvin` console-script does not import corvin_console). Mirrors
    the boot tripwire's ``_audit_module`` convention exactly — APPEND, not insert(0),
    because that dir also holds generic top-level names (tests/, templates/) with no
    __init__.py that would shadow another package if placed first on the path."""
    import sys as _sys
    repo_root = Path(__file__).resolve().parents[3]  # ops/launcher/corvin → repo root
    shared = repo_root / "operator" / "bridges" / "shared"
    if shared.is_dir() and str(shared) not in _sys.path:
        _sys.path.append(str(shared))


def _audit_module() -> Any:
    """Import the bridge audit module (the canonical verifier the boot tripwire
    also uses). Raises ImportError if the bridge/forge layer is absent."""
    try:
        import audit  # type: ignore[import-not-found]
        return audit
    except ImportError:
        _ensure_bridge_shared_on_path()
        import audit  # type: ignore[import-not-found]
        return audit


def _consent_module() -> Any:
    try:
        import consent  # type: ignore[import-not-found]
        return consent
    except ImportError:
        _ensure_bridge_shared_on_path()
        import consent  # type: ignore[import-not-found]
        return consent


# ── corvin audit verify ──────────────────────────────────────────────────────

def cmd_audit_verify(args: argparse.Namespace) -> int:
    """Verify the audit hash-chain. Exit 1 (fail) if any record is broken —
    the same fail-closed contract as ``voice-audit verify`` and the boot tripwire."""
    try:
        audit = _audit_module()
    except ImportError as exc:
        _err(f"audit module not available (bridge/forge layer absent): {exc}")
        return 2

    path = Path(args.path) if getattr(args, "path", None) else audit.audit_path()
    if not path.exists():
        # An absent chain is a NEW chain — nothing to verify, not a failure.
        print(f"✓ audit chain not yet created (nothing to verify): {path}")
        return 0
    try:
        ok, problems = audit.verify_audit(path)
    except Exception as exc:
        _err(f"verification could not run: {exc}")
        return 2

    if ok:
        print(f"✓ audit chain verified — no broken records: {path}")
        return 0
    print(f"✗ AUDIT CHAIN BROKEN — {len(problems)} problem record(s): {path}", file=sys.stderr)
    for p in problems[:20]:
        print(f"    {json.dumps(p, sort_keys=True)}", file=sys.stderr)
    if len(problems) > 20:
        print(f"    … and {len(problems) - 20} more", file=sys.stderr)
    return 1


# ── corvin audit health ──────────────────────────────────────────────────────

def cmd_audit_health(args: argparse.Namespace) -> int:
    """Boot-style integrity check: verify the chain and report record count."""
    try:
        audit = _audit_module()
    except ImportError as exc:
        _err(f"audit module not available (bridge/forge layer absent): {exc}")
        return 2

    path = Path(args.path) if getattr(args, "path", None) else audit.audit_path()
    try:
        ok, count = audit.audit_health_check(path)
    except Exception as exc:
        _err(f"health check could not run: {exc}")
        return 2

    status = "healthy" if ok else "DEGRADED"
    print(f"audit chain {status}: {count} problem record(s) — {path}")
    return 0 if ok else 1


# ── corvin consent list ──────────────────────────────────────────────────────

def cmd_consent_list(args: argparse.Namespace) -> int:
    """List all currently-valid consent entries for a room."""
    try:
        consent = _consent_module()
    except ImportError as exc:
        _err(f"consent module not available (bridge layer absent): {exc}")
        return 2
    try:
        entries = consent.list_consents(args.channel, args.chat_key)
    except Exception as exc:
        _err(f"could not read consent store: {exc}")
        return 2

    if not entries:
        print(f"No active consents in {args.channel}/{args.chat_key}")
        return 0
    print(f"Active consents in {args.channel}/{args.chat_key}:\n")
    for uid, entry in entries.items():
        mode = entry.get("mode", "?")
        via = entry.get("granted_via", "?")
        print(f"  {uid}  (mode={mode}, via={via})")
    print()
    return 0


# ── corvin consent status ────────────────────────────────────────────────────

def cmd_consent_status(args: argparse.Namespace) -> int:
    """Show one user's consent status."""
    try:
        consent = _consent_module()
    except ImportError as exc:
        _err(f"consent module not available (bridge layer absent): {exc}")
        return 2
    try:
        st = consent.status(args.channel, args.chat_key, args.uid)
    except Exception as exc:
        _err(f"could not read consent store: {exc}")
        return 2
    print(json.dumps(st, indent=2, sort_keys=True, default=str))
    return 0 if st.get("granted") else 1


# ── corvin consent revoke ────────────────────────────────────────────────────

def cmd_consent_revoke(args: argparse.Namespace) -> int:
    """Revoke one user's consent (GDPR Art. 7(3) — withdrawal). Returns 0 iff
    an entry actually existed and was dropped, 1 if nothing was on file."""
    try:
        consent = _consent_module()
    except ImportError as exc:
        _err(f"consent module not available (bridge layer absent): {exc}")
        return 2
    try:
        existed = consent.revoke(args.channel, args.chat_key, args.uid, via="cli")
    except Exception as exc:
        _err(f"revoke could not run: {exc}")
        return 2
    if existed:
        print(f"✓ Revoked consent for {args.uid} in {args.channel}/{args.chat_key}")
        return 0
    print(f"No consent on file for {args.uid} in {args.channel}/{args.chat_key}")
    return 1


# ── Parser wiring ────────────────────────────────────────────────────────────

def add_parser(main_sub: Any) -> None:
    """Attach the `audit` and `consent` command groups to the main parser."""
    # ── audit ──
    audit_p = main_sub.add_parser("audit", help="Verify the hash-chained audit log (GDPR Art. 30/32)")
    audit_sub = audit_p.add_subparsers(dest="audit_cmd", metavar="subcommand")

    av = audit_sub.add_parser("verify", help="Verify the audit chain; exit 1 if broken")
    av.add_argument("--path", metavar="P", default=None, help="Audit file (default: canonical audit_path())")
    av.set_defaults(audit_cmd="verify", func=cmd_audit_verify)

    ah = audit_sub.add_parser("health", help="Boot-style health check (chain ok + record count)")
    ah.add_argument("--path", metavar="P", default=None, help="Audit file (default: canonical audit_path())")
    ah.set_defaults(audit_cmd="health", func=cmd_audit_health)

    audit_p.set_defaults(func=None)

    # ── consent ──
    consent_p = main_sub.add_parser("consent", help="Inspect / revoke per-user consent (GDPR Art. 6/7)")
    consent_sub = consent_p.add_subparsers(dest="consent_cmd", metavar="subcommand")

    cl = consent_sub.add_parser("list", help="List who has consented in a room")
    cl.add_argument("channel", metavar="CHANNEL", help="Channel id (e.g. discord)")
    cl.add_argument("chat_key", metavar="CHATKEY", help="Chat/room key")
    cl.set_defaults(consent_cmd="list", func=cmd_consent_list)

    cst = consent_sub.add_parser("status", help="One user's consent status")
    cst.add_argument("channel", metavar="CHANNEL", help="Channel id")
    cst.add_argument("chat_key", metavar="CHATKEY", help="Chat/room key")
    cst.add_argument("uid", metavar="UID", help="User id")
    cst.set_defaults(consent_cmd="status", func=cmd_consent_status)

    cr = consent_sub.add_parser("revoke", help="Revoke one user's consent (GDPR Art. 7(3))")
    cr.add_argument("channel", metavar="CHANNEL", help="Channel id")
    cr.add_argument("chat_key", metavar="CHATKEY", help="Chat/room key")
    cr.add_argument("uid", metavar="UID", help="User id")
    cr.set_defaults(consent_cmd="revoke", func=cmd_consent_revoke)

    consent_p.set_defaults(func=None)


def dispatch(args: argparse.Namespace) -> int:
    """Dispatch an audit/consent command via its ``func`` default."""
    func = getattr(args, "func", None)
    if func is None:
        grp = args.command
        print(f"usage: corvin {grp} <subcommand>  (see: corvin {grp} --help)", file=sys.stderr)
        return 2
    return func(args)


__all__ = [
    "add_parser", "dispatch",
    "cmd_audit_verify", "cmd_audit_health",
    "cmd_consent_list", "cmd_consent_status", "cmd_consent_revoke",
]
