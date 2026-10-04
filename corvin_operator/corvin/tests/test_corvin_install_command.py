"""Tests for the /corvin:install backend (corvin_operator/corvin/scripts/
corvin_install_command.py).

Per memory feedback-dead-mechanism-needs-call-site-test: a unit test proves a
function returns the right value when called, not that anything calls it, and
not that the retry/idempotency logic is real rather than vacuous. This file
therefore covers three things explicitly:

1. The retry PRIMITIVE actually retries (mocked clock/probe — no real sleeps,
   no real network) and actually distinguishes "connection refused" from the
   permanent "build failed" fallback (which must stop early, not burn the
   whole budget).
2. The idempotent path does NOT re-run the installer when onboarding is
   already complete and the console is healthy (asserts run_installer's
   subprocess hook is never called) — and the failure path never opens a
   browser (asserts webbrowser.open is never called) when the installer
   itself failed.
3. The command file that Claude Code actually invokes
   (commands/install.md) names a script path that exists AND parses as
   valid Python — the mechanical check that would have caught the exact
   "command references a script that was never built" shape of dead
   mechanism.
"""
from __future__ import annotations

import ast
import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _PLUGIN_ROOT / "scripts" / "corvin_install_command.py"
_COMMAND_MD = _PLUGIN_ROOT / "commands" / "install.md"

