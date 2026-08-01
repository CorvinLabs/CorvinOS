"""E2E coverage for scripts/adr_backfill.py, the ADR-0264 frontmatter
backfill extractor used to migrate the pre-convention Corvin-ADR corpus.

Per adr_gate Step 5, this exercises the real extraction logic against real,
hermetic fixture files (a tmp decisions/ dir + a tmp git repo standing in
for Corvin-ADR, never mocked) plus real subprocess CLI runs -- the same
discipline as tests/test_adr_0264_decision_graph.py. The one thing NOT
retested here is scripts.adr_graph itself (covered by its own suite);
this file is specifically about what the extractor decides to write, and
crucially, what it correctly refuses to write.

Run: python3 -m pytest tests/test_adr_0264_backfill.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from scripts.adr_backfill import (  # noqa: E402
    analyze,
    find_id_collisions,
    render_frontmatter,
)
from scripts.adr_graph import load_graph  # noqa: E402

_SCRIPT = _REPO / "scripts" / "adr_backfill.py"


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


def _write_and_commit(repo: Path, rel_path: str, content: str, message: str) -> None:
    p = repo / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", rel_path], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


@pytest.fixture
def adr_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "Corvin-ADR"
    (repo / "decisions").mkdir(parents=True)
    _init_git_repo(repo)
    return repo


@pytest.fixture
def corvin_repo(tmp_path: Path) -> Path:
    """Stands in for the CorvinOS repo -- adr_backfill greps ITS commit log
    for `commits:`, a genuinely separate repo from Corvin-ADR."""
    repo = tmp_path / "CorvinOS"
    repo.mkdir()
    _init_git_repo(repo)
    _write_and_commit(repo, "README.md", "placeholder\n", "chore: init")
    return repo


class TestStatusExtraction:
    def test_known_status_word_is_normalized(self, adr_repo, corvin_repo):
        f = adr_repo / "decisions" / "0100-a.md"
        f.write_text("# ADR-0100 — A\n\n**Status:** Accepted\n\nBody.\n", encoding="utf-8")
        r = analyze(f, corvin_repo)
        assert r.status == "accepted"

    def test_missing_status_line_is_unknown_not_defaulted(self, adr_repo, corvin_repo):
        f = adr_repo / "decisions" / "0100-a.md"
        f.write_text("# ADR-0100 — A\n\nNo status line at all.\n", encoding="utf-8")
        r = analyze(f, corvin_repo)
        assert r.status == "unknown"

    def test_unrecognized_status_word_is_unknown_not_guessed(self, adr_repo, corvin_repo):
        f = adr_repo / "decisions" / "0100-a.md"
        f.write_text("# ADR-0100 — A\n\n**Status:** WaitingOnLegal\n", encoding="utf-8")
        r = analyze(f, corvin_repo)
        assert r.status == "unknown"


class TestSupersedesAndDependsOnRequireExplicitPhrasing:
    def test_bare_mention_does_not_become_a_dependency(self, adr_repo, corvin_repo):
        """The core safety property: co-occurrence is not evidence."""
        f = adr_repo / "decisions" / "0100-a.md"
        f.write_text(
            "# ADR-0100 — A\n\nSee also ADR-0050 for background context.\n",
            encoding="utf-8",
        )
        r = analyze(f, corvin_repo)
        assert r.depends_on == []
        assert r.supersedes == []
        assert r.related == ["ADR-0050"]

    def test_hedged_supersedes_line_is_not_treated_as_an_edge(self, adr_repo, corvin_repo):
        """Real corpus example: '**Supersedes**: nothing (additive to
        ADR-0007)' must NOT produce supersedes: [ADR-0007]."""
        f = adr_repo / "decisions" / "0100-a.md"
        f.write_text(
            "# ADR-0100 — A\n\n**Supersedes**: nothing (additive to ADR-0007)\n",
            encoding="utf-8",
        )
        r = analyze(f, corvin_repo)
        assert r.supersedes == []
        assert "ADR-0007" in r.related

    def test_explicit_depends_on_phrase_is_captured(self, adr_repo, corvin_repo):
        f = adr_repo / "decisions" / "0100-a.md"
        f.write_text(
            "# ADR-0100 — A\n\nThis metric dashboard builds on\nADR-0050 Phase 6.\n",
            encoding="utf-8",
        )
        r = analyze(f, corvin_repo)
        assert r.depends_on == ["ADR-0050"]
        assert "ADR-0050" not in r.related  # captured once, not double-counted

    def test_explicit_supersedes_phrase_is_captured(self, adr_repo, corvin_repo):
        f = adr_repo / "decisions" / "0100-a.md"
        f.write_text(
            "# ADR-0100 — A\n\nThis decision supersedes ADR-0050 entirely.\n",
            encoding="utf-8",
        )
        r = analyze(f, corvin_repo)
        assert r.supersedes == ["ADR-0050"]

    def test_superseded_by_phrasing_is_not_read_as_supersedes(self, adr_repo, corvin_repo):
        """'superseded BY X' means X supersedes THIS file, the opposite
        direction of 'supersedes X' -- must not be conflated."""
        f = adr_repo / "decisions" / "0100-a.md"
        f.write_text(
            "# ADR-0100 — A\n\nThis approach was superseded by ADR-0200.\n",
            encoding="utf-8",
        )
        r = analyze(f, corvin_repo)
        assert r.supersedes == []
        assert "ADR-0200" in r.related


