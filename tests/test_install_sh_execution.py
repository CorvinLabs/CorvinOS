"""Execution tests for install.sh — the real one-liner installer entry point.

Confirmed test blind spot (repo-wide grep across every test that mentions
`install.sh` / `install.ps1`): every existing hit is a *text-level* assertion —
`_INSTALL_SH.read_text()` fed through a regex (URL pinning in
test_installer_health_probe.py) or a heredoc-body diff (supervisor parity in
test_windows_supervisor_parity.py). None of them ever invoke `sh install.sh`
as a real subprocess, so the script's actual control flow — CLI arg parsing
with die-on-unknown-arg, the editable-path validation, the curl/wget
bootstrap-or-die branch, the "corvinos-serve must land on PATH" die, and the
happy-path readiness/cheat-sheet render — has never been exercised by any
test in this repo.

This file closes that gap by running the real script (POSIX `sh`, matching
its own shebang) as a subprocess:

  * fast, no-mocking tests that hit `die()` paths before any network call is
    attempted (arg parsing, editable-path validation, missing curl+wget) —
    these need nothing but an empty PATH, since everything before them is a
    shell builtin;
  * a stubbed-PATH "happy path" test that fakes `uv` and `curl` (and,
    separately, omits a `corvinos-serve` stub) to drive the script all the
    way through its real install → readiness-poll → cheat-sheet flow, or into
    its real "corvinos-serve not on PATH" die(), without touching the
    network or spawning a real server.

install.ps1 needs a Windows/pwsh runner (this sandbox is Linux-only) and is
intentionally left to a follow-up; see the review notes that produced this
file for that scoping call.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_INSTALL_SH = _REPO / "install.sh"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not Path("/bin/sh").exists(),
    reason="install.sh is a POSIX sh script; needs a real /bin/sh interpreter",
)


def _run(args: list[str], *, env: dict[str, str] | None = None, timeout: int = 20) -> subprocess.CompletedProcess:
    """Execute the real install.sh as a subprocess, never touching the real
    network/PATH unless the caller explicitly builds that into `env`."""
    return subprocess.run(
        ["/bin/sh", str(_INSTALL_SH), *args],
        cwd=_REPO,
        env=env if env is not None else {},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )


def _write_stub(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


# ── syntax ────────────────────────────────────────────────────────────────


def test_install_sh_passes_posix_shell_syntax_check() -> None:
    """`sh -n` parses the script without executing it — the cheapest possible
    real-interpreter check, and one that catches quoting/heredoc breakage
    that no text-regex test can (e.g. an unbalanced quote in a printf that a
    grep for a URL literal would never notice)."""
    result = subprocess.run(
        ["/bin/sh", "-n", str(_INSTALL_SH)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


# ── argument parsing (die() before any network call) ───────────────────────


def test_unknown_argument_dies_with_usage_message() -> None:
    result = _run(["--this-flag-does-not-exist"])
    assert result.returncode == 1
    assert "Unknown argument: --this-flag-does-not-exist" in result.stderr
    assert "Usage:" in result.stderr


def test_editable_flag_without_value_dies() -> None:
    # `--editable` is the last token with no path following it — must hit
    # the `[ $# -lt 2 ] && die ...` guard, not a shell arity crash.
    result = _run(["--editable"])
    assert result.returncode == 1
    assert "--editable requires a path argument" in result.stderr


def test_editable_short_flag_without_value_dies() -> None:
    result = _run(["-e"])
    assert result.returncode == 1
    assert "--editable requires a path argument" in result.stderr


def test_editable_path_that_does_not_exist_dies() -> None:
    result = _run(["--editable", "/definitely/not/a/real/path/corvin-xyz"])
    assert result.returncode == 1
    assert "Editable path does not exist" in result.stderr


# ── network bootstrap: die() when neither curl nor wget is available ───────


def test_missing_curl_and_wget_dies_with_clear_message() -> None:
    # Empty PATH: every step before the curl/wget check (arg parsing, the
    # banner, `command -v uv`) is a shell builtin, so this genuinely
    # exercises the "no uv, no curl, no wget" real failure path instead of
    # crashing on a missing utility earlier in the script.
    result = _run([], env={"PATH": ""})
    assert result.returncode == 1
    assert "Need curl or wget to bootstrap uv" in result.stderr


# ── stubbed-PATH integration: drive the real happy path end-to-end ─────────


@pytest.fixture()
def _stubbed_env(tmp_path: Path):
    """Build a fake PATH + HOME that make install.sh's real control flow run
    to completion without any real network access or process spawn.

    Key realism point this test caught: install.sh does
        export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    unconditionally right after the initial `command -v uv` check, which
    means a stub `uv` placed only earlier in PATH gets silently shadowed by
    a REAL uv already installed at the real $HOME/.local/bin on the box
    running the test. The fixture must therefore also point HOME at an
    isolated directory with the stub uv linked into .local/bin, or the test
    exercises the real `uv tool install` against a fake --editable target
    and fails for an unrelated reason.
    """
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    fakehome = tmp_path / "home"
    (fakehome / ".local" / "bin").mkdir(parents=True)
    (fakehome / ".cargo" / "bin").mkdir(parents=True)

    _write_stub(
        fakebin / "uv",
        """
case "$1" in
  --version) echo "uv 9.9.9 (stub)"; exit 0 ;;
  tool) exit 0 ;;
  *) exit 0 ;;
esac
""",
    )
    # Fake curl: report the console healthz probe as instantly ready, fail
    # every other URL (PyPI version lookup, Ollama, etc.) so the script is
    # forced down its real fail-soft / already-satisfied branches instead of
    # touching the network.
    _write_stub(
        fakebin / "curl",
        """
for a in "$@"; do
  case "$a" in
    *healthz*) exit 0 ;;
  esac
done
exit 1
""",
    )
    os.symlink(fakebin / "uv", fakehome / ".local" / "bin" / "uv")

    editable_dir = tmp_path / "editable-target"
    editable_dir.mkdir()

    env = {
        "PATH": f"{fakebin}:/usr/bin:/bin",
        "HOME": str(fakehome),
    }
    return fakebin, editable_dir, env


def test_happy_path_reaches_ready_banner_with_no_hermes(_stubbed_env) -> None:
    fakebin, editable_dir, env = _stubbed_env
    _write_stub(fakebin / "corvinos-serve", "exit 0")

    result = _run(["--editable", str(editable_dir), "--no-hermes"], env=env)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "CorvinOS is ready!" in result.stdout
    assert "Server is ready!" in result.stdout
    assert f"Installing CorvinOS (editable) from {editable_dir}" in result.stdout


def test_missing_corvinos_serve_on_path_dies_with_clear_message(_stubbed_env) -> None:
    # Deliberately do NOT stub corvinos-serve: the real
    # `command -v corvinos-serve || die ...` guard (line ~113 of install.sh)
    # must fire instead of the script silently limping on to the readiness
    # loop with nothing actually installed.
    fakebin, editable_dir, env = _stubbed_env

    result = _run(["--editable", str(editable_dir), "--no-hermes"], env=env)

    assert result.returncode == 1
    assert "corvinos-serve" in result.stderr
    assert "is not on PATH" in result.stderr
