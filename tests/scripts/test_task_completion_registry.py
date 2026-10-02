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
    _adr(adr, "0014-admin-ui.md", "ADR-0014", "PROPOSED", "Admin UI")
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