class TestAlreadyFrontmatteredFilesAreSkipped:
    def test_file_with_existing_frontmatter_is_not_reanalyzed(self, adr_repo, corvin_repo):
        f = adr_repo / "decisions" / "0100-a.md"
        f.write_text("---\nid: ADR-0100\nstatus: accepted\n---\n\n# ADR-0100 — A\n",
                      encoding="utf-8")
        r = analyze(f, corvin_repo)
        assert r.skipped_reason == "already has frontmatter"


class TestIdCollisionDetection:
    def test_two_files_sharing_a_number_are_both_flagged(self, tmp_path):
        d = tmp_path / "decisions"
        d.mkdir()
        f1 = d / "0100-first.md"
        f2 = d / "0100-second.md"
        f1.write_text("# ADR-0100 — First\n", encoding="utf-8")
        f2.write_text("# ADR-0100 — Second\n", encoding="utf-8")
        colliding_ids, collision_files = find_id_collisions([f1, f2])
        assert colliding_ids == {"0100"}
        assert collision_files == {f1, f2}

    def test_unique_numbers_collide_with_nothing(self, tmp_path):
        d = tmp_path / "decisions"
        d.mkdir()
        f1 = d / "0100-first.md"
        f2 = d / "0101-second.md"
        f1.write_text("# ADR-0100 — First\n", encoding="utf-8")
        f2.write_text("# ADR-0101 — Second\n", encoding="utf-8")
        colliding_ids, collision_files = find_id_collisions([f1, f2])
        assert colliding_ids == set()
        assert collision_files == set()


class TestRenderedFrontmatterIsValidAndMarkedBackfilled:
    def test_rendered_output_parses_through_the_real_adr_graph_parser(self, adr_repo, corvin_repo):
        f = adr_repo / "decisions" / "0100-a.md"
        f.write_text("# ADR-0100 — A\n\n**Status:** Proposed\n", encoding="utf-8")
        r = analyze(f, corvin_repo)
        fm_text = render_frontmatter(r, "2026-08-01")

        from scripts.adr_graph import _parse_frontmatter
        parsed = _parse_frontmatter(fm_text + "\n# ADR-0100 — A\n")
        assert parsed["id"] == "ADR-0100"
        assert parsed["backfilled"] is True
        # YAML parses an unquoted YYYY-MM-DD as a date object, not a str --
        # correct, expected safe_load behaviour, not a bug in the tool.
        assert str(parsed["backfill_date"]) == "2026-08-01"
        assert parsed["paths"] == []


