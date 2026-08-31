"""flow_cli.py — corvin-flow CLI (ADR-0121 M3).

Entry point: corvin-flow

Sub-commands:
  pack    <flow-dir>        Pack a flow directory into a .corvinflow bundle
  install <bundle.corvinflow> [--dest <dir>]  Install + validate a bundle
  verify  <bundle.corvinflow> --pub-key <hex>  Verify bundle signature
  run     <flow-id> <input-json>              Run an installed flow (local, M1)
  keygen                                       Generate an Ed25519 signing keypair

Usage examples:
  corvin-flow pack ./my-flow/
  corvin-flow install my-flow-1.0.0.corvinflow
  corvin-flow run my-flow '{"query": "hello"}'
  corvin-flow keygen
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _shared_path() -> None:
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    op = here.parents[1]  # operator/
    if str(op) not in sys.path:
        sys.path.insert(0, str(op))


def cmd_pack(args: argparse.Namespace) -> int:
    _shared_path()
    from flow_bundle import FlowBundle, FlowBundleError
    flow_dir = Path(args.flow_dir).expanduser().resolve()
    output_dir = Path(args.output).expanduser().resolve() if args.output else None

    signing_key: bytes | None = None
    if args.sign_key:
        raw = Path(args.sign_key).read_bytes()
        if len(raw) != 32:
            print(f"error: signing key must be 32 bytes raw Ed25519 private key, got {len(raw)}", file=sys.stderr)
            return 1
        signing_key = raw

    try:
        bundle_path = FlowBundle.pack(flow_dir, output_dir, signing_key_bytes=signing_key)
        print(f"Packed: {bundle_path}")
        return 0
    except FlowBundleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def cmd_install(args: argparse.Namespace) -> int:
    _shared_path()
    import zipfile
    from flow_bundle import FlowBundle, FlowBundleError
    bundle_path = Path(args.bundle).expanduser().resolve()

    pub_key: bytes | None = None
    if args.pub_key:
        raw = Path(args.pub_key).read_bytes()
        if len(raw) != 32:
            print(f"error: public key must be 32 bytes raw Ed25519 public key", file=sys.stderr)
            return 1
        pub_key = raw

    if args.dest:
        dest = Path(args.dest).expanduser().resolve()
    else:
        # Default: install under corvin_home/tenants/_default/global/flows/<flow_id>/
        # so that `corvin-flow run <flow_id>` finds the flow without --dest surgery.
        try:
            with zipfile.ZipFile(bundle_path, "r") as _zf:
                _manifest = json.loads(_zf.read("manifest.json").decode())
            flow_id = _manifest.get("id") or bundle_path.stem
        except Exception:
            flow_id = bundle_path.stem
        from paths import corvin_home
        dest = corvin_home() / "tenants" / "_default" / "global" / "flows" / flow_id

    try:
        fd = FlowBundle.install(bundle_path, dest, pub_key_bytes=pub_key)
        print(f"Installed flow '{fd.id}' v{fd.version} → {dest}")
        return 0
    except FlowBundleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def cmd_verify(args: argparse.Namespace) -> int:
    _shared_path()
    from flow_bundle import FlowBundle, FlowBundleError
    bundle_path = Path(args.bundle).expanduser().resolve()

    if not args.pub_key:
        print("error: --pub-key is required for verify", file=sys.stderr)
        return 1

    pub_raw = Path(args.pub_key).read_bytes()
    try:
        FlowBundle.verify(bundle_path, pub_raw)
        print(f"Signature OK: {bundle_path.name}")
        return 0
    except FlowBundleError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1


def cmd_run(args: argparse.Namespace) -> int:
    _shared_path()
    from flow_definition import FlowDefinition, FlowDefinitionError
    from flow_runner import FlowRunner
    from paths import corvin_home

    flow_id = args.flow_id
    try:
        flow_input = json.loads(args.input_json) if args.input_json else {}
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON input: {exc}", file=sys.stderr)
        return 1

    flows_dir = corvin_home() / "tenants" / "_default" / "global" / "flows"
    flow_yaml_path = flows_dir / flow_id / "flow.yaml"
    if not flow_yaml_path.exists():
        print(f"error: flow '{flow_id}' not found at {flow_yaml_path}", file=sys.stderr)
        return 1

    try:
        fd = FlowDefinition.from_file(flow_yaml_path)
    except FlowDefinitionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    manifest_root = flows_dir / "runs"
    runner = FlowRunner(fd, manifest_root, flow_input)

    try:
        result = runner.run()
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        print(f"error: flow run failed: {exc}", file=sys.stderr)
        return 1


def cmd_keygen(args: argparse.Namespace) -> int:
    _shared_path()
    from flow_bundle import FlowBundle
    priv, pub = FlowBundle.generate_keypair()

    out = Path(args.out) if args.out else Path(".")
    priv_path = out / "flow-signing.key"
    pub_path = out / "flow-signing.pub"
    priv_path.write_bytes(priv)
    pub_path.write_bytes(pub)

    import os
    os.chmod(priv_path, 0o600)

    print(f"Private key: {priv_path}  (mode 0600 — keep secret)")
    print(f"Public key:  {pub_path}")
    print(f"Pub (hex):   {pub.hex()}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="corvin-flow",
        description="CorvinFlow — declarative multi-node agent workflow CLI (ADR-0121)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # pack
    p_pack = sub.add_parser("pack", help="Pack a flow directory into a .corvinflow bundle")
    p_pack.add_argument("flow_dir", help="Directory containing flow.yaml + manifest.json")
    p_pack.add_argument("--output", "-o", help="Output directory (default: parent of flow_dir)")
    p_pack.add_argument("--sign-key", help="Path to 32-byte raw Ed25519 private key file")

    # install
    p_inst = sub.add_parser("install", help="Install a .corvinflow bundle")
    p_inst.add_argument("bundle", help="Path to .corvinflow file")
    p_inst.add_argument("--dest", help="Destination directory")
    p_inst.add_argument("--pub-key", help="Path to 32-byte raw Ed25519 public key for verification")

    # verify
    p_ver = sub.add_parser("verify", help="Verify a bundle's Ed25519 signature")
    p_ver.add_argument("bundle", help="Path to .corvinflow file")
    p_ver.add_argument("--pub-key", required=True, help="Path to 32-byte raw Ed25519 public key")

    # run
    p_run = sub.add_parser("run", help="Run an installed flow (M1: local execution)")
    p_run.add_argument("flow_id", help="Flow ID (must be installed under corvin_home/global/flows/)")
    p_run.add_argument("input_json", nargs="?", default="{}", help='JSON input dict e.g. \'{"query":"hello"}\'')

    # keygen
    p_kg = sub.add_parser("keygen", help="Generate an Ed25519 signing keypair")
    p_kg.add_argument("--out", help="Output directory (default: current dir)")

    args = parser.parse_args()

    handlers = {
        "pack": cmd_pack,
        "install": cmd_install,
        "verify": cmd_verify,
        "run": cmd_run,
        "keygen": cmd_keygen,
    }
    sys.exit(handlers[args.cmd](args))


if __name__ == "__main__":
    main()
