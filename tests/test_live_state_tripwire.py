"""A guard for the guard: the live-state tripwire must catch, and must not cry wolf.

`conftest.py`'s `_live_state_tripwire` is the repo's defence against the class of
bug that on 2026-07-08 deleted the running bridge's session state, budgets and
hash-chained audit log through a green pytest run. On 2026-07-28 it was found to
be reporting that destruction for two tests that had destroyed nothing:

    LIVE OPERATOR STATE DESTROYED by this test
      ~/.config/corvin-voice: was DELETED
      ~/.config/systemd/user corvin-* units: directory itself was DELETED

Both directories were fully intact (30 corvin-* units, every service running).
The cause was in the tripwire, not the tests: it re-resolved `Path.home()` for
its "after" snapshot, and both tests patch the home directory — one via
`monkeypatch.setenv("HOME", …)`, one via `monkeypatch.setattr(Path, "home", …)`,
which rebinds the same shared `pathlib.Path` class the tripwire uses. Their
monkeypatch teardown runs after the tripwire's, so the comparison looked into an
empty tmp home and saw everything "missing".

A guard that raises a destroyed-audit-log alarm on a clean run gets muted, and
then the real incident lands unannounced. So both directions are pinned here.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Load the REPO-ROOT conftest by path, not by name. A plain `import conftest`
# picks whatever already owns `sys.modules["conftest"]`, and this repo has ten
# of them; in a whole-suite run another one wins and every test here dies with
# `module 'conftest' has no attribute '_protected_state'`. Same same-name
# collision that made `--import-mode=importlib` necessary for the suites
# themselves (see .github/workflows/coverage.yml).
_ROOT_CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"
_spec = importlib.util.spec_from_file_location(
    "corvin_root_conftest_under_test", _ROOT_CONFTEST)
assert _spec and _spec.loader
root_conftest = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = root_conftest
_spec.loader.exec_module(root_conftest)


def _sandbox_paths(root: Path) -> "dict[str, Path]":
    """The tripwire's four roots, aimed at a sandbox tree.

    Passed per call rather than monkeypatched onto the module global: the
    tripwire fixture's own teardown runs while a monkeypatch is still active,
    so patching the global would make the guard read this sandbox and report
    every real entry as deleted — the same fixture-ordering trap it was
    mis-firing on in the first place.
    """
    return {
        "repo_corvin": root / "repo_corvin",
        "voice_config": root / "voice_config",
        "systemd_user": root / "systemd_user",
        "plugin_cache": root / "plugin_cache",
    }


def _build(root: Path) -> None:
    (root / "repo_corvin").mkdir(parents=True)
    (root / "repo_corvin" / "audit.jsonl").write_text("{}")
    (root / "repo_corvin" / "sessions").mkdir()
    (root / "voice_config").mkdir()
    (root / "systemd_user").mkdir()
    (root / "systemd_user" / "corvin-webui.service").write_text("x")
    (root / "plugin_cache").mkdir()


def test_frozen_paths_ignore_a_patched_home(monkeypatch, tmp_path: Path) -> None:
    """The regression itself: patching the home directory must not look like
    deletion. `_PROTECTED_PATHS` is resolved at import, so a later patch of
    `HOME` or `Path.home` cannot move the targets."""
    before = root_conftest._protected_state()
    assert before is not None, "snapshot unavailable on this host — nothing to pin"

    monkeypatch.setenv("HOME", str(tmp_path / "elsewhere"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "elsewhere"))

    after = root_conftest._protected_state()
    assert after == before, (
        "the tripwire followed a patched home directory — this is exactly the "
        "false positive that reported a deleted audit log on a clean run"
    )


def test_a_deleted_directory_is_still_detected(tmp_path: Path) -> None:
    """The guard must keep catching. Freezing the paths must not weaken it."""
    _build(tmp_path)
    paths = _sandbox_paths(tmp_path)

    before = root_conftest._protected_state(paths)
    assert before is not None
    assert before["~/.config/corvin-voice"] is True
    assert before["~/.config/systemd/user corvin-* units"] == frozenset(
        {"corvin-webui.service"})

    (tmp_path / "voice_config").rmdir()
    (tmp_path / "systemd_user" / "corvin-webui.service").unlink()

    after = root_conftest._protected_state(paths)
    assert after is not None
    assert after["~/.config/corvin-voice"] is False, "a deleted dir must be seen"
    assert after["~/.config/systemd/user corvin-* units"] == frozenset(), (
        "a deleted unit must be seen")


def test_a_deleted_repo_corvin_entry_is_still_detected(tmp_path: Path) -> None:
    """The 2026-07-08 shape: the tree survives, one entry inside it does not."""
    _build(tmp_path)
    paths = _sandbox_paths(tmp_path)

    before = root_conftest._protected_state(paths)
    assert before is not None
    assert "audit.jsonl" in before["repo .corvin top-level entries"]

    (tmp_path / "repo_corvin" / "audit.jsonl").unlink()

    after = root_conftest._protected_state(paths)
    assert after is not None
    gone = before["repo .corvin top-level entries"] - after["repo .corvin top-level entries"]
    assert gone == {"audit.jsonl"}, (
        "the audit chain disappearing is the incident this guard exists for")


def test_an_added_file_is_not_a_violation(tmp_path: Path) -> None:
    """A live bridge writes next to pytest in this repo; new files are normal."""
    _build(tmp_path)
    paths = _sandbox_paths(tmp_path)

    before = root_conftest._protected_state(paths)
    (tmp_path / "repo_corvin" / "fresh.jsonl").write_text("{}")
    after = root_conftest._protected_state(paths)

    assert before is not None and after is not None
    gone = before["repo .corvin top-level entries"] - after["repo .corvin top-level entries"]
    assert gone == frozenset(), "only DELETIONS are violations"
