"""Operator-layout guard — keeps the canonical tree canonical (ADR-0730).

Background (2026-09-15). Two commits moved ~7400 files each, under messages
that mention no move at all ("feat(vibe): Phase 2 Feature 1 Frontend Wiring",
"fix(task-graph-api): wire CheckpointManager producer") and without an ADR.
The repo ended up carrying THREE operator trees simultaneously:

    operator/          0 tracked,  0 *.py, 42 MB of live bridge queues
    corvin_operator/   7433 tracked, canonical
    core/operator/     7406 tracked, ZERO inbound references

Nothing was red while that was true. The damage surfaced indirectly and late:
187 ``sys.path`` injections pointed at a directory that no longer held any
Python, the audit-verify unit died on ``No module named 'audit'``, 18 systemd
units ran out of deleted working directories, and — worst — the ``.gitignore``
entries shielding ``operator/bridges/*/settings.json`` stopped matching, so a
live Discord bot token and two mail passwords were committed.

These tests are the cheap check that was missing. They are deliberately
*structural*: they do not care what the code does, only that the layout the
build system and 300+ sys.path injections assume is still the layout on disk.

Run from a git checkout; skipped elsewhere (a wheel has no repo to inspect).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: Legacy names that must never hold Python again. ``operator`` collides with
#: the stdlib module of the same name, which is the whole reason for the
#: rename; ``core/operator`` was the accidental duplicate.
LEGACY_DIRS = ("operator", "core/operator")

#: Ratchet for the dotted-import defect (PLAN-0730 Phase 3, still open).
#: ``from operator.X import Y`` can NEVER resolve — ``operator`` is a stdlib
#: module, so Python raises "'operator' is not a package". Every one of these
#: sits in a try/except and fails silently; core/orchestration/quota_gate.py
#: records that the license quota was consequently never enforced.
#: This number may only go DOWN. Lower it when Phase 3 lands; never raise it.
MAX_DOTTED_OPERATOR_IMPORTS = 61

#: A rename sweep makes this collapse toward zero, which is exactly the signal
#: the two accident commits failed to produce. Well below the real count
#: (7428) so ordinary churn never trips it.
MIN_CORVIN_OPERATOR_FILES = 6000

_DOTTED_IMPORT = re.compile(r"^\s*(?:from|import)\s+operator\.", re.MULTILINE)
_STALE_SYSPATH = re.compile(r'sys\.path\.insert[^)]*"operator"')


def _git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args], cwd=REPO, capture_output=True, text=True, timeout=120
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
        pytest.skip(f"git unavailable: {exc}")
    if out.returncode not in (0, 1):  # 1 = grep found nothing
        pytest.skip(f"git failed: {out.stderr[:200]}")
    return out.stdout


@pytest.fixture(scope="module", autouse=True)
def _require_checkout() -> None:
    if not (REPO / ".git").exists():
        pytest.skip("not a git checkout")


def test_no_python_under_legacy_operator_dirs() -> None:
    """``operator/`` and ``core/operator/`` must hold no Python at all.

    Either one holding a ``.py`` again means a tree was restored or a new
    sweep landed — and a repo-root ``operator/`` package re-arms the stdlib
    shadow that ADR-0730 exists to end.
    """
    offenders = [
        str(p.relative_to(REPO))
        for d in LEGACY_DIRS
        for p in (REPO / d).rglob("*.py")
        if (REPO / d).is_dir()
    ]
    assert not offenders, (
        f"{len(offenders)} Python file(s) reappeared under a legacy operator "
        f"directory; the canonical tree is corvin_operator/ (ADR-0730). "
        f"First few: {offenders[:5]}"
    )


def test_no_stale_syspath_injection() -> None:
    """No module may inject the legacy ``operator`` directory onto sys.path.

    187 files did exactly this after the rename, pointing at a directory with
    zero ``.py`` files. The failure mode is silent: the import lands on a
    stdlib module or a try/except, never on a traceback anyone reads.
    """
    hits = [ln for ln in _git("grep", "-nE", _STALE_SYSPATH.pattern, "--", "*.py").splitlines()]
    assert not hits, (
        f"{len(hits)} sys.path injection(s) still name the legacy "
        f'"operator" directory; use "corvin_operator". First few: {hits[:3]}'
    )


def test_build_files_reference_only_the_canonical_tree() -> None:
    """The wheel machinery must agree with the tree on disk.

    ``hatch_build.py`` vendors operator subtrees into the wheel and
    ``_operator_bootstrap.py`` puts them back on sys.path (ADR-0352). If these
    two name a directory that does not exist, the checkout still works and
    every wheel install breaks — the exact asymmetry that hid the 2026-09-03
    wheel-content bugs.
    """
    for rel in ("hatch_build.py", "core/console/corvin_core/_operator_bootstrap.py"):
        text = (REPO / rel).read_text(encoding="utf-8")
        stale = re.findall(r'(?<!corvin_)(?<![\w.-])operator/', text)
        assert not stale, (
            f"{rel} references the legacy operator/ path {len(stale)}x; "
            f"it must name corvin_operator/ only (ADR-0730)."
        )


def test_dotted_operator_imports_do_not_grow() -> None:
    """Ratchet: ``from operator.X`` can never resolve, so it must not spread.

    This is a ratchet, not a clean gate — PLAN-0730 Phase 3 still has to
    repair the existing ones, and that is a behaviour change (it activates the
    license quota gate) which must not ride along in a rename commit.
    """
    count = len(_DOTTED_IMPORT.findall(_git("grep", "-hE",
                                            r"^\s*(from|import) operator\.",
                                            "--", "*.py")))
    assert count <= MAX_DOTTED_OPERATOR_IMPORTS, (
        f"{count} dotted `operator.` imports, limit is "
        f"{MAX_DOTTED_OPERATOR_IMPORTS}. These NEVER resolve: `operator` is a "
        f"stdlib module, so Python raises \"'operator' is not a package\", and "
        f"the surrounding try/except swallows it. Put the directory on "
        f"sys.path and import the inner package bare instead (ADR-0352)."
    )


def test_canonical_tree_is_still_populated() -> None:
    """Catch a mass move the moment it happens, not six hours later.

    Both accident commits emptied a 7400-file tree without a single test
    turning red. This one turns red.
    """
    n = len([x for x in _git("ls-files", "corvin_operator/").splitlines() if x])
    assert n >= MIN_CORVIN_OPERATOR_FILES, (
        f"only {n} files tracked under corvin_operator/ (expected "
        f">={MIN_CORVIN_OPERATOR_FILES}). A rename sweep probably moved the "
        f"canonical tree; see ADR-0730 before adjusting this number."
    )


def test_bridge_credentials_are_not_tracked() -> None:
    """Live bridge credentials must never be in the index.

    The .gitignore entries protecting these matched ``operator/bridges/...``
    by literal path. The rename silently voided that protection and the next
    two commits checked in a working Discord bot token plus IMAP and SMTP
    passwords. Path-literal ignores are fragile by construction, so assert the
    outcome instead of trusting the pattern.
    """
    tracked = [
        p for p in _git("ls-files", "*/bridges/*/settings.json").splitlines() if p
    ]
    assert not tracked, (
        f"{len(tracked)} bridge settings file(s) are tracked and these carry "
        f"live tokens/passwords: {tracked}. Remove with "
        f"`git rm --cached` (the file must stay on disk — the bridges read "
        f"it) and make sure .gitignore covers the current path."
    )