_MODULE_NAME = "corvin_corvin_install_command_under_test"
_spec = importlib.util.spec_from_file_location(_MODULE_NAME, _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
cic = importlib.util.module_from_spec(_spec)
sys.modules[_MODULE_NAME] = cic
_spec.loader.exec_module(cic)


# ── 1. retry/backoff primitive is real, not vacuous ─────────────────────────

def test_wait_for_real_console_retries_until_healthy(monkeypatch):
    """Connection-refused for the first N probes, healthy on the (N+1)th —
    proves the loop genuinely retries rather than returning on the first
    failure or always returning True."""
    calls = {"n": 0}

    def fake_probe(url):
        calls["n"] += 1
        if calls["n"] < 4:
            return cic.ConsoleState.UNREACHABLE, None, "connection refused"
        return cic.ConsoleState.HEALTHY, 200, ""

    monkeypatch.setattr(cic, "probe_console", fake_probe)
    sleeps = []
    ok, detail = cic.wait_for_real_console(
        total_budget_s=60.0, _sleep=sleeps.append, _clock=_fake_clock(sleeps)
    )
    assert ok is True
    assert "4 attempt" in detail
    assert calls["n"] == 4
    assert len(sleeps) == 3  # slept between attempts 1-2, 2-3, 3-4, never after success


def test_wait_for_real_console_never_retries_build_failed(monkeypatch):
    """The permanent 503 'build failed' fallback must stop the loop on the
    FIRST probe — waiting it out cannot help, since mount_static() decides
    the route once at boot. A version of this function that just kept
    sleeping on every non-200 would burn the whole budget here."""
    calls = {"n": 0}

    def fake_probe(url):
        calls["n"] += 1
        return cic.ConsoleState.BUILD_FAILED, 503, "build failed"

    monkeypatch.setattr(cic, "probe_console", fake_probe)
    sleeps = []
    ok, detail = cic.wait_for_real_console(
        total_budget_s=300.0, _sleep=sleeps.append, _clock=_fake_clock(sleeps)
    )
    assert ok is False
    assert "restart" in detail.lower()
    assert "corvin-webui" in detail
    assert calls["n"] == 1  # stopped immediately, did not retry 503s hoping it clears
    assert sleeps == []


def test_wait_for_real_console_gives_up_after_budget(monkeypatch):
    """Permanently unreachable (nothing ever listens): the loop must
    terminate once the budget is exhausted, not hang forever."""
    monkeypatch.setattr(
        cic, "probe_console",
        lambda url: (cic.ConsoleState.UNREACHABLE, None, "connection refused"),
    )
    sleeps = []
    ok, detail = cic.wait_for_real_console(
        total_budget_s=10.0, _sleep=sleeps.append, _clock=_fake_clock(sleeps)
    )
    assert ok is False
    assert "connection refused" in detail
    assert len(sleeps) > 0  # it did actually wait/retry before giving up


def _fake_clock(sleeps: list):
    """A monotonic() stand-in that advances by exactly what was `slept`,
    so the retry loop's real backoff math drives a deterministic, instant
    test instead of a real wall-clock wait."""
    state = {"t": 0.0}

    def clock():
        state["t"] = sum(sleeps)
        return state["t"]

    return clock


# ── 2. idempotency: already-onboarded + healthy skips reinstall ────────────

def test_already_onboarded_and_healthy_skips_installer(monkeypatch, tmp_path):
    flag = tmp_path / ".corvin_setup_complete"
    flag.touch()
    monkeypatch.setattr(cic, "onboarding_flag_path", lambda: flag)
    monkeypatch.setattr(cic, "wait_for_real_console", lambda **kw: (True, "responded 200 after 1 attempt(s)"))

    run_installer_mock = MagicMock()
    monkeypatch.setattr(cic, "run_installer", run_installer_mock)
    open_browser_mock = MagicMock(return_value=True)
    monkeypatch.setattr(cic, "open_console_browser", open_browser_mock)

    rc = cic.main(subprocess_run=MagicMock())

    assert rc == 0
    run_installer_mock.assert_not_called()
    open_browser_mock.assert_called_once()


def test_already_onboarded_but_unhealthy_falls_through_to_restore_then_install(monkeypatch, tmp_path):
    """Flag says done, console is dead: must try the cheap `restore` path
    before a full reinstall, and only run the full installer if restore
    doesn't recover it."""
    flag = tmp_path / ".corvin_setup_complete"
    flag.touch()
    monkeypatch.setattr(cic, "onboarding_flag_path", lambda: flag)
    monkeypatch.setattr(cic, "find_corvin_restore", lambda: "/usr/bin/corvin-restore")

    health_results = iter([
        (False, "no healthy response within 30s (last: connection refused)"),  # initial check
        (False, "no healthy response within 300s (last: connection refused)"),  # after restore attempt
        (True, "responded 200 after 1 attempt(s)"),  # after full install
    ])
    monkeypatch.setattr(cic, "wait_for_real_console", lambda **kw: next(health_results))
    run_installer_mock = MagicMock(return_value=(True, "corvin-install completed"))
    monkeypatch.setattr(cic, "run_installer", run_installer_mock)
    monkeypatch.setattr(cic, "open_console_browser", lambda: True)

    subprocess_run_mock = MagicMock()
    rc = cic.main(subprocess_run=subprocess_run_mock)

    assert rc == 0
    # restore was attempted
    restore_calls = [c for c in subprocess_run_mock.call_args_list if c.args[0] == ["/usr/bin/corvin-restore"]]
    assert len(restore_calls) == 1
    # full install ran because restore didn't recover it
    run_installer_mock.assert_called_once()


# ── 3. failure path never lies about success ────────────────────────────────

def test_install_failure_never_opens_browser_and_returns_nonzero(monkeypatch, tmp_path):
    flag = tmp_path / ".corvin_setup_complete"  # does not exist -> fresh install path
    monkeypatch.setattr(cic, "onboarding_flag_path", lambda: flag)
    monkeypatch.setattr(cic, "run_installer", lambda **kw: (False, "the installer exited 1 — see its output above"))
    open_browser_mock = MagicMock()
    monkeypatch.setattr(cic, "open_console_browser", open_browser_mock)
    wait_mock = MagicMock()
    monkeypatch.setattr(cic, "wait_for_real_console", wait_mock)

    rc = cic.main(subprocess_run=MagicMock())

    assert rc == 1
    open_browser_mock.assert_not_called()
    wait_mock.assert_not_called()  # never even got to the health check


def test_console_never_healthy_after_successful_install_is_still_a_failure(monkeypatch, tmp_path):
    flag = tmp_path / ".corvin_setup_complete"
    monkeypatch.setattr(cic, "onboarding_flag_path", lambda: flag)
    monkeypatch.setattr(cic, "run_installer", lambda **kw: (True, "corvin-install completed"))
    monkeypatch.setattr(cic, "wait_for_real_console", lambda **kw: (False, "no healthy response within 300s (last: connection refused)"))
    open_browser_mock = MagicMock()
    monkeypatch.setattr(cic, "open_console_browser", open_browser_mock)

    rc = cic.main(subprocess_run=MagicMock())

    assert rc == 2
    open_browser_mock.assert_not_called()


# ── repo-only: the checkout's own installer, never a download ───────────────

def _fake_checkout(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name in (".corvin_repo", "pyproject.toml", "install.sh"):
        (root / name).write_text("")
    return root


def test_find_checkout_walks_up_from_a_nested_start(tmp_path):
    checkout = _fake_checkout(tmp_path / "CorvinOS")
    nested = checkout / "corvin_operator" / "corvin" / "scripts"
    nested.mkdir(parents=True)
    assert cic.find_checkout([nested]) == checkout


def test_find_checkout_requires_the_repo_marker(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "install.sh").write_text("")
    assert cic.find_checkout([tmp_path]) is None


def test_no_checkout_fails_with_clone_steps_and_runs_nothing(monkeypatch):
    monkeypatch.setattr(cic, "find_checkout", lambda *a, **k: None)
    runner = MagicMock()
    ok, detail = cic.run_installer(subprocess_run=runner)
    assert ok is False
    assert "git clone https://github.com/CorvinLabs/CorvinOS.git" in detail
    assert "corvin-labs" not in detail
    runner.assert_not_called()


def test_run_installer_runs_the_checkouts_own_installer_in_the_checkout(tmp_path, monkeypatch):
    checkout = _fake_checkout(tmp_path / "CorvinOS")
    monkeypatch.setattr(cic.sys, "platform", "linux")
    runner = MagicMock(return_value=MagicMock(returncode=0))
    ok, _ = cic.run_installer(subprocess_run=runner, checkout=checkout)
    assert ok is True
    (cmd,), kwargs = runner.call_args
    assert cmd == ["sh", str(checkout / "install.sh")]
    assert kwargs["cwd"] == str(checkout)
    assert "--yes" not in cmd  # install.sh has no --yes flag; passing one dies


def test_installer_command_on_windows_uses_install_ps1(tmp_path, monkeypatch):
    monkeypatch.setattr(cic.sys, "platform", "win32")
    cmd = cic.installer_command(tmp_path)
    assert cmd[0] == "powershell" and cmd[-1] == str(tmp_path / "install.ps1")


def test_installer_flags_are_ones_install_sh_accepts():
    """Positive control for the --yes regression: whatever run_installer
    passes must survive install.sh's own argument parser."""
    repo = Path(__file__).resolve().parents[3]
    cmd = cic.installer_command(repo)
    extra = cmd[2:]
    result = subprocess.run(
        ["/bin/sh", str(repo / "install.sh"), *extra, "--definitely-unknown"],
        capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL,
        env={"PATH": "", "TMPDIR": str(Path(__file__).parent)},
    )
    # Only OUR sentinel may be rejected — never a flag run_installer adds.
    assert "Unknown argument: --definitely-unknown" in result.stderr


# ── 4. real call-site / wiring proof ────────────────────────────────────────

def test_command_markdown_references_the_real_script_path():
    """The exact dead-mechanism shape this guards against: a command file
    that LOOKS wired (references ${CLAUDE_PLUGIN_ROOT}/scripts/...) but the
    script was renamed/never committed. Assert the referenced relative path
    resolves to a real file, not just that some .py path string exists."""
    assert _COMMAND_MD.exists(), "commands/install.md itself is missing"
    text = _COMMAND_MD.read_text(encoding="utf-8")
    match = re.search(r'\$\{CLAUDE_PLUGIN_ROOT\}/(scripts/[A-Za-z0-9_./-]+\.py)', text)
    assert match, "install.md does not reference a scripts/*.py path at all"
    referenced = _PLUGIN_ROOT / match.group(1)
    assert referenced.resolve() == _SCRIPT_PATH.resolve(), (
        f"install.md points at {referenced}, which does not match the actual "
        f"script {_SCRIPT_PATH}"
    )


def test_script_is_valid_python_and_has_a_main_entrypoint():
    source = _SCRIPT_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_SCRIPT_PATH))
    top_level_defs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert "main" in top_level_defs
    assert re.search(r'if __name__ == ["\']__main__["\']:\s*\n\s*sys\.exit\(main\(\)\)', source), (
        "script has no __main__ guard calling sys.exit(main()) — it would "
        "import cleanly but never actually run when invoked as a subprocess"
    )


