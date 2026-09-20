#!/usr/bin/env python3
"""test_claude_auth_probe_parity.py — cross-copy guard for the Claude Code auth probe.

There are FOUR independent answers in this tree to "is Claude Code usable on this
host". They must agree, because each one gates a different user-visible feature:

  * ``chat_runtime.py::_claude_authenticated``      — console chat engine choice
  * ``summarize.py::_claude_authenticated``         — the VOICE SUMMARY backend
  * ``dialectic.py::_claude_authenticated``         — the dialectic judge
  * ``engine_detection.py::probe_claude_code``      — the canonical detector

On a 3rd-party-platform install (Amazon Bedrock / Google Vertex / Microsoft
Foundry) auth is carried by the PLATFORM's own credentials (AWS/GCP/Azure).
There is NO ``ANTHROPIC_API_KEY`` and NO ``~/.claude/.credentials.json``, and the
setup wizards write ``CLAUDE_CODE_USE_*`` into settings.json's ``env`` block
rather than exporting it into the shell. A probe that checks only the API key and
the OAuth file therefore returns False on a perfectly working install.

Consequence when a copy drifts (measured live 2026-09-20, ALLIANZDE): the voice
summarizer skipped the `claude` backend WITHOUT EVER SPAWNING IT, fell through to
an unreachable Ollama, and every spoken summary degraded to a near-verbatim echo
of the answer. The dialectic judge degraded to thesis-only. Console chat was
fine — which is exactly what makes this class of drift so hard to see.

HOW THE DRIFT HAPPENED, and why this file asserts paths and not just behaviour:
commit c7ea7449 (2026-09-15) fixed the probe under the then-current ``operator/``
tree. That tree was later renamed to ``corvin_operator/`` across two unrelated
commits, and the new path was populated with PRE-FIX copies of the files instead
of a ``git mv``. The fix was silently reverted; nothing failed. So this guard
treats a MISSING expected path as a FAILURE, never a skip — a skip is precisely
how a rename slips through a second time. If you move one of these files, update
_PROBE_SOURCES in the same commit and re-read the moved content.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
for _p in (
    _REPO,
    _REPO / "core" / "console",
    _REPO / "corvin_operator" / "bridges" / "shared",
    _REPO / "corvin_operator" / "voice" / "scripts",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

#: Every file that decides "is Claude Code usable", by repo-relative path.
#: A path that does not exist fails the test — see the module docstring.
_PROBE_SOURCES = (
    "core/console/corvin_console/chat_runtime.py",
    "corvin_operator/voice/scripts/summarize.py",
    "corvin_operator/bridges/shared/dialectic.py",
    "corvin_operator/bridges/shared/engine_detection.py",
)

#: The three platform flags, in the order Claude Code itself resolves them.
_PLATFORM_FLAGS = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                   "CLAUDE_CODE_USE_FOUNDRY")


@pytest.mark.parametrize("rel", _PROBE_SOURCES)
def test_probe_source_exists_at_its_canonical_path(rel: str) -> None:
    """A probe copy that vanished from its expected path is a FAILURE.

    This is the assertion that catches a rename-without-git-mv: the moved file
    still exists somewhere, so a content-only check on a glob would keep passing
    against the stale copy. Pinning the path forces the mover to look here.
    """
    assert (_REPO / rel).is_file(), (
        f"{rel} is missing. If this file MOVED, update _PROBE_SOURCES in this "
        "test and verify the moved copy still carries the platform-flag check — "
        "a rename that re-adds files instead of `git mv`-ing them silently "
        "reverted exactly this fix once before (c7ea7449 → operator/ rename)."
    )


@pytest.mark.parametrize("rel", _PROBE_SOURCES)
def test_probe_source_checks_every_platform_flag(rel: str) -> None:
    """Each copy must name all three platform flags AND read settings.json.

    Source-level on purpose: it holds for ``engine_detection.py`` (which exposes
    a differently-shaped ``probe_claude_code``) and for any future copy, without
    this guard needing to know each one's call signature.
    """
    src = (_REPO / rel).read_text(encoding="utf-8", errors="replace")
    for flag in _PLATFORM_FLAGS:
        assert flag in src, (
            f"{rel} never mentions {flag} — a {flag.split('_USE_')[1].lower()} "
            "install would be reported as unauthenticated and the feature this "
            "probe gates would silently degrade."
        )
    assert "settings.json" in src, (
        f"{rel} does not read settings.json. The setup wizards write the "
        "CLAUDE_CODE_USE_* flags into its `env` block, NOT into the shell "
        "environment, so an os.environ-only check misses every wizard install."
    )


def _isolate(monkeypatch, tmp_path: Path) -> Path:
    """Neutralise every real credential signal; return the fake config dir.

    Redirects HOME *and* USERPROFILE because ``Path.home()`` reads the latter on
    Windows, so patching only HOME would let the test see the developer's real
    ``~/.claude/.credentials.json`` and pass for the wrong reason.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for flag in _PLATFORM_FLAGS:
        monkeypatch.delenv(flag, raising=False)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    config_dir = tmp_path / "claude-config"
    config_dir.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    return config_dir


