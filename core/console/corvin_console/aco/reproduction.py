"""ACO Layer 6 — the reproduction proof gate (ADR-0179).

The hard guarantee the maintainer asked for: **code is only ever changed when the
bug is genuinely proven AND the fix is proven to work.** A plausible-looking
patch is not enough; the loop must SEE the bug fail and SEE the fix turn it green
without breaking anything else.

A candidate patch MUST contain two kinds of edits:
  * a NEW regression test (path under ``tests/`` or basename ``test_*.py``)
  * the actual fix (one or more non-test source edits)

Then this gate runs three stages on a CLEAN repo worktree, reverting between each:

  A. write the test ONLY  → run it → it MUST FAIL.
        (proves the test actually reproduces the bug; a test that's green on
         unpatched code proves nothing → reject.)
  B. write test + fix      → run it → it MUST PASS.
        (proves the fix resolves the reported bug.)
  C. run the FULL suite    → it MUST PASS.
        (proves the fix introduced no regression.)

Only A-red ∧ B-green ∧ C-green ⇒ proven. Anything else ⇒ not proven ⇒ no commit,
no PR. The gate leaves the worktree exactly as it found it (clean at HEAD).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

# A test edit: lives under a tests/ dir OR its file basename is test_*.py / *_test.py
_TEST_PATH = re.compile(r"(^|/)tests?/|(^|/)test_[^/]+\.py$|(^|/)[^/]+_test\.py$")


def is_test_path(rel: str) -> bool:
    return bool(_TEST_PATH.search(rel.replace("\\", "/")))


def split_edits(patch) -> tuple[list, list]:
    """(test_edits, fix_edits) by path. A patch needs at least one of each."""
    tests, fixes = [], []
    for e in patch.edits:
        (tests if is_test_path(e.path) else fixes).append(e)
    return tests, fixes


@dataclass
class ReproResult:
    proven: bool
    detail: str
    stage_a_red: bool = False   # test failed on unpatched code (good)
    stage_b_green: bool = False  # test passed after fix (good)
    stage_c_green: bool = False  # full suite stayed green (good)

    def to_dict(self) -> dict:
        return {"proven": self.proven, "detail": self.detail,
                "stage_a_red": self.stage_a_red, "stage_b_green": self.stage_b_green,
                "stage_c_green": self.stage_c_green}


def _git(repo: Path, *args: str) -> tuple[int, str]:
    import subprocess
    try:
        p = subprocess.run(["git", "-C", str(repo), *args],
                           capture_output=True, text=True, timeout=120)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def _apply(repo: Path, edits) -> None:
    for e in edits:
        t = (repo / e.path)
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_text(e.new_content, encoding="utf-8")


def _revert(repo: Path) -> None:
    # Drop tracked-file changes AND any new untracked files the patch wrote, so
    # the next stage starts from a pristine HEAD. Scoped to the repo worktree.
    _git(repo, "checkout", "--", ".")
    _git(repo, "clean", "-fdq")


def reproduction_gate(
    repo: str | Path, patch, *,
    test_runner: Callable[[list[str]], tuple[int, str]],
    full_runner: Callable[[], tuple[int, str]],
) -> ReproResult:
    """Prove the bug and the fix. NEVER raises; returns proven=False on any doubt
    (deny-by-default). Requires a clean worktree and leaves it clean.

    ``test_runner``/``full_runner`` return ``(pytest_returncode, output)``. pytest
    exit codes are NOT binary: 0=passed, 1=failures, 2=collection/import error,
    4=usage error, 5=no tests collected. Stage A demands a GENUINE failure
    (rc==1) — a test that errors (2), can't be found (4), or collects nothing (5)
    on unpatched code is NOT a reproduction and is rejected. This closes the
    "new-feature-smuggled-as-bug-fix" bypass (a test importing a not-yet-existing
    symbol errors with rc=2; without this check the gate would read it as red)."""
    repo = Path(repo)
    tests, fixes = split_edits(patch)
    if not tests:
        return ReproResult(False, "no regression test in patch — cannot prove the bug")
    if not fixes:
        return ReproResult(False, "no fix edit in patch — nothing to verify")

    rc, porcelain = _git(repo, "status", "--porcelain")
    if rc != 0 or porcelain.strip():
        return ReproResult(False, "worktree not clean — refusing to run reproduction gate")

    test_targets = [e.path for e in tests]
    try:
        # ── Stage A: test only → must FAIL GENUINELY (rc==1), not error/empty ───
        _apply(repo, tests)
        a_rc, a_out = test_runner(test_targets)
        _revert(repo)
        if a_rc == 0:
            return ReproResult(False,
                               "stage A: regression test PASSED on unpatched code — "
                               "it does not reproduce the bug",
                               stage_a_red=False)
        if a_rc != 1:
            # 2=collection/import error, 4=usage, 5=no tests collected → the test
            # did not genuinely fail; it is broken or reproduces nothing. Reject.
            return ReproResult(False,
                               f"stage A: regression test did not genuinely FAIL on "
                               f"unpatched code (pytest rc={a_rc}, not 1) — it errors or "
                               f"collects nothing, so it proves no bug: {a_out[:140]}",
                               stage_a_red=False)

        # ── Stage B: test + fix → must PASS (rc==0) ─────────────────────────────
        _apply(repo, tests + fixes)
        b_rc, b_out = test_runner(test_targets)
        if b_rc != 0:
            _revert(repo)
            return ReproResult(False,
                               f"stage B: fix did NOT make the test pass (rc={b_rc}): {b_out[:140]}",
                               stage_a_red=True, stage_b_green=False)

        # ── Stage C: full suite → must stay GREEN (rc==0, no regression) ────────
        c_rc, c_out = full_runner()
        _revert(repo)
        if c_rc != 0:
            return ReproResult(False,
                               f"stage C: fix passed its test but the full suite is not "
                               f"green (rc={c_rc}, regression): {c_out[:140]}",
                               stage_a_red=True, stage_b_green=True, stage_c_green=False)

        return ReproResult(True,
                           "proven: test red on unpatched code, green after fix, "
                           "full suite green (no regression)",
                           stage_a_red=True, stage_b_green=True, stage_c_green=True)
    finally:
        _revert(repo)  # belt-and-suspenders: never leave the tree dirty


def build_repro_runner(repo: str | Path, *, full_cmd: list[str],
                       python: Optional[str] = None):
    """Bind ``reproduction_gate`` to a worktree + pytest. Returns a
    ``Callable[[Patch], ReproResult]`` for ``run_maintenance_loop(repro_runner=...)``.

    * test_runner runs pytest on JUST the patch's new test file(s).
    * full_runner runs ``full_cmd`` for the regression check — this MUST be the
      project's BROAD suite (not the narrow gate file), or stage C cannot detect a
      regression outside the patched test's coverage.

    Both return the raw pytest return code (0 pass / 1 fail / 2,4,5 error), so the
    gate can require a genuine rc==1 failure in stage A. A subprocess exception →
    rc=-1 (neither pass nor genuine-fail → the gate rejects)."""
    import subprocess
    import sys as _sys
    repo = Path(repo)
    py = python or _sys.executable

    def _run(cmd: list[str]) -> tuple[int, str]:
        try:
            p = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True, timeout=1800)
            return p.returncode, (p.stdout + p.stderr)[-400:]
        except Exception as exc:  # noqa: BLE001
            return -1, str(exc)[:200]

    def _test_runner(targets: list[str]) -> tuple[int, str]:
        return _run([py, "-m", "pytest", *targets, "-q"])

    def _full_runner() -> tuple[int, str]:
        return _run(full_cmd)

    def _runner(patch):
        return reproduction_gate(repo, patch, test_runner=_test_runner,
                                 full_runner=_full_runner)

    return _runner
