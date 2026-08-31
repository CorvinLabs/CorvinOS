"""Operator CLI for the Corvin Gateway.

Usage::

    python -m corvin_gateway.cli webhook secret set  <tenant_id> <ref>
    python -m corvin_gateway.cli webhook secret list <tenant_id>
    python -m corvin_gateway.cli tenant init         <tenant_id>
    python -m corvin_gateway.cli tenant show         <tenant_id>
    python -m corvin_gateway.cli package build|verify|install ...
    python -m corvin_gateway.cli plugin install     <path> [--tenant ID]

Token auth has been removed; static atlr_* tokens are no longer issued.
For local deployments the loopback binding is the security boundary.
OIDC/Google OAuth will be wired in the cloud deployment phase.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

def _webhooks():
    from . import webhooks  # noqa: F401 — re-exported via the closure
    return webhooks


def _tenant_config():
    from . import tenant_config  # noqa: F401
    return tenant_config


def _packaging():
    from . import packaging  # noqa: F401
    return packaging


# ── Webhook-secret subcommands ────────────────────────────────────────


def _cmd_secret_set(args: argparse.Namespace) -> int:
    value = args.value
    if value is None:
        # stdin path — preferred so the secret never lands in shell
        # history. Strips the trailing newline that `echo` adds.
        value = sys.stdin.read().rstrip("\n")
    if not value:
        print("Refusing to store an empty secret.", file=sys.stderr)
        return 1
    store = _webhooks().WebhookSecretStore()
    store.set_secret(args.tenant_id, args.ref, value)
    print(
        f"Webhook secret {args.ref!r} stored for tenant {args.tenant_id!r}."
    )
    return 0


def _cmd_secret_list(args: argparse.Namespace) -> int:
    store = _webhooks().WebhookSecretStore()
    entries = store.list_secrets(args.tenant_id)
    if not entries:
        print(f"(no webhook secrets for tenant {args.tenant_id!r})")
        return 0
    print(f"{'REF':<32} CREATED")
    for e in entries:
        print(f"{e['ref']:<32} {e.get('created_at') or ''}")
    return 0


def _cmd_secret_revoke(args: argparse.Namespace) -> int:
    store = _webhooks().WebhookSecretStore()
    if store.delete_secret(args.tenant_id, args.ref):
        print(f"Deleted webhook secret {args.ref!r}.")
        return 0
    print(
        f"No secret {args.ref!r} for tenant {args.tenant_id!r}.",
        file=sys.stderr,
    )
    return 1


# ── Tenant config subcommands (Phase 3.1) ─────────────────────────────


def _cmd_tenant_init(args: argparse.Namespace) -> int:
    tc = _tenant_config()
    allowed = args.allowed_engines or None
    forbid = args.forbid_engines or None
    if allowed:
        known = tc.known_engine_names()
        unknown = [e for e in allowed if e not in known]
        if unknown:
            print(
                f"Warning: unknown engine name(s) in --allowed-engines: "
                f"{unknown}. Known: {sorted(known)}",
                file=sys.stderr,
            )
    if forbid:
        known = tc.known_engine_names()
        unknown = [e for e in forbid if e not in known]
        if unknown:
            print(
                f"Warning: unknown engine name(s) in --forbid-engines: "
                f"{unknown}. Known: {sorted(known)}",
                file=sys.stderr,
            )
    config = tc.init(
        args.tenant_id,
        display_name=args.display_name or "",
        zone=args.zone,
        allowed_engines=allowed,
        forbid_engines=forbid,
    )
    print(
        f"Tenant config written for {config.metadata.id!r} "
        f"(display_name={config.metadata.display_name!r}, "
        f"zone={config.spec.data_residency.zone!r})."
    )
    return 0


def _cmd_tenant_show(args: argparse.Namespace) -> int:
    tc = _tenant_config()
    try:
        config = tc.load(args.tenant_id)
    except tc.TenantConfigMalformed as exc:
        print(f"Cannot load tenant {args.tenant_id!r}: {exc}", file=sys.stderr)
        return 1
    import yaml as _yaml
    print(_yaml.safe_dump(
        config.model_dump(mode="python"),
        sort_keys=False, allow_unicode=True,
    ).rstrip())
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="corvin-gateway",
        description="Operator CLI for the Corvin Gateway (ADR-0007 Phase 2).",
    )
    sub = p.add_subparsers(dest="group", required=True)

    # ── Webhook secrets (Phase 2.4) ────────────────────────────────
    wh_p = sub.add_parser(
        "webhook", help="Manage outbound-webhook HMAC secrets for a tenant.",
    )
    wh_sub = wh_p.add_subparsers(dest="action", required=True)

    wh_secret_p = wh_sub.add_parser(
        "secret", help="Manage webhook signing secrets.",
    )
    wh_secret_sub = wh_secret_p.add_subparsers(dest="op", required=True)

    set_p = wh_secret_sub.add_parser("set", help="Store / overwrite a secret.")
    set_p.add_argument("tenant_id")
    set_p.add_argument("ref", help="Operator-visible name (matches spec.webhook.secret_ref).")
    set_p.add_argument(
        "--value", default=None,
        help="Secret value. If omitted, reads from stdin (preferred for ops hygiene).",
    )
    set_p.set_defaults(func=_cmd_secret_set)

    list_p = wh_secret_sub.add_parser("list", help="List secret refs (no values).")
    list_p.add_argument("tenant_id")
    list_p.set_defaults(func=_cmd_secret_list)

    rm_p = wh_secret_sub.add_parser("revoke", help="Delete a secret.")
    rm_p.add_argument("tenant_id")
    rm_p.add_argument("ref")
    rm_p.set_defaults(func=_cmd_secret_revoke)

    # ── Tenant config (Phase 3.1) ───────────────────────────────────
    tn_p = sub.add_parser(
        "tenant", help="Manage per-tenant configuration (tenant.corvin.yaml).",
    )
    tn_sub = tn_p.add_subparsers(dest="action", required=True)

    init_p = tn_sub.add_parser("init", help="Initialise a fresh tenant config.")
    init_p.add_argument("tenant_id")
    init_p.add_argument("--display-name", default="")
    init_p.add_argument(
        "--zone", default=None,
        help="Data-residency zone label (free-form, e.g. eu-west).",
    )
    init_p.add_argument(
        "--allowed-engines", nargs="*", default=None,
        help="Engine names allowed for this tenant; empty = no restriction.",
    )
    init_p.add_argument(
        "--forbid-engines", nargs="*", default=None,
        help="Engine names explicitly forbidden for this tenant.",
    )
    init_p.set_defaults(func=_cmd_tenant_init)

    show_p = tn_sub.add_parser("show", help="Print the tenant config as YAML.")
    show_p.add_argument("tenant_id")
    show_p.set_defaults(func=_cmd_tenant_show)

    # ── Package (Phase 5) ─────────────────────────────────────────
    pkg_p = sub.add_parser(
        "package", help="Build / verify / install .corvin-pkg artifacts.",
    )
    pkg_sub = pkg_p.add_subparsers(dest="action", required=True)

    keygen_p = pkg_sub.add_parser(
        "keygen", help="Generate a fresh ed25519 keypair (PEM).",
    )
    keygen_p.add_argument("private_path")
    keygen_p.add_argument("public_path")
    keygen_p.set_defaults(func=_cmd_package_keygen)

    build_p = pkg_sub.add_parser(
        "build", help="Pack + sign a source directory.",
    )
    build_p.add_argument("source_dir")
    build_p.add_argument("--name", required=True)
    build_p.add_argument("--publisher", required=True)
    build_p.add_argument("--version", required=True)
    build_p.add_argument("--runtime-min", default="0.10")
    build_p.add_argument("--private-key", required=True,
                         help="Path to ed25519 private key PEM.")
    build_p.add_argument("--output-dir", default=".")
    build_p.set_defaults(func=_cmd_package_build)

    verify_p = pkg_sub.add_parser(
        "verify", help="Verify a package + signature against a public key.",
    )
    verify_p.add_argument("archive")
    verify_p.add_argument("--public-key", required=True)
    verify_p.set_defaults(func=_cmd_package_verify)

    install_p = pkg_sub.add_parser(
        "install", help="Verify + extract into a tenant.",
    )
    install_p.add_argument("archive")
    install_p.add_argument("--tenant", required=True)
    install_p.add_argument("--public-key", required=True)
    install_p.set_defaults(func=_cmd_package_install)

    # ── Plugin management (ADR-0249 Stage 6) ──────────────────────────
    from . import plugin_cmd
    plugin_cmd.add_plugin_subcommand(sub)

    return p


# ── Package subcommands ───────────────────────────────────────────────


def _cmd_package_keygen(args: argparse.Namespace) -> int:
    pkg = _packaging()
    pri, pub = pkg.generate_keypair()
    import os as _os
    fd = _os.open(args.private_path,
                  _os.O_WRONLY | _os.O_CREAT | _os.O_TRUNC, 0o600)
    try:
        _os.write(fd, pri)
    finally:
        _os.close(fd)
    Path(args.public_path).write_bytes(pub)
    print(f"Keypair written: private={args.private_path} "
          f"public={args.public_path}")
    return 0


def _cmd_package_build(args: argparse.Namespace) -> int:
    pkg = _packaging()
    private_pem = Path(args.private_key).read_bytes()
    archive, sig, manifest = pkg.build_package(
        source_dir=Path(args.source_dir),
        name=args.name, publisher=args.publisher, version=args.version,
        runtime_min=args.runtime_min,
        output_dir=Path(args.output_dir),
        private_key_pem=private_pem,
    )
    print(f"Built {archive}")
    print(f"Signed {sig}")
    print(f"  payload_sha256: {manifest.payload_sha256}")
    return 0


def _cmd_package_verify(args: argparse.Namespace) -> int:
    pkg = _packaging()
    archive = Path(args.archive)
    sig = archive.with_suffix(archive.suffix + pkg.SIGNATURE_SUFFIX)
    public_pem = Path(args.public_key).read_bytes()
    try:
        manifest = pkg.verify_package(archive, sig, public_pem)
    except pkg.PackageError as exc:
        print(f"Verify failed: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {manifest.publisher}/{manifest.name}@{manifest.version}")
    return 0


def _cmd_package_install(args: argparse.Namespace) -> int:
    pkg = _packaging()
    archive = Path(args.archive)
    sig = archive.with_suffix(archive.suffix + pkg.SIGNATURE_SUFFIX)
    public_pem = Path(args.public_key).read_bytes()
    try:
        manifest = pkg.install_package(
            archive, sig, public_pem,
            tenant_id=args.tenant,
        )
    except pkg.PackageError as exc:
        print(f"Install failed: {exc}", file=sys.stderr)
        return 1
    print(f"Installed {manifest.publisher}/{manifest.name}@{manifest.version} "
          f"into tenant {args.tenant!r}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
