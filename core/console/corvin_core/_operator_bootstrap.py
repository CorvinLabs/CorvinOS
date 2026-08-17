"""Wheel-install operator-dependency bootstrap.

Background
----------
56 modules under ``corvin_console`` reach their runtime dependencies in
``operator/`` via repo-relative ``sys.path`` injection, e.g.::

    _REPO = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(_REPO / "operator" / "forge"))
    from forge import paths

This works in a *source-tree* checkout (the ``operator/`` directory is a
real sibling of ``core/``).  It breaks in a *wheel* install: the wheel
ships only ``corvin_console`` / ``core`` / ``ops`` into site-packages, so
``parents[2]`` resolves into site-packages where no ``operator/`` exists
and ``from forge import paths`` raises ``ModuleNotFoundError``.

Fix
---
The wheel build vendors the needed operator subtrees into
``corvin_console/_vendor/operator/<same-relative-layout>`` (see the
``[tool.hatch.build.targets.wheel.force-include]`` block in the root
``pyproject.toml``).  This module locates that vendored tree relative to
its own ``__file__`` and prepends the mirrored subtree directories onto
``sys.path`` so the bare imports resolve from the vendored copy.

Source-tree no-op contract (load-bearing)
-----------------------------------------
In a source-tree checkout the ``_vendor/operator`` directory does NOT
exist, so :func:`ensure_operator_on_path` returns immediately without
touching ``sys.path``.  Behaviour is therefore byte-for-byte identical to
before this module existed — the per-module repo-relative injection keeps
working exactly as it did.

The function is idempotent: it can be called many times (it is invoked
from ``corvin_console.__init__``); each mirrored dir is inserted at most
once.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Operator subtree directories that console modules prepend onto sys.path,
# expressed relative to ``_vendor/operator/``.  These mirror EXACTLY the
# distinct ``operator/...`` path expressions found across all 56 modules so
# the bare imports they perform resolve from the vendored copy:
#
#   ""                    -> ``import license.validator``, ``import agent.keypair``
#   "forge"               -> ``from forge import paths``
#   "forge/forge"         -> the INNER package dir, so a BARE ``import clag`` /
#                            ``import paths`` resolves. 12 call sites across
#                            consent.py, disclosure.py, license/validator.py,
#                            audit_sealer.py and voice_audit.py import clag
#                            bare, mirroring the source-tree walk-up that
#                            inserts operator/forge/forge. Without this entry
#                            every one of them failed on a WHEEL install, and
#                            consent.py's gate is FAIL-CLOSED: is_granted()
#                            returned (False, 'chain-integrity-failed') for
#                            every user, so on a fresh install nobody could be
#                            admitted in any messenger. Worse, the boot tripwire
#                            reported "deny-by-default holds" — a permanently
#                            broken gate is indistinguishable from a working one
#                            that denies (2026-07-26 fresh-install roundtrip).
#                            Same class as the "orchestration" entry below.
#   "bridges/shared"      -> ``import engine_switch``, ``import audit``,
#                            ``agents`` package, data_classification, ...
#   "bridges"             -> a few modules insert the bridges parent dir
#   "voice/scripts"       -> voice say.py / TTS helpers
#   "mcp_manager"         -> mcp_plugins route
#   "skill-forge"         -> promote route (skill promotion)
#   "license"             -> legacy ADR-0017 license path (operator/license)
#   "cowork"              -> remote-trigger origin/endpoint readers
#   "orchestration"       -> ``import tde.*``, ``import initial_analysis`` —
#                            the ADR-0214/0217 Tiered Delegation Engine. Without
#                            this entry `import tde.*` fails on every WHEEL
#                            install, so `_tde_available()` returns False and
#                            the ADR-0217 default (TDE for all non-big-data
#                            delegation) silently degrades to ACS on PyPI —
#                            i.e. the shipped default would not exist on the
#                            primary distribution channel (2026-07-24 review).
#
# Order matters only for human readability; Python searches every entry, and
# a directory that does not contain the target package is simply skipped.
_OPERATOR_SUBTREES: tuple[str, ...] = (
    "",
    "forge",
    "forge/forge",
    "bridges/shared",
    "bridges",
    "voice/scripts",
    "mcp_manager",
    "skill-forge",
    "license",
    "cowork",
    "orchestration",
)

# Vendored core subtrees (force-included alongside operator).  ``core`` itself
# is already a top-level wheel package, but ``core/compute`` is imported by
# the data-sources route via an explicit sys.path insert; in wheel mode the
# package is importable as ``corvin_compute`` from the shipped ``core``
# package, so no extra path is required here.  Kept as a documented anchor.
_VENDOR_DIRNAME = "_vendor"


def vendor_operator_root() -> Path | None:
    """Return the vendored ``operator`` root if present, else ``None``.

    Wheel mode → ``<corvin_console>/_vendor/operator`` (exists).
    Source-tree mode → ``None`` (the directory is not shipped).
    """
    root = Path(__file__).resolve().parent / _VENDOR_DIRNAME / "operator"
    return root if root.is_dir() else None


def ensure_operator_on_path() -> bool:
    """Make vendored operator subtrees importable in wheel installs.

    Returns ``True`` if vendored paths were applied (wheel mode), ``False``
    if this is a source-tree checkout (no vendor dir → no-op).

    Idempotent and safe to call repeatedly.
    """
    root = vendor_operator_root()
    if root is None:
        # Source-tree mode: existing per-module injection already works.
        # MUST be a pure no-op here — do not touch sys.path.
        return False

    for sub in _OPERATOR_SUBTREES:
        target = root / sub if sub else root
        if not target.is_dir():
            continue
        s = str(target)
        if s not in sys.path:
            # Prepend so the vendored copy is found before any stray entry.
            sys.path.insert(0, s)
    return True
