"""Direct OS-turn artifact scan: internal bookkeeping must never reach the chat.

Regression for the console chat ``web:ISGd-xIvqn`` (2026-07-27), where a single
turn emitted 144 artifact chips — 72× ``manifest.json`` and 72× ``result.json``
— because every model-chosen ``delegate_*`` MCP call writes a WDAT bookkeeping
run under ``<session>/acs/runs/<run_id>/`` (``corvin_delegate.delegation.
_write_wdat_run``) and the direct-turn scan diffed the WHOLE session workdir
with no exclusion. The ACS delegation branch had such a filter
(``_ACS_SKIP_DIRS``/``_ACS_SKIP_ROOT_FILES``) but applied it only relative to its
own run_dir, so the direct turn — which is what actually ran that chat — had
none at all.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from corvin_console import chat_runtime as cr  # noqa: E402


def _wdat_runs(workdir: Path, n: int) -> None:
    """Reproduce n delegate_* bookkeeping runs, exactly as the real chat had."""
    for i in range(n):
        d = workdir / "acs" / "runs" / f"acs-dlg-1785188748-{i:06x}"
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps(
            {"run_id": d.name, "workflow_id": "delegation:codex_cli"}))
        (d / "result.json").write_text(json.dumps(
            {"run_id": d.name, "status": "completed"}))


def test_wdat_bookkeeping_never_becomes_a_chat_artifact(tmp_path: Path) -> None:
    workdir = tmp_path / "session"
    workdir.mkdir()
    before: set[Path] = set(workdir.rglob("*"))

    _wdat_runs(workdir, 72)  # the exact count from web:ISGd-xIvqn

    parts, suppressed = cr._scan_turn_artifacts(workdir, before)
    assert parts == [], (
        f"internal delegate_* run records leaked into the chat as "
        f"{len(parts)} artifact chips")
    assert suppressed == 0, "excluded files must not count against the cap"


def test_real_outputs_still_surface_alongside_bookkeeping(tmp_path: Path) -> None:
    """The filter must be surgical: a genuine output file created in the same
    turn as the bookkeeping still reaches the chat, exactly once."""
    workdir = tmp_path / "session"
    (workdir / "outputs").mkdir(parents=True)
    before: set[Path] = set(workdir.rglob("*"))

    _wdat_runs(workdir, 30)
    (workdir / "outputs" / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (workdir / "report.md").write_text("# Report")

    parts, suppressed = cr._scan_turn_artifacts(workdir, before)
    names = [p["name"] for p in parts]
    assert sorted(names) == ["chart.png", "report.md"], names
    assert len(names) == len(set(names)), "an artifact must be emitted only once"
    assert suppressed == 0
    # Paths stay forward-slash (Windows serving-route contract).
    assert {p["path"] for p in parts} == {"outputs/chart.png", "report.md"}


def test_artifact_flood_is_capped_and_announced(tmp_path: Path) -> None:
    """Backstop independent of the skip-list: even legitimate files cannot
    flood the chat, and the truncation is reported rather than silent."""
    workdir = tmp_path / "session"
    (workdir / "outputs").mkdir(parents=True)
    before: set[Path] = set(workdir.rglob("*"))

    total = cr._MAX_TURN_ARTIFACTS + 17
    for i in range(total):
        (workdir / "outputs" / f"page_{i:03d}.md").write_text(f"# {i}")

    parts, suppressed = cr._scan_turn_artifacts(workdir, before)
    assert len(parts) == cr._MAX_TURN_ARTIFACTS
    assert suppressed == total - cr._MAX_TURN_ARTIFACTS


def test_session_internal_dirs_cover_the_runtime_trees(tmp_path: Path) -> None:
    f = cr._is_session_internal
    assert f(Path("acs/runs/acs-dlg-1/manifest.json")) is True
    assert f(Path("tasks/abc.events.jsonl")) is True
    assert f(Path("voice/tts_out.wav")) is True
    assert f(Path("tde/run-1/plan.json")) is True
    # A user output must never be mistaken for internal state.
    assert f(Path("outputs/chart.png")) is False
    assert f(Path("report.md")) is False


def test_truncation_notice_is_one_shared_shape_in_english() -> None:
    """Both artifact branches (direct scan + ACS fan-out) must emit the SAME
    notice, and it must be English.

    Two defects this pins, both found 2026-07-28:
      * the ACS branch `break`-ed at the cap and emitted no notice at all — the
        one place where a dropped artifact really did read as "the run produced
        nothing", contradicting the invariant stated on `_MAX_TURN_ARTIFACTS`;
      * the direct branch's message was a hard-coded German literal, while the
        repo rule is that user-facing runtime text defaults to English (a
        runtime notice has no model answer whose language it could follow).
    """
    evt = cr._artifacts_truncated_notice(emitted=20, suppressed=124)
    assert evt["type"] == "notice"
    assert evt["subtype"] == "artifacts_truncated"
    assert evt["emitted"] == 20
    assert evt["suppressed"] == 124
    assert "124" in evt["message"]
    assert str(cr._MAX_TURN_ARTIFACTS) in evt["message"]
    # No German literals: the first draft read "… und N weitere Dateien".
    assert "weitere" not in evt["message"]
    assert "werden" not in evt["message"]
