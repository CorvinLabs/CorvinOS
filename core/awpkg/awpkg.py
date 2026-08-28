#!/usr/bin/env python3
"""corvin pkg — AWPKG CLI (ADR-0032)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def _cmd_install(args: argparse.Namespace) -> int:
    from awpkg.installer import install, register_components, InstallError
    try:
        pkg = install(args.file, scope=args.scope)
    except InstallError as exc:
        print(f"install error: {exc}", file=sys.stderr)
        return 1
    print(f"Installed {pkg.id}  v{pkg.version}  [{args.scope}]")
    # Activate the package: register its skills/tools into the runtime registries so they
    # become visible to skill_inject / Forge. This is the operator-initiated (human-gated)
    # install path — the correct place per ADR-0451/0460 (install() alone is a deposit and
    # registers nothing; the operator running `pkg install` is the human gate). A registration
    # failure does NOT undo the on-disk install, but it is reported (never a silent partial).
    try:
        tenant_id = getattr(args, "tenant", None) or "_default"
        summary = register_components(pkg, tenant_id=tenant_id)
        n_sk, n_tl = len(summary.get("skills", [])), len(summary.get("forge_tools", []))
        if n_sk or n_tl:
            print(f"Registered {n_sk} skill(s), {n_tl} tool(s)")
        for s in summary.get("skills_skipped", []):
            print(f"  skipped skill: {s}", file=sys.stderr)
    except Exception as exc:
        print(f"warning: package installed but component registration failed: {exc}", file=sys.stderr)
    return 0


def _cmd_remove(args: argparse.Namespace) -> int:
    from awpkg.installer import remove, NotInstalledError
    try:
        remove(args.id, scope=args.scope)
        print(f"Removed {args.id}  [{args.scope}]")
        return 0
    except NotInstalledError as exc:
        print(f"not installed: {exc}", file=sys.stderr)
        return 1


def _cmd_list(args: argparse.Namespace) -> int:
    from awpkg.installer import list_installed
    pkgs = list_installed(scope=args.scope)
    if not pkgs:
        print(f"No packages installed in scope {args.scope!r}.")
        return 0
    for p in pkgs:
        kinds = ", ".join(
            f"{len(v)} {k}"
            for k, v in p.components.items()
            if v
        )
        print(f"  {p.id}  v{p.version}  ({kinds})")
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    from awpkg.inspector import inspect, InspectError
    try:
        result = inspect(args.file)
        print(result.summary())
        if result.readme:
            print("\n--- README ---")
            print(result.readme[:2000])
        return 0
    except InspectError as exc:
        print(f"inspect error: {exc}", file=sys.stderr)
        return 1


def _cmd_init(args: argparse.Namespace) -> int:
    from awpkg.builder import init, BuildError
    try:
        cfg = init(args.dir)
        print(f"Initialized {cfg}")
        return 0
    except BuildError as exc:
        print(f"init error: {exc}", file=sys.stderr)
        return 1


def _cmd_build(args: argparse.Namespace) -> int:
    from awpkg.builder import build, BuildError
    try:
        out = build(args.dir, output_dir=args.out)
        print(f"Built {out}")
        return 0
    except BuildError as exc:
        print(f"build error: {exc}", file=sys.stderr)
        return 1


def _cmd_export(args: argparse.Namespace) -> int:
    from awpkg.builder import export, BuildError
    try:
        out = export(args.id, args.out, scope=args.scope)
        print(f"Exported {out}")
        return 0
    except BuildError as exc:
        print(f"export error: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="corvin pkg", description="AWPKG package manager")
    sub = parser.add_subparsers(dest="command", required=True)

    p_install = sub.add_parser("install", help="Install an .awpkg file")
    p_install.add_argument("file", help="Path to .awpkg file")
    p_install.add_argument("--scope", default="user", choices=["user", "project", "session"])

    p_remove = sub.add_parser("remove", help="Remove an installed package")
    p_remove.add_argument("id", help="Package id (e.g. com.example.my-workflow)")
    p_remove.add_argument("--scope", default="user", choices=["user", "project", "session"])

    p_list = sub.add_parser("list", help="List installed packages")
    p_list.add_argument("--scope", default="user", choices=["user", "project", "session"])

    p_inspect = sub.add_parser("inspect", help="Inspect an .awpkg without installing")
    p_inspect.add_argument("file", help="Path to .awpkg file")

    p_init = sub.add_parser("init", help="Create awpkg.yaml skeleton in a directory")
    p_init.add_argument("dir", nargs="?", default=".", help="Target directory")

    p_build = sub.add_parser("build", help="Build .awpkg from awpkg.yaml")
    p_build.add_argument("dir", nargs="?", default=".", help="Source directory with awpkg.yaml")
    p_build.add_argument("--out", default=None, help="Output directory")

    p_export = sub.add_parser("export", help="Export installed package to .awpkg")
    p_export.add_argument("id", help="Package id")
    p_export.add_argument("out", help="Output directory")
    p_export.add_argument("--scope", default="user", choices=["user", "project", "session"])

    args = parser.parse_args(argv)
    dispatch = {
        "install": _cmd_install,
        "remove": _cmd_remove,
        "list": _cmd_list,
        "inspect": _cmd_inspect,
        "init": _cmd_init,
        "build": _cmd_build,
        "export": _cmd_export,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