def test_plugin_registered_in_marketplace():
    marketplace = _PLUGIN_ROOT.parents[1] / ".claude-plugin" / "marketplace.json"
    assert marketplace.exists()
    import json
    data = json.loads(marketplace.read_text(encoding="utf-8"))
    names = {p["name"] for p in data["plugins"]}
    assert "corvin" in names, (
        "the 'corvin' plugin exists on disk but was never added to "
        ".claude-plugin/marketplace.json — Claude Code would never offer it"
    )
    entry = next(p for p in data["plugins"] if p["name"] == "corvin")
    assert (_PLUGIN_ROOT.parents[1] / entry["source"].lstrip("./")).resolve() == _PLUGIN_ROOT.resolve()


# ── 5. real E2E: actual subprocess, actual HTTP probe, real transport boundary ──

def test_check_mode_real_subprocess_against_the_real_console():
    """Not a call to cic.main() in-process — a genuine subprocess invocation
    of the script, exactly like Claude Code's Bash tool invokes
    `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/corvin_install_command.py"`. Read-
    only: --check never touches the installer or a browser, only a GET
    against 127.0.0.1:8765/console/ — safe to run against whatever this host
    actually has listening right now, mutates nothing."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), "--check"],
        capture_output=True, text=True, timeout=30,
    )
    # This host's own console may or may not be up at test time — either
    # outcome is a valid proof of real wiring; a hang, a traceback, or any
    # OTHER exit code would not be.
    assert result.returncode in (0, 3), (
        f"unexpected exit {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    if result.returncode == 0:
        assert "console is live" in result.stdout
    else:
        assert "not yet healthy" in result.stdout


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
