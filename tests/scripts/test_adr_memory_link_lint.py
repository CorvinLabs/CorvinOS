"""ADR <-> memory link lint (ADR-2101 §5) — driven through the real CLI.

Every case runs ``python3 scripts/adr_memory_link_lint.py`` as a subprocess
against a fixture memory dir and a fixture ADR dir, so the argument parsing,
exit codes and output format are what is tested, not an imported function.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_LINT = _REPO / "scripts" / "adr_memory_link_lint.py"


def _adr(d: Path, name: str, adr_id: str, status: str, title: str) -> None:
    (d / name).write_text(
        f"---\nid: {adr_id}\nstatus: {status}\n---\n\n# {adr_id} — {title}\n\nBody.\n",
        encoding="utf-8")


@pytest.fixture
def dirs(tmp_path: Path):
    adr = tmp_path / "decisions"
    mem = tmp_path / "memory"
    adr.mkdir()
    mem.mkdir()
    _adr(adr, "ADR-0100-goal-alignment-gate.md", "ADR-0100", "ACCEPTED", "Goal Alignment Gate")
    _adr(adr, "ADR-0200-session-bridge.md", "ADR-0200", "PROPOSED", "Session Context Bridge")
    _adr(adr, "ADR-0300-plugin-extraction.md", "ADR-0300", "PROPOSED", "Workflows Plugin Extraction")
    _adr(adr, "ADR-0300-other-topic.md", "ADR-0300", "PROPOSED", "Something Else Entirely")
    _adr(adr, "ADR-0400-0401-renumbered.md", "ADR-0401", "ACCEPTED", "Renumbered Decision")
    return mem, adr


def _run(mem: Path, adr: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_LINT), "--memory-dir", str(mem), "--adr-dir", str(adr), *extra],
        capture_output=True, text=True, timeout=60)


def _note(mem: Path, name: str, body: str) -> None:
    (mem / name).write_text(f"---\nname: {name}\ndescription: ADR-9999 in frontmatter\n---\n\n{body}",
                            encoding="utf-8")


def test_clean_memory_exits_zero(dirs):
    mem, adr = dirs
    _note(mem, "ok.md", "ADR-0100 (goal alignment gate) is ACCEPTED ✅\n")
    r = _run(mem, adr)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "0 errors, 0 warnings" in r.stdout


def test_dangling_citation_is_an_error(dirs):
    mem, adr = dirs
    _note(mem, "n.md", "See ADR-0777 for the plan.\n")
    r = _run(mem, adr)
    assert r.returncode == 1
    assert "n.md:6: error: DANGLING ADR-0777" in r.stdout


def test_status_conflict_done_claim_on_proposed_adr(dirs):
    mem, adr = dirs
    _note(mem, "n.md", "- ADR-0200 session bridge ✅ DONE\n")
    r = _run(mem, adr)
    assert r.returncode == 1
    assert "STATUS_CONFLICT line claims ADR-0200 done, its frontmatter says PROPOSED" in r.stdout


def test_status_conflict_open_claim_on_accepted_adr(dirs):
    mem, adr = dirs
    _note(mem, "n.md", "- ADR-0100 still NOT WIRED\n")
    r = _run(mem, adr)
    assert "STATUS_CONFLICT line claims ADR-0100 open, its frontmatter says ACCEPTED" in r.stdout


def test_subject_shift_is_not_a_status_claim(dirs):
    mem, adr = dirs
    _note(mem, "n.md", "- Blocker: ADR-0200 violation → RESOLVED ✅\n"
                       "- Fail-open gate violated ADR-0100 invariant\n")
    r = _run(mem, adr)
    assert "STATUS_CONFLICT" not in r.stdout, r.stdout


def test_multi_adr_line_is_not_status_checked(dirs):
    mem, adr = dirs
    _note(mem, "n.md", "ADR-0100 and ADR-0200 both ✅\n")
    r = _run(mem, adr)
    assert "STATUS_CONFLICT" not in r.stdout


def test_ambiguous_id_reported_once_per_file(dirs):
    mem, adr = dirs
    _note(mem, "n.md", "ADR-0300 first\nADR-0300 second\n")
    r = _run(mem, adr)
    assert r.stdout.count("AMBIGUOUS_ID ADR-0300") == 1
    assert r.returncode == 0  # warnings only


def test_description_mismatch_and_digit_metadata_skipped(dirs):
    mem, adr = dirs
    _adr(adr, "ADR-0500-workflows-plugin-extraction.md", "ADR-0500", "ACCEPTED",
         "Workflows Plugin Extraction")
    _note(mem, "n.md", "ADR-0500 (task-id canonicalization)\n"
                       "ADR-0500 (2026-07-20, commit 561f77e)\n"
                       "ADR-0500 (workflow plugin split)\n")
    r = _run(mem, adr)
    assert r.stdout.count("DESCRIPTION_MISMATCH") == 1
    assert "described as 'task-id canonicalization'" in r.stdout


def test_filename_only_number_is_reported_not_dangling(dirs):
    mem, adr = dirs
    _note(mem, "n.md", "ADR-0400 shipped\n")
    r = _run(mem, adr)
    assert "DANGLING" not in r.stdout
    assert "ID_FILENAME_MISMATCH ADR-0400 resolves only through a file name; " \
           "its frontmatter id is ADR-0401" in r.stdout


def test_archive_frontmatter_and_index_are_skipped(dirs):
    mem, adr = dirs
    (mem / "archive").mkdir()
    (mem / "archive" / "old.md").write_text("ADR-0777 ✅\n", encoding="utf-8")
    (mem / "MEMORY.md").write_text("- ADR-0777 index line\n", encoding="utf-8")
    _note(mem, "n.md", "nothing cited here\n")  # frontmatter cites ADR-9999
    r = _run(mem, adr)
    assert r.returncode == 0, r.stdout
    assert "1 memory files, 0 ADR citations" in r.stdout


def test_strict_fails_on_warnings(dirs):
    mem, adr = dirs
    _note(mem, "n.md", "ADR-0300 mentioned\n")
    assert _run(mem, adr).returncode == 0
    assert _run(mem, adr, "--strict").returncode == 1


def test_json_report(dirs):
    mem, adr = dirs
    _note(mem, "n.md", "ADR-0777\n")
    r = _run(mem, adr, "--json")
    data = json.loads(r.stdout)
    assert data["errors"] == 1
    assert data["findings"][0]["code"] == "DANGLING"
    assert data["findings"][0]["line"] == 6


def test_missing_directory_exits_two(tmp_path):
    r = subprocess.run([sys.executable, str(_LINT), "--memory-dir", str(tmp_path / "nope"),
                        "--adr-dir", str(tmp_path)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 2
    assert "memory directory not found" in r.stderr