class TestRealCliEndToEnd:
    """Real subprocess runs against a real fixture repo -- not direct
    function calls -- so a regression in the CLI wiring itself is caught."""

    def test_dry_run_writes_nothing(self, adr_repo, corvin_repo):
        f = adr_repo / "decisions" / "0100-a.md"
        original = "# ADR-0100 — A\n\n**Status:** Accepted\n"
        f.write_text(original, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(_SCRIPT),
             "--decisions-dir", str(adr_repo / "decisions"),
             "--adr-repo", str(adr_repo), "--corvin-repo", str(corvin_repo)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert f.read_text(encoding="utf-8") == original  # untouched

    def test_write_then_second_run_is_a_clean_no_op(self, adr_repo, corvin_repo):
        f = adr_repo / "decisions" / "0100-a.md"
        f.write_text("# ADR-0100 — A\n\n**Status:** Accepted\n", encoding="utf-8")

        common_args = ["--decisions-dir", str(adr_repo / "decisions"),
                        "--adr-repo", str(adr_repo), "--corvin-repo", str(corvin_repo)]

        r1 = subprocess.run([sys.executable, str(_SCRIPT), "--write", *common_args],
                             capture_output=True, text=True, timeout=30)
        assert r1.returncode == 0, r1.stderr
        assert "Wrote frontmatter to 1 file" in r1.stdout

        r2 = subprocess.run([sys.executable, str(_SCRIPT), "--write", *common_args],
                             capture_output=True, text=True, timeout=30)
        assert r2.returncode == 0, r2.stderr
        assert "0 will be backfilled" in r2.stdout
        assert "1 already have frontmatter" in r2.stdout

        nodes = load_graph(adr_repo / "decisions")
        assert nodes["ADR-0100"].status == "accepted"

    def test_dirty_file_is_never_touched(self, adr_repo, corvin_repo):
        """A file with uncommitted local changes (standing in for a
        parallel session's in-progress work) must survive a --write run
        completely unmodified."""
        f = adr_repo / "decisions" / "0100-a.md"
        _write_and_commit(adr_repo, "decisions/0100-a.md",
                           "# ADR-0100 — A\n\n**Status:** Accepted\n", "add ADR")
        # Make it dirty: an uncommitted local edit.
        dirty_content = "# ADR-0100 — A\n\n**Status:** Accepted\n\nWIP EDIT.\n"
        f.write_text(dirty_content, encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "--write",
             "--decisions-dir", str(adr_repo / "decisions"),
             "--adr-repo", str(adr_repo), "--corvin-repo", str(corvin_repo)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert f.read_text(encoding="utf-8") == dirty_content  # bit-for-bit unchanged
        assert "1 dirty" in result.stdout

    def test_colliding_ids_are_never_written(self, adr_repo, corvin_repo):
        f1 = adr_repo / "decisions" / "0100-first.md"
        f2 = adr_repo / "decisions" / "0100-second.md"
        c1, c2 = "# ADR-0100 — First\n", "# ADR-0100 — Second\n"
        f1.write_text(c1, encoding="utf-8")
        f2.write_text(c2, encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "--write",
             "--decisions-dir", str(adr_repo / "decisions"),
             "--adr-repo", str(adr_repo), "--corvin-repo", str(corvin_repo)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert f1.read_text(encoding="utf-8") == c1
        assert f2.read_text(encoding="utf-8") == c2
        assert "id-collisions" in result.stdout

    def test_limit_applies_after_collision_filtering_not_before(self, adr_repo, corvin_repo):
        """A --limit N must never accidentally admit a colliding file just
        because it sorted early -- collisions are filtered corpus-wide
        first, then the limit is applied to what remains."""
        (adr_repo / "decisions" / "0001-collide.md").write_text(
            "# ADR-0001 — Collide A\n", encoding="utf-8")
        (adr_repo / "decisions" / "0001-also-collide.md").write_text(
            "# ADR-0001 — Collide B\n", encoding="utf-8")
        (adr_repo / "decisions" / "0002-clean.md").write_text(
            "# ADR-0002 — Clean\n", encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "--write", "--limit", "1",
             "--decisions-dir", str(adr_repo / "decisions"),
             "--adr-repo", str(adr_repo), "--corvin-repo", str(corvin_repo)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        nodes = load_graph(adr_repo / "decisions")
        assert "ADR-0002" in nodes
        assert "ADR-0001" not in nodes  # both collision files stayed untouched
