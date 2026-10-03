"""Task completion registry — ADR scan, driven through the real CLI.

Every case runs ``python3 scripts/task_completion_registry.py`` as a subprocess
against a fixture ``decisions/`` dir and a temporary runtime root. ``HOME`` and
``CORVIN_HOME`` both point into ``tmp_path``, so no run can touch the live
``~/.corvin/task_registry.json``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "task_completion_registry.py"


def _adr(d: Path, name: str, adr_id: str | None, status: str | None, title: str) -> None:
    fm = []
    if adr_id is not None:
        fm.append(f"id: {adr_id}")
    if status is not None:
        fm.append(f"status: {status}")
    head = ("---\n" + "\n".join(fm) + "\n---\n\n") if fm else ""
    (d / name).write_text(f"{head}# {title}\n\nBody.\n", encoding="utf-8")


def _run(tmp: Path, adr: Path, *, via_env: bool = False) -> dict:
    home = tmp / "corvin_home"
    env = {**os.environ, "HOME": str(tmp / "home"), "CORVIN_HOME": str(home)}
    args = [sys.executable, str(_SCRIPT), "--adr-root", str(adr),
            "--repo-root", str(tmp / "no-repo"), "--memory-root", str(tmp / "no-memory")]
    if not via_env:
        args += ["--corvin-home", str(home)]
    proc = subprocess.run(args, capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads((home / "task_registry.json").read_text(encoding="utf-8"))["tasks"]


@pytest.fixture
def adr(tmp_path: Path) -> Path:
    d = tmp_path / "decisions"
    d.mkdir()
    return d


def test_unprefixed_files_reach_the_registry(tmp_path: Path, adr: Path) -> None:
    _adr(adr, "ADR-0100-prefixed.md", "ADR-0100", "ACCEPTED", "Prefixed")
    _adr(adr, "ADR-0014-admin-ui.md", "ADR-0014", "PROPOSED", "Admin UI")
    _adr(adr, "0207-edge-header.md", "ADR-0207", "ACCEPTED", "Edge header")
    tasks = _run(tmp_path, adr)
    assert tasks["adr_0100"]["status"] == "ACCEPTED"
    assert tasks["adr_0014"]["status"] == "IN_PROGRESS"
    assert tasks["adr_0207"]["status"] == "ACCEPTED"


def test_filename_number_is_the_fallback_key(tmp_path: Path, adr: Path) -> None:
    _adr(adr, "0255-no-frontmatter.md", None, None, "No frontmatter at all")
    _adr(adr, "ADR-0802-0511-overview.md", "ADR-0511-CONSOLE-PANEL", "PROPOSED", "Malformed id")
    tasks = _run(tmp_path, adr)
    assert tasks["adr_0255"]["status"] == "UNKNOWN"  # never done without a status
    assert tasks["adr_0802"]["adr_status"] == "PROPOSED"
    assert "adr_0511_console_panel" not in tasks


def test_non_decision_documents_are_skipped(tmp_path: Path, adr: Path) -> None:
    _adr(adr, "DOC-PHASE5_COMPLETION_SUMMARY.md", "ADR-0000", "ACCEPTED", "Report")
    _adr(adr, "ADR-0XXX-placeholder.md", "ADR-0XXX", "PROPOSED", "Placeholder")
    _adr(adr, "README.md", None, None, "Readme")
    _adr(adr, "ADR-0300-real.md", "ADR-0300", "PROPOSED", "Real")
    tasks = _run(tmp_path, adr)
    assert set(tasks) == {"adr_0300"}


@pytest.mark.parametrize("names", [("ADR-0407-a.md", "ADR-0407-z.md"), ("ADR-0407-z.md", "ADR-0407-a.md")])
def test_two_live_siblings_that_disagree_read_as_open(tmp_path: Path, adr: Path, names) -> None:
    done_name, open_name = names  # the ACCEPTED file sorts first in one case, last in the other
    _adr(adr, done_name, "ADR-0407", "ACCEPTED", "Done sibling")
    _adr(adr, open_name, "ADR-0407", "PROPOSED", "Open sibling")
    t = _run(tmp_path, adr)["adr_0407"]
    assert t["status"] == "IN_PROGRESS"
    assert t["adr_status"] == "PROPOSED"
    assert "2 files carry this number" in t["notes"]


def test_superseded_sibling_is_ignored_while_a_live_one_exists(tmp_path: Path, adr: Path) -> None:
    _adr(adr, "0500-old.md", "ADR-0500", "SUPERSEDED by ADR-0600", "Old")
    _adr(adr, "ADR-0500-new.md", "ADR-0500", "ACCEPTED", "New")
    t = _run(tmp_path, adr)["adr_0500"]
    assert t["status"] == "ACCEPTED"
    assert "ADR-0500-new.md" in t["notes"]


def test_only_superseded_siblings_read_as_archived(tmp_path: Path, adr: Path) -> None:
    _adr(adr, "0501-old.md", "ADR-0501", "SUPERSEDED", "Old")
    assert _run(tmp_path, adr)["adr_0501"]["status"] == "ARCHIVED"


def test_corvin_home_env_selects_the_output(tmp_path: Path, adr: Path) -> None:
    _adr(adr, "ADR-0100-x.md", "ADR-0100", "ACCEPTED", "X")
    assert _run(tmp_path, adr, via_env=True)["adr_0100"]["status"] == "ACCEPTED"
    assert not (tmp_path / "home" / ".corvin").exists()


# ── Parity with the task-tracking sync (adr_meta) ─────────────────────────────
#
# The registry and corvin_console.task_tracking_git_sync.adr_meta must give every
# decision number the same state: same key (the FILE NAME's number), same
# sibling choice, same done vocabulary.

_REAL_ADR_ROOT = Path("/home/shumway/projects/Corvin-Knowledge")
_STATE = {"ACCEPTED": "complete", "ARCHIVED": "archived", "IN_PROGRESS": "in_progress", "UNKNOWN": "in_progress"}


def _sync():
    for p in (str(_REPO), str(_REPO / "core" / "console")):
        if p not in sys.path:
            sys.path.insert(0, p)
    from corvin_console.task_tracking_git_sync import adr_meta, status_for  # noqa: PLC0415

    return adr_meta, status_for


def _filename_numbers(decisions: Path) -> set[str]:
    """Every number adr_meta can find: one a file name carries."""
    import re  # noqa: PLC0415

    nums = set()
    for f in decisions.glob("*.md"):
        m = re.match(r"^(?:ADR-)?(\d{4})(?:-|$)", f.stem)
        if m and m.group(1) != "0000":
            nums.add(f"ADR-{m.group(1)}")
    return nums


def _disagreements(decisions: Path, tasks: dict) -> tuple[int, list]:
    """``(numbers compared, [mismatch...])`` between the registry and adr_meta:
    same state AND the same deciding file for every number."""
    adr_meta, status_for = _sync()
    reg = {t["adr_id"]: t for t in tasks.values()}
    by_name = _filename_numbers(decisions)
    bad = []
    for n in sorted(by_name | set(reg)):
        meta = adr_meta(n, decisions.parent)
        r = reg.get(n)
        if meta is None:
            # Only a number no file name carries (frontmatter fallback) may be
            # registered without adr_meta seeing it.
            if n in by_name:
                bad.append((n, r and r["status"], None))
            continue
        want = status_for(meta["status"])
        got = _STATE[r["status"]] if r else None
        if got != want or f"status read from {meta['file']}" not in r["notes"]:
            bad.append((n, r and r["status"], want, meta["file"], r and r["notes"]))
    return len(by_name | set(reg)), bad


def test_registry_state_matches_adr_meta_on_a_crafted_corpus(tmp_path: Path, adr: Path) -> None:
    # Renumbered file still carrying its OLD id: it is ADR-0800, not ADR-0472.
    _adr(adr, "ADR-0800-0472-renumbered.md", "ADR-0472", "ACCEPTED", "Renumbered")
    _adr(adr, "ADR-0472-current.md", "ADR-0472", "REJECTED", "Current 0472")
    # A three-digit file name is not a number; its id must not outvote 0001-*.
    _adr(adr, "ADR-001-three-digits.md", "ADR-0001", "PROPOSED", "Three digits")
    _adr(adr, "0001-four-digits.md", "ADR-0001", "ACCEPTED", "Four digits")
    # Underscore names carry no file-name number; the fallback never joins a
    # number a file name already carries.
    _adr(adr, "adr_0801_system.md", "ADR-0801", "PROPOSED", "Underscore twin")
    _adr(adr, "ADR-0801-0472-gaps.md", "ADR-0472", "IMPLEMENTED", "Gaps")
    # Lone IMPLEMENTED is done; mixed naming schemes order like adr_meta.
    _adr(adr, "ADR-0405-impl.md", "ADR-0405", "IMPLEMENTED", "Implemented")
    _adr(adr, "0406-old-scheme.md", None, "DEPLOYED", "Old scheme")
    _adr(adr, "ADR-0406-new-scheme.md", "ADR-0406", "ACCEPTED", "New scheme")
    _adr(adr, "ADR-0407.md", "ADR-0407", "PROPOSED", "Bare name")
    _adr(adr, "ADR-0407-slug.md", "ADR-0407", "ACCEPTED", "Slugged")
    # A number only a frontmatter id carries is still registered.
    _adr(adr, "adr_0950_only.md", "ADR-0950", "ACCEPTED", "Fallback only")
    tasks = _run(tmp_path, adr)

    assert tasks["adr_0800"]["status"] == "ACCEPTED"
    assert tasks["adr_0472"]["status"] == "ARCHIVED"
    assert tasks["adr_0001"]["status"] == "ACCEPTED"
    assert "ADR-001-three-digits.md" not in tasks["adr_0001"]["notes"]
    assert tasks["adr_0801"]["status"] == "ACCEPTED"
    assert "adr_0801_system.md" not in tasks["adr_0801"]["notes"]
    assert tasks["adr_0405"]["status"] == "ACCEPTED"
    assert tasks["adr_0950"]["status"] == "ACCEPTED"

    count, bad = _disagreements(adr, tasks)
    assert bad == [], bad
    assert count == 8


@pytest.mark.parametrize("names", [("ADR-0410-a.md", "ADR-0410-z.md"), ("ADR-0410-z.md", "ADR-0410-a.md")])
def test_accepted_and_implemented_siblings_read_done_in_either_order(tmp_path: Path, adr: Path, names) -> None:
    accepted, implemented = names
    _adr(adr, accepted, "ADR-0410", "ACCEPTED", "Accepted")
    _adr(adr, implemented, "ADR-0410", "IMPLEMENTED", "Implemented")
    tasks = _run(tmp_path, adr)
    assert tasks["adr_0410"]["status"] == "ACCEPTED"
    assert _disagreements(adr, tasks)[1] == []


@pytest.mark.skipif(not (_REAL_ADR_ROOT / "decisions").is_dir(), reason="no Corvin-Knowledge checkout on this host")
def test_registry_agrees_with_adr_meta_on_the_real_corpus(tmp_path: Path, capsys) -> None:
    """Read-only against the real record repo: the registry is written to tmp_path."""
    decisions = _REAL_ADR_ROOT / "decisions"
    tasks = _run(tmp_path, decisions)
    count, bad = _disagreements(decisions, tasks)
    with capsys.disabled():
        print(f"\n[real corpus] {count} numbers, {count - len(bad)} agree, {len(bad)} disagree")
    assert bad == [], bad[:20]
