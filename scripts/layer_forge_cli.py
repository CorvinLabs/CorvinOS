#!/usr/bin/env python3
"""Layer Forge CLI (ADR-2222, ADR-2225 Phase 1).

Storage and audit resolve through the tenant resolver: the registry lives at
``<corvin_home>/tenants/<tid>/global/layer_forge/`` and every decision lands in
the tenant's hash-chained audit log. ``CORVIN_HOME`` selects the root.

Usage:
    layer_forge_cli.py plan <layer_id> <intent> [--tenant TID]
    layer_forge_cli.py create <manifest.json> [--tenant TID] [--skip-gates]
    layer_forge_cli.py get <id> [--version V] [--tenant TID]
    layer_forge_cli.py list [--tenant TID]
    layer_forge_cli.py promote <id> <version> <to_status> [--tenant TID]

Exit codes: 0 success, 1 refused/failed, 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.orchestration.layer_forge.audit import LayerForgeAuditError  # noqa: E402
from core.orchestration.layer_forge.orchestrator import LayerForgeOrchestrator  # noqa: E402
from core.orchestration.layer_forge.registry import (  # noqa: E402
    LayerNotFoundError,
    LayerPromotionError,
)


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="layer_forge_cli")
    parser.add_argument("--tenant", default=os.environ.get("CORVIN_TENANT_ID") or "_default")
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create")
    p_create.add_argument("manifest_path")
    p_create.add_argument("--skip-gates", action="store_true")

    p_get = sub.add_parser("get")
    p_get.add_argument("id")
    p_get.add_argument("--version", default=None)

    sub.add_parser("list")

    p_promote = sub.add_parser("promote")
    p_promote.add_argument("id")
    p_promote.add_argument("version")
    p_promote.add_argument("to_status")

    p_plan = sub.add_parser("plan")
    p_plan.add_argument("layer_id")
    p_plan.add_argument("intent")

    args = parser.parse_args(argv)
    try:
        orch = LayerForgeOrchestrator(args.tenant, actor="cli")
    except ValueError as e:  # invalid tenant id
        _print({"error": f"invalid tenant: {e}"})
        return 2

    if args.command == "plan":
        manifest, result = orch.plan_layer_definition(args.layer_id, args.intent)
        body = result.to_dict()
        if manifest:
            body["manifest"] = manifest
        _print(body)
        return 0 if result.status == "SUCCESS" else 1

    if args.command == "create":
        try:
            manifest = json.loads(Path(args.manifest_path).read_text())
        except (OSError, json.JSONDecodeError) as e:
            _print({"status": "FAILED", "error": f"cannot read manifest: {type(e).__name__}"})
            return 1
        result = orch.create_layer_definition(manifest, skip_gates=args.skip_gates)
        _print(result.to_dict())
        return 0 if result.status == "SUCCESS" else 1

    if args.command == "get":
        try:
            _print(orch.get(args.id, args.version))
        except LayerNotFoundError as e:
            _print({"error": f"not found: {e}"})
            return 1
        return 0

    if args.command == "list":
        _print(orch.list_definitions())
        return 0

    if args.command == "promote":
        try:
            _print(orch.promote(args.id, args.version, args.to_status))
        except (LayerNotFoundError, LayerPromotionError, LayerForgeAuditError) as e:
            _print({"error": str(e)})
            return 1
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
