"""Tenant-native skill management CLI — argparse↔click bridge (ADR-0446).

The tenant-native skill management CLI already exists as two fully built,
unit-tested ``click`` groups:

  * ``operator/cli/skill_commands.py``      → ``skill`` group
    (list / info / validate / deps / migrate / init)
  * ``operator/cli/skill_sync_commands.py`` → ``skill-sync`` group
    (push / pull / configure / status)

Both exposed ``register_skill_commands`` / ``register_sync_commands`` entry
points, but nothing ever called them, so the whole surface was dead
(reachable from zero live call sites). This module wires it into the real
``corvin`` launcher CLI.

Two impedance mismatches are bridged here:

1. **Framework** — the launcher CLI (``ops/launcher/corvin/cli.py``) is
   ``argparse``; the skill CLI is ``click``. We expose ``skill`` /
   ``skill-sync`` as argparse subcommands that capture their remaining tokens
   verbatim (``argparse.REMAINDER``) and hand them to a private ``click``
   parent group at dispatch time.

2. **Import path** — ``operator`` is also a Python stdlib module, so
   ``from operator.cli.skill_commands import …`` resolves to the wrong
   ``operator`` and fails (the repo notes this shadow in several places). We
   therefore load the two modules by file path via ``importlib`` and call
   their own ``register_*`` functions, honouring the package's documented
   wiring contract rather than importing the click groups directly.

Ship-dark / feature-flag decision (see ADR-0446): **no flag.** A separately
named CLI verb group is inert until an operator explicitly types it, so a
fresh or upgraded install that never runs ``corvin skill …`` is byte-for-byte
unchanged in behaviour. CorvinOS ships every other additive subcommand
(``plugin``, ``tenant``, ``migrate``, ``tde``, ``audit``/``consent``) the same
way — unconditionally, no flag — because the repo's feature-flag rule gates
*ambient* behaviour changes (routing, delegation, UI mounts), not
manually-invoked commands. The one mutating verb, ``skill migrate --confirm``,
already requires an explicit confirmation flag of its own.
"""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from types import ModuleType

# operator/cli lives at <repo-root>/operator/cli. This file is at
# <repo-root>/ops/launcher/corvin/skill_cmd.py → parents[3] is the repo root.
_OPERATOR_CLI = Path(__file__).resolve().parents[3] / "operator" / "cli"


def add_parser(parent_subparsers: argparse._SubParsersAction) -> None:
    """Wire ``skill`` and ``skill-sync`` subcommands into the main CLI.

    ``add_help=False`` + a ``REMAINDER`` positional means argparse hands the
    whole tail (including ``--help`` and any options) straight to click, so
    click renders its own group/subcommand help and usage errors.
    """
    sk = parent_subparsers.add_parser(
        "skill",
        help="Tenant-native skill management (list/info/validate/deps/migrate/init)",
        add_help=False,
    )
    sk.add_argument(
        "click_args",
        nargs=argparse.REMAINDER,
        help="Subcommand and options passed through to the skill CLI",
    )

    ss = parent_subparsers.add_parser(
        "skill-sync",
        help="GitHub skill synchronization (push/pull/configure/status)",
        add_help=False,
    )
    ss.add_argument(
        "click_args",
        nargs=argparse.REMAINDER,
        help="Subcommand and options passed through to the skill-sync CLI",
    )


def _load_by_path(module_name: str, filename: str) -> ModuleType:
    """Load a module in operator/cli by file path (avoids the stdlib
    ``operator`` shadow — see module docstring)."""
    path = _OPERATOR_CLI / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover — defensive
        raise ImportError(f"cannot load {filename} from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_click_parent():
    """Build the private click parent group with both skill groups registered.

    Goes through the package's own ``register_skill_commands`` /
    ``register_sync_commands`` entry points rather than importing the groups
    directly, so this stays wired to the contract the skill CLI publishes.
    """
    import click  # local import: keep argparse-only paths (e.g. `corvin status`) working

    skill_commands = _load_by_path(
        "corvin_launcher_skill_commands", "skill_commands.py"
    )
    skill_sync_commands = _load_by_path(
        "corvin_launcher_skill_sync_commands", "skill_sync_commands.py"
    )

    @click.group()
    def _parent() -> None:
        pass

    skill_commands.register_skill_commands(_parent)
    skill_sync_commands.register_sync_commands(_parent)
    return _parent


def dispatch_argv(argv: list[str]) -> int:
    """Dispatch ``corvin skill …`` / ``corvin skill-sync …`` to the click CLI.

    ``argv`` is the raw launcher argv tail (``argv[0]`` is ``"skill"`` or
    ``"skill-sync"``). It is handed to click verbatim rather than routed
    through argparse, because ``argparse.REMAINDER`` does not capture a leading
    optional such as ``--help`` when it is the first positional token — so
    ``corvin skill --help`` would otherwise be rejected by the launcher parser
    before it ever reached click.
    """
    try:
        parent = _build_click_parent()
    except ImportError as exc:
        # core.skill_management (or click) not installed in this environment.
        print(
            "  Skill management is unavailable in this install: "
            f"{exc}\n  Install the full CorvinOS package to enable it."
        )
        return 1

    # standalone_mode=True lets click render --help and usage errors itself and
    # call sys.exit with the right code; we intercept that to return an int so
    # the launcher's `sys.exit(...)` stays the single exit point.
    try:
        parent.main(args=list(argv), prog_name="corvin", standalone_mode=True)
        return 0
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        return code if isinstance(code, int) else 1
