#!/usr/bin/env python3
"""Forge Bundle CLI — export (ADR-2229 Phase 2).

Packs forged Skills/Tools/Layers/Plugins into one ZIP, reusing each forge's
own packaging (``core/forge_bundle/export.py``). ``CORVIN_HOME`` selects the
tenant root, same as every other Forge CLI.

Usage:
    forge_bundle_cli.py export --id ID --version VERSION --output FILE.zip
        [--tenant TID] [--description TEXT]
        [--skill NAME@VERSION ...] [--tool NAME@VERSION ...]
        [--layer ID@VERSION ...] [--plugin ID@VERSION:WHEEL_PATH ...]

``requires`` (cross-artifact dependencies) are not expressible from the CLI
yet — use ``core.forge_bundle.export.build_bundle`` directly for that; the
CLI exports every selection with an empty ``requires`` list.

Exit codes: 0 success, 1 refused/failed, 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
# ``forge.*`` (MultiRegistry, paths) lives at corvin_operator/forge/forge/ —
# a separate top-level package, not importable from REPO_ROOT alone.
sys.path.insert(0, str(REPO_ROOT / "corvin_operator" / "forge"))

from core.forge_bundle.audit import ForgeBundleAuditError  # noqa: E402
from core.forge_bundle.export import (  # noqa: E402
    ExportError,
    LayerSelection,
    PluginSelection,
    SkillSelection,
    ToolSelection,
    build_bundle,
)


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def _split_at_version(spec: str, flag: str) -> tuple[str, str]:
    if "@" not in spec:
        raise ValueError(f"{flag} {spec!r}: expected NAME@VERSION")
    name, _, version = spec.partition("@")
    if not name or not version:
        raise ValueError(f"{flag} {spec!r}: expected NAME@VERSION")
    return name, version


def _parse_plugin(spec: str) -> tuple[str, str, Path]:
    name_version, _, wheel = spec.partition(":")
    if not wheel:
        raise ValueError(f"--plugin {spec!r}: expected ID@VERSION:WHEEL_PATH")
    name, version = _split_at_version(name_version, "--plugin")
    return name, version, Path(wheel)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forge_bundle_cli.py")
    sub = parser.add_subparsers(dest="command", required=True)

    export = sub.add_parser("export", help="Build a Forge Bundle ZIP")
    export.add_argument("--id", required=True, help="Bundle id (safe identifier)")
    export.add_argument("--version", required=True, help="Bundle version (semver)")
    export.add_argument("--output", required=True, help="Path to write the ZIP to")
    export.add_argument("--tenant", default=None, help="Tenant id (default: $CORVIN_TENANT_ID or _default)")
    export.add_argument("--description", default=None)
    export.add_argument("--skill", action="append", default=[], metavar="NAME@VERSION")
    export.add_argument("--tool", action="append", default=[], metavar="NAME@VERSION")
    export.add_argument("--layer", action="append", default=[], metavar="ID@VERSION")
    export.add_argument("--plugin", action="append", default=[], metavar="ID@VERSION:WHEEL_PATH")
    return parser


def _cmd_export(args: argparse.Namespace) -> int:
    from forge.paths import _resolve_tenant_id  # noqa: PLC0415

    try:
        tenant_id = _resolve_tenant_id(args.tenant)
        selections = []
        for spec in args.skill:
            name, version = _split_at_version(spec, "--skill")
            selections.append(SkillSelection(skill_id=name, version=version))
        for spec in args.tool:
            name, version = _split_at_version(spec, "--tool")
            selections.append(ToolSelection(name=name, version=version))
        for spec in args.layer:
            name, version = _split_at_version(spec, "--layer")
            selections.append(LayerSelection(entry_id=name, version=version))
        for spec in args.plugin:
            name, version, wheel = _parse_plugin(spec)
            selections.append(PluginSelection(plugin_id=name, version=version, wheel_path=wheel))
    except ValueError as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        return 2

    if not selections:
        print("usage error: at least one --skill/--tool/--layer/--plugin is required", file=sys.stderr)
        return 2

    try:
        result = build_bundle(
            bundle_id=args.id,
            bundle_version=args.version,
            selections=selections,
            tenant_id=tenant_id,
            description=args.description,
        )
    except (ExportError, ForgeBundleAuditError) as exc:
        _print({"status": "FAILED", "error": str(exc), "error_class": type(exc).__name__})
        return 1

    output = Path(args.output)
    output.write_bytes(result.data)
    _print({
        "status": "SUCCESS",
        "bundle_id": result.bundle_id,
        "bundle_version": result.bundle_version,
        "artifact_count": result.artifact_count,
        "total_bytes": result.total_bytes,
        "output": str(output.resolve()),
        "audit_hash": result.audit_hash,
    })
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "export":
        return _cmd_export(args)
    parser.error(f"unknown command: {args.command}")
    return 2  # pragma: no cover — argparse.error() exits before this


if __name__ == "__main__":
    sys.exit(main())