def _behavioural_probes():
    """The three ``_claude_authenticated()`` copies, imported for real.

    An ImportError here is a failure, not a skip: every one of these modules is
    in-tree and importable with the repo's own sys.path, so a failure means a
    genuine break (or another silent move).
    """
    from corvin_console import chat_runtime  # type: ignore
    import dialectic  # type: ignore
    import summarize  # type: ignore
    return {
        "chat_runtime": chat_runtime._claude_authenticated,
        "summarize": summarize._claude_authenticated,
        "dialectic": dialectic._claude_authenticated,
    }


@pytest.mark.parametrize("name", sorted(_behavioural_probes()))
def test_probe_is_false_with_no_credential_at_all(name, monkeypatch, tmp_path) -> None:
    """Baseline / positive control: with nothing configured the probe says False.

    Without this, the platform assertions below would pass vacuously against a
    probe that simply returns True unconditionally.
    """
    _isolate(monkeypatch, tmp_path)
    assert _behavioural_probes()[name]() is False


@pytest.mark.parametrize("name", sorted(_behavioural_probes()))
@pytest.mark.parametrize("flag", _PLATFORM_FLAGS)
def test_platform_flag_in_shell_env_counts_as_authenticated(
    name, flag, monkeypatch, tmp_path,
) -> None:
    """A manually exported platform flag authenticates — no key, no OAuth file."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv(flag, "1")
    assert _behavioural_probes()[name]() is True


@pytest.mark.parametrize("name", sorted(_behavioural_probes()))
@pytest.mark.parametrize("flag", _PLATFORM_FLAGS)
def test_platform_flag_in_settings_json_counts_as_authenticated(
    name, flag, monkeypatch, tmp_path,
) -> None:
    """THE case that broke the voice summary on a real install.

    The flag lives ONLY in settings.json's ``env`` block — never exported to the
    shell — which is what every setup wizard produces. ``os.environ`` is empty of
    it here, so a probe that reads only the process environment returns False and
    the feature it gates degrades without a single error message.
    """
    config_dir = _isolate(monkeypatch, tmp_path)
    (config_dir / "settings.json").write_text(
        json.dumps({"env": {flag: "1", "AWS_PROFILE": "ClaudeCode"}}),
        encoding="utf-8",
    )
    assert _behavioural_probes()[name]() is True


@pytest.mark.parametrize("name", sorted(_behavioural_probes()))
def test_corrupt_settings_json_does_not_crash_the_probe(
    name, monkeypatch, tmp_path,
) -> None:
    """A damaged settings file must degrade, not raise — this runs on the
    per-turn path, and an exception here would surface as a failed chat turn
    rather than as a routing decision."""
    config_dir = _isolate(monkeypatch, tmp_path)
    (config_dir / "settings.json").write_text("{not json at all", encoding="utf-8")
    assert _behavioural_probes()[name]() is False
