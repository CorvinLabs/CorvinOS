"""Learning reachability ratchet (ADR-2281 §3).

The real tree must match ``scripts/learning_reachability_baseline.json``
exactly: no unreachable learning module outside the baseline (a new dead
module), and no baseline entry whose module is reachable or gone (stale).

The self-tests prove the gate can fail. They run the same sweep over a
SANDBOX COPY of the tree (hard links of the files the sweep reads; nothing in
the repo is written), seed a new unreachable module there and delete a
baselined one, and show each direction red with the unchanged baseline and
green once the baseline is corrected.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import learning_reachability as lr  # noqa: E402
import zero_caller_sweep as zcs  # noqa: E402

SEED = "core/learning/_ratchet_seed_unreachable.py"


@pytest.fixture(scope="module")
def real():
    return lr.unreachable()


def _sweep_inputs(name: str) -> bool:
    # Everything the sweep reads: python files, shell launchers, systemd
    # units, builtin plugin manifests, pyproject (scripts + wheel mapping).
    return (name.endswith((".py", ".sh", ".service"))
            or name in ("plugin.yaml", "pyproject.toml"))


def _sandbox_copy(dst: Path) -> None:
    skip = zcs.EXCLUDE_PARTS | {".git"}
    for dirpath, dirnames, filenames in os.walk(REPO):
        dirnames[:] = [d for d in dirnames if d not in skip]
        rel = Path(dirpath).relative_to(REPO)
        for n in filenames:
            if not _sweep_inputs(n):
                continue
            src = Path(dirpath) / n
            out = dst / rel / n
            out.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(src, out)   # same inode, no bytes copied; only ever unlinked below
            except OSError:
                shutil.copy2(src, out)


@pytest.fixture(scope="module")
def seeded(tmp_path_factory, real):
    _, dead = real
    baseline = lr.load_baseline()
    victim = next(d for d in baseline if d in dead and not d.endswith("__init__.py"))
    box = tmp_path_factory.mktemp("learning_ratchet_sandbox")
    _sandbox_copy(box)
    (box / SEED).write_text('"""Seeded by the ratchet self-test; nothing imports it."""\n',
                            encoding="utf-8")
    (box / victim).unlink()   # removes the sandbox link only
    _, box_dead = lr.unreachable(box)
    assert not (REPO / SEED).exists(), "the self-test must never write into the repo"
    assert (REPO / victim).is_file(), "the self-test must never delete from the repo"
    return box_dead, baseline, victim


def test_real_tree_matches_baseline(real):
    n, dead = real
    assert n > 0, "no learning module in scope: the sweep looked at nothing"
    new, stale = lr.compare(dead, lr.load_baseline())
    assert not new, (
        "new unreachable learning module(s); wire them through a real entry point, "
        f"or retire them by a decision — never add them to the baseline: {new}")
    assert not stale, (
        "baseline entries whose module is now reachable or deleted; remove them from "
        f"scripts/learning_reachability_baseline.json: {stale}")


def test_baseline_is_sorted_and_unique():
    b = lr.load_baseline()
    assert b == sorted(set(b))


def test_seeded_new_module_fails_then_passes_once_baselined(seeded):
    box_dead, baseline, _ = seeded
    assert SEED in box_dead, "positive control: the seeded module must be seen as unreachable"
    new, _ = lr.compare(box_dead, baseline)
    assert new == [SEED]                                  # red
    new, _ = lr.compare(box_dead, baseline + [SEED])
    assert new == []                                      # green


def test_deleted_baselined_module_is_stale_until_removed(seeded):
    box_dead, baseline, victim = seeded
    assert victim not in box_dead
    _, stale = lr.compare(box_dead, baseline)
    assert stale == [victim]                              # red
    _, stale = lr.compare(box_dead, [b for b in baseline if b != victim])
    assert stale == []                                    # green


def test_baselined_reachable_module_is_stale(real):
    _, dead = real
    live = "core/learning/outcome_sink.py"
    assert live not in dead, "positive control: the live outcome sink is reachable"
    _, stale = lr.compare(dead, lr.load_baseline() + [live])
    assert stale == [live]


def test_check_cli_exits_nonzero_on_new_module(tmp_path, capsys):
    # The CLI gate (what CI and run-all-tests.sh see) against the real tree
    # with a baseline that misses one real entry: --check must exit 1.
    short = tmp_path / "short.json"
    lr.write_baseline(lr.load_baseline()[1:], short)
    assert lr.main(["--check", "--baseline", str(short)]) == 1
    assert "1 not in baseline" in capsys.readouterr().out


# ── LRN-19: a re-export in core/learning/__init__.py is not reachability ───────

REEXPORT_SEED = "core/learning/_lrn19_reexported_only.py"


@pytest.fixture(scope="module")
def reexport_box(tmp_path_factory):
    """A sandbox where a new module is imported ONLY by core/learning/__init__.py."""
    box = tmp_path_factory.mktemp("learning_ratchet_reexport")
    _sandbox_copy(box)
    init = box / "core" / "learning" / "__init__.py"
    original = init.read_text(encoding="utf-8")
    init.unlink()   # break the hard link first: the repo's __init__ is never written
    init.write_text(original + "\nfrom ._lrn19_reexported_only import LRN19_SEED  # noqa\n",
                    encoding="utf-8")
    (box / REEXPORT_SEED).write_text("LRN19_SEED = 1\n", encoding="utf-8")
    assert "_lrn19" not in (REPO / "core" / "learning" / "__init__.py").read_text(encoding="utf-8")
    return box


def test_lrn19_module_reached_only_by_the_package_reexport_is_unreachable(reexport_box):
    # positive control: the plain sweep counts the __init__'s import as reachability
    saved = zcs.REPO
    zcs.REPO = reexport_box.resolve()
    try:
        idx = zcs.Index(zcs.production_files())
        plain = {f.relative_to(zcs.REPO).as_posix() for f in zcs.reachable(idx)}
    finally:
        zcs.REPO = saved
    assert REEXPORT_SEED in plain, "control: the plain sweep must see the re-export"
    _, dead = lr.unreachable(reexport_box)
    assert REEXPORT_SEED in dead, "a module only core/learning/__init__ imports counted as reachable"
    # and a module that production code imports BY NAME through the package stays reachable
    assert "core/learning/integration.py" not in dead
