#!/usr/bin/env python3
"""Layer Forge CLI — the real entry point for ADR-2222.

This is the call site an E2E test must drive (as a subprocess) to prove the
orchestrator is reachable from outside its own module — not just correct
when imported and called directly (feedback-dead-mechanism-needs-call-site-test).

Usage:
    layer_forge_cli.py create <manifest.json> [--tenant-root PATH] [--skip-gates]
    layer_forge_cli.py get <id> [--tenant-root PATH]
    layer_forge_cli.py promote <id> <version> <to_status> [--tenant-root PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.orchestration.layer_forge.orchestrator import LayerForgeOrchestrator  # noqa: E402
from core.orchestration.layer_forge.registry import LayerNotFoundError, LayerRegistry  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="layer_forge_cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create")
    p_create.add_argument("manifest_path")
    p_create.add_argument("--tenant-root", default=None)
    p_create.add_argument("--skip-gates", action="store_true")

    p_get = sub.add_parser("get")
    p_get.add_argument("id")
    p_get.add_argument("--tenant-root", default=None)

    p_promote = sub.add_parser("promote")
    p_promote.add_argument("id")
    p_promote.add_argument("version")
    p_promote.add_argument("to_status")
    p_promote.add_argument("--tenant-root", default=None)

    args = parser.parse_args(argv)

    tenant_root = Path(args.tenant_root) if args.tenant_root else REPO_ROOT / ".corvin" / "tenants" / "_default"

    if args.command == "create":
        manifest = json.loads(Path(args.manifest_path).read_text())
        orchestrator = LayerForgeOrchestrator(tenant_root=tenant_root, repo_root=REPO_ROOT)
        result = orchestrator.create_layer_definition(manifest, skip_gates=args.skip_gates)
        output = {
            "status": result.status,
            "registry_key": result.registry_key,
            "error": result.error,
            "gate_verdicts": [v.__dict__ for v in result.gate_verdicts],
            "enforcement_verdicts": [v.__dict__ for v in result.enforcement_verdicts],
        }
        print(json.dumps(output, indent=2))
        return 0 if result.status == "SUCCESS" else 1

    if args.command == "get":
        registry = LayerRegistry(tenant_root / "layer_forge" / "registry")
        try:
            entry = registry.get(args.id)
        except LayerNotFoundError as e:
            print(json.dumps({"error": f"not found: {e}"}))
            return 1
        print(json.dumps(entry, indent=2))
        return 0

    if args.command == "promote":
        registry = LayerRegistry(tenant_root / "layer_forge" / "registry")
        try:
            record = registry.promote(args.id, args.version, args.to_status)
        except Exception as e:  # noqa: BLE001 — CLI boundary, report and exit non-zero
            print(json.dumps({"error": str(e)}))
            return 1
        print(json.dumps(record, indent=2))
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
