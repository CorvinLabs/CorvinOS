"""E2E tests for /corvin:update (scripts/corvin_update_command.py).

Every test runs the real script as a subprocess — the same way Claude Code runs
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/corvin_update_command.py" $ARGUMENTS` —
against a real git checkout whose origin is a local bare repository, a stand-in
`update.sh` inside that checkout (the real one would reinstall this host), and
a real HTTP server standing in for the console. Nothing touches the live
install or the network.
"""
from __future__ import annotations

import http.server
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _PLUGIN_ROOT / "scripts" / "corvin_update_command.py"
_COMMAND_MD = _PLUGIN_ROOT / "commands" / "update.md"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not Path("/bin/sh").exists(), reason="needs git and /bin/sh"
)

_FAKE_UPDATE_SH = """#!/bin/sh
# Stand-in for update.sh: pull like the real one, record that it ran,
# exit with the code the test asks for.
echo ran >> "$(dirname "$0")/.update-ran"
rc="${FAKE_UPDATE_RC:-0}"
[ "$rc" = 0 ] && git -C "$(dirname "$0")" pull --quiet --ff-only origin main
exit "$rc"
"""


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "init.defaultBranch=main", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _commit_version(repo: Path, version: str) -> None:
    (repo / "pyproject.toml").write_text(f'[project]\nname = "corvinos"\nversion = "{version}"\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", f"v{version}")


@pytest.fixture()
def repos(tmp_path):
    """origin (bare) + a seed clone that publishes to it + the user's checkout."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(origin))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", "-q", str(origin), str(seed))
    for name in (".corvin_repo", "install.sh"):
        (seed / name).write_text("")
    (seed / "update.sh").write_text(_FAKE_UPDATE_SH)
    (seed / ".gitignore").write_text(".update-ran\n")
    _commit_version(seed, "1.0.0")
    _git(seed, "push", "-q", "origin", "HEAD:main")
    checkout = tmp_path / "CorvinOS"
    _git(tmp_path, "clone", "-q", "--branch", "main", str(origin), str(checkout))
    return seed, checkout


@pytest.fixture()
def console():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<!doctype html><div id=root></div>")

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()


@pytest.fixture()
def broken_console():
    """The console's permanent fallback after booting without dist/."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(503)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>CorvinOS Console - build failed</h2>")

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()


def _run(checkout: Path, *args: str, port: int | None = None, rc: int = 0) -> subprocess.CompletedProcess:
    env = dict(os.environ, FAKE_UPDATE_RC=str(rc),
               GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    if port:
        env["CORVIN_CONSOLE_PORT"] = str(port)
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--checkout", str(checkout), *args],
        capture_output=True, text=True, timeout=120, env=env, stdin=subprocess.DEVNULL,
    )


def test_check_reports_up_to_date_and_changes_nothing(repos):
    _, checkout = repos
    out = _run(checkout, "--check")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "up to date" in out.stdout
    assert not (checkout / ".update-ran").exists()


def test_check_reports_a_newer_version_without_pulling_it(repos):
    seed, checkout = repos
    before = _git(checkout, "rev-parse", "HEAD")
    _commit_version(seed, "1.1.0")
    _git(seed, "push", "-q", "origin", "HEAD:main")

    out = _run(checkout, "--check")

    assert out.returncode == 4, out.stdout + out.stderr
    assert "1 commit(s) behind" in out.stdout
    assert "(1.0.0)" in out.stdout and "(1.1.0)" in out.stdout
    assert _git(checkout, "rev-parse", "HEAD") == before
    assert not (checkout / ".update-ran").exists()


def test_update_runs_the_checkouts_updater_then_proves_the_console(repos, console):
    seed, checkout = repos
    _commit_version(seed, "1.1.0")
    _git(seed, "push", "-q", "origin", "HEAD:main")

    out = _run(checkout, port=console)

    assert out.returncode == 0, out.stdout + out.stderr
    assert (checkout / ".update-ran").exists()
    assert re.search(r"updated \w+ → \w+ \(1\.0\.0 → 1\.1\.0\); console is live", out.stdout)
    assert _git(checkout, "rev-parse", "HEAD") == _git(seed, "rev-parse", "HEAD")
    assert "Configuration was kept" in out.stdout
    assert "/plugin marketplace update" in out.stdout


def test_already_current_does_not_run_the_updater(repos, console):
    _, checkout = repos
    out = _run(checkout, port=console)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "nothing to update" in out.stdout
    assert not (checkout / ".update-ran").exists()


def test_force_runs_the_updater_even_when_current(repos, console):
    _, checkout = repos
    out = _run(checkout, "--force", port=console)
    assert out.returncode == 0, out.stdout + out.stderr
    assert (checkout / ".update-ran").exists()


@pytest.mark.parametrize("rc,needle", [
    (1, "rolled back"),
    (2, "rollback failed too"),
    (3, "another CorvinOS install/update is running"),
])
def test_updater_failures_pass_through_and_never_claim_success(repos, console, rc, needle):
    seed, checkout = repos
    _commit_version(seed, "1.1.0")
    _git(seed, "push", "-q", "origin", "HEAD:main")

    out = _run(checkout, port=console, rc=rc)

    assert out.returncode == rc, out.stdout + out.stderr
    assert needle in out.stdout
    assert "updated" not in out.stdout.replace("not updated", "")


def test_updater_success_with_a_broken_console_is_not_success(repos, broken_console):
    seed, checkout = repos
    _commit_version(seed, "1.1.0")
    _git(seed, "push", "-q", "origin", "HEAD:main")

    out = _run(checkout, port=broken_console)

    assert out.returncode == 2, out.stdout + out.stderr
    assert "console is not healthy" in out.stdout
    assert "systemctl --user restart corvin-webui" in out.stdout
    assert "console is live" not in out.stdout


def test_no_checkout_stops_with_the_clone_steps(tmp_path):
    out = _run(tmp_path, "--check")
    assert out.returncode == 1
    assert "git clone https://github.com/CorvinLabs/CorvinOS.git" in out.stdout


def test_unreachable_origin_is_a_reported_failure(repos):
    _, checkout = repos
    _git(checkout, "remote", "set-url", "origin", str(checkout.parent / "gone.git"))
    out = _run(checkout, "--check")
    assert out.returncode == 1
    assert "version check failed" in out.stdout


def test_command_file_runs_this_script_with_its_arguments():
    text = _COMMAND_MD.read_text(encoding="utf-8")
    m = re.search(r'\$\{CLAUDE_PLUGIN_ROOT\}/(scripts/[\w./-]+\.py)"? \$ARGUMENTS', text)
    assert m, "update.md must run a scripts/*.py with $ARGUMENTS"
    assert (_PLUGIN_ROOT / m.group(1)).resolve() == _SCRIPT.resolve()
