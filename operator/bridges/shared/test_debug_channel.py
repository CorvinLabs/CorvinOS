#!/usr/bin/env python3
"""test_debug_channel.py — E2E for the /debug self-test channel.

The /debug command + the corresponding `phase3_cli.py debug send`
subcommand let the agent push messages back into the messenger for
self-testing once the chat owner has enabled it via /debug on. This
test verifies the full guard chain — state check, depth guard, rate
limit, audit — against a real filesystem with a real subprocess
invocation.

No mocks. Per-subtask E2E rule from CLAUDE.md.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

CLI = ROOT / "phase3_cli.py"


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _settings_dir(channel: str, parent: Path) -> Path:
    """Create bridges/<channel>/ structure under parent."""
    d = parent / channel
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_settings(channel_dir: Path, profiles: dict) -> Path:
    settings = {"whitelist": ["+49xxx-OWNER"], "chat_profiles": profiles}
    path = channel_dir / "settings.json"
    path.write_text(json.dumps(settings), encoding="utf-8")
    return path


def _run_cli(
    *args: str,
    chat_id: str,
    bridges_root: Path,
    outbox: Path,
    home: Path,
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Spawn the CLI as a real subprocess with the env it would see in
    production (CORVIN_CHANNEL_ID, CORVIN_HOME). The CLI computes
    the bridges/<channel>/settings.json path from its own location, so
    we can't redirect that — instead we run the test CLI from a copy
    sitting under the test bridges_root."""
    env = os.environ.copy()
    env["CORVIN_HOME"] = str(home)
    env["CORVIN_CHANNEL_ID"] = chat_id
    env["ADAPTER_OUTBOX"] = str(outbox)
    if extra_env:
        env.update(extra_env)
    # Run the SHARED-dir CLI (not a copy). This means settings discovery
    # walks UP from .../shared/phase3_cli.py to bridges/<channel>/settings.json
    # in the REAL repo. To isolate per-test, we need to run a CLI that
    # sits under the tempdir's bridges/ tree — copy it.
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(bridges_root.parent),  # so paths.py walk-up finds plugins/
    )


def _make_test_cli_under(bridges_root: Path) -> Path:
    """Copy the production CLI + paths module into a tempdir bridges
    structure so the CLI's relative-path resolution lands in the test
    sandbox instead of the real repo's bridges/."""
    shared = bridges_root / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    for src_name in (
        "phase3_cli.py", "paths.py", "process_table.py",
        "pipe_registry.py", "context_budget.py",
    ):
        src = ROOT / src_name
        if src.exists():
            shutil.copy(src, shared / src_name)
    # Init plugin too (svc subcommand imports init.py)
    plugins = bridges_root.parent
    init_src = plugins / "core" / "init"
    if init_src.exists():
        shutil.copytree(init_src, plugins / "core" / "init",
                        dirs_exist_ok=True)
    return shared / "phase3_cli.py"


def _setup_sandbox() -> tuple[Path, Path, Path, Path]:
    """Create plugins/<channel>/settings.json + outbox + home dirs.
    Returns (sandbox_root, channel_dir, outbox, home).

    Layout matches the real repo: <sandbox>/operator/bridges/<channel>
    so that paths.py's walk-up to a 'plugins/' marker resolves to the
    sandbox, not the real repo.
    """
    sandbox = Path(tempfile.mkdtemp(prefix="debug-test-"))
    plugins = sandbox / "plugins"
    voice = plugins / "voice"
    bridges = voice / "bridges"
    bridges.mkdir(parents=True, exist_ok=True)
    home = sandbox / ".corvinOS"
    home.mkdir(parents=True, exist_ok=True)
    outbox = sandbox / "outbox"
    outbox.mkdir(exist_ok=True)
    return sandbox, bridges, outbox, home


# --------------------------------------------------------------------- cases

def case_status_off_when_no_profile(sandbox: Path, bridges: Path,
                                     outbox: Path, home: Path) -> None:
    _section("status reports off when no chat_profile exists")
    cli = _make_test_cli_under(bridges)
    channel_dir = _settings_dir("discord", bridges)
    _write_settings(channel_dir, {})

    r = subprocess.run(
        [sys.executable, str(cli), "debug", "status"],
        env={**os.environ,
             "CORVIN_HOME": str(home),
             "CORVIN_CHANNEL_ID": "discord:abc123"},
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "disabled" in r.stdout, r.stdout
    print(f"  PASS stdout: {r.stdout.strip()}")


def case_status_on_after_setting_flag(sandbox, bridges, outbox, home) -> None:
    _section("status reports on after chat_profile.debug=true")
    cli = _make_test_cli_under(bridges)
    channel_dir = _settings_dir("discord", bridges)
    _write_settings(channel_dir, {"abc123": {"debug": True}})

    r = subprocess.run(
        [sys.executable, str(cli), "debug", "status"],
        env={**os.environ,
             "CORVIN_HOME": str(home),
             "CORVIN_CHANNEL_ID": "discord:abc123"},
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "enabled" in r.stdout, r.stdout
    print(f"  PASS stdout: {r.stdout.strip()}")


def case_send_writes_outbox_when_enabled(sandbox, bridges, outbox, home) -> None:
    _section("send writes outbox envelope when debug is enabled")
    cli = _make_test_cli_under(bridges)
    channel_dir = _settings_dir("discord", bridges)
    _write_settings(channel_dir, {"abc123": {"debug": True}})

    r = subprocess.run(
        [sys.executable, str(cli), "debug", "send", "hello", "world"],
        env={**os.environ,
             "CORVIN_HOME": str(home),
             "CORVIN_CHANNEL_ID": "discord:abc123",
             "ADAPTER_OUTBOX": str(outbox)},
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"stderr={r.stderr} stdout={r.stdout}"

    files = list(outbox.glob("debug_*.json"))
    assert len(files) == 1, files
    payload = json.loads(files[0].read_text())
    assert payload["chat_id"] == "abc123", payload
    assert payload["text"] == "hello world", payload
    assert payload["channel"] == "discord", payload
    assert payload["_debug"] is True
    print(f"  PASS outbox file written: {files[0].name}")


def case_send_denied_when_disabled(sandbox, bridges, outbox, home) -> None:
    _section("send refused when chat_profile.debug is missing/false")
    cli = _make_test_cli_under(bridges)
    channel_dir = _settings_dir("discord", bridges)
    _write_settings(channel_dir, {})  # no profile -> not enabled

    r = subprocess.run(
        [sys.executable, str(cli), "debug", "send", "should-fail"],
        env={**os.environ,
             "CORVIN_HOME": str(home),
             "CORVIN_CHANNEL_ID": "discord:abc123",
             "ADAPTER_OUTBOX": str(outbox)},
        capture_output=True, text=True,
    )
    assert r.returncode == 1, f"expected exit 1, got {r.returncode}"
    assert "debug not enabled" in r.stderr, r.stderr
    assert not list(outbox.glob("debug_*.json")), "no outbox should be written"
    print(f"  PASS denied: {r.stderr.strip()}")


def case_send_refuses_at_depth_3(sandbox, bridges, outbox, home) -> None:
    _section("send refused when CORVIN_DEBUG_DEPTH >= 3 (loop guard)")
    cli = _make_test_cli_under(bridges)
    channel_dir = _settings_dir("discord", bridges)
    _write_settings(channel_dir, {"abc123": {"debug": True}})

    r = subprocess.run(
        [sys.executable, str(cli), "debug", "send", "deep"],
        env={**os.environ,
             "CORVIN_HOME": str(home),
             "CORVIN_CHANNEL_ID": "discord:abc123",
             "ADAPTER_OUTBOX": str(outbox),
             "CORVIN_DEBUG_DEPTH": "3"},
        capture_output=True, text=True,
    )
    assert r.returncode == 1, r.stdout
    assert "depth 3" in r.stderr, r.stderr
    print(f"  PASS denied at depth=3: {r.stderr.strip()}")


def case_rate_limit_blocks_at_11(sandbox, bridges, outbox, home) -> None:
    _section("rate limit denies the 11th send within 60 s window")
    cli = _make_test_cli_under(bridges)
    channel_dir = _settings_dir("discord", bridges)
    _write_settings(channel_dir, {"abc123": {"debug": True}})

    env = {**os.environ,
           "CORVIN_HOME": str(home),
           "CORVIN_CHANNEL_ID": "discord:abc123",
           "ADAPTER_OUTBOX": str(outbox)}

    successes = 0
    rate_limited = 0
    for i in range(12):
        r = subprocess.run(
            [sys.executable, str(cli), "debug", "send", f"msg-{i}"],
            env=env, capture_output=True, text=True,
        )
        if r.returncode == 0:
            successes += 1
        elif "rate-limit" in r.stderr:
            rate_limited += 1
    assert successes == 10, f"expected 10 successes, got {successes}"
    assert rate_limited == 2, f"expected 2 rate-limited, got {rate_limited}"
    files = list(outbox.glob("debug_*.json"))
    assert len(files) == 10, f"expected 10 outbox files, got {len(files)}"
    print(f"  PASS first 10 sent ({successes}), 11th+12th denied "
          f"({rate_limited}), outbox count = {len(files)}")


def case_missing_channel_id_errors(sandbox, bridges, outbox, home) -> None:
    _section("missing CORVIN_CHANNEL_ID is a clear error")
    cli = _make_test_cli_under(bridges)
    env = {**os.environ, "CORVIN_HOME": str(home)}
    env.pop("CORVIN_CHANNEL_ID", None)
    r = subprocess.run(
        [sys.executable, str(cli), "debug", "status"],
        env=env, capture_output=True, text=True,
    )
    assert r.returncode == 1, r.stdout
    assert "CORVIN_CHANNEL_ID" in r.stderr, r.stderr
    print(f"  PASS clear error: {r.stderr.strip()}")


def case_help_text_includes_debug(sandbox, bridges, outbox, home) -> None:
    _section("help text mentions the debug subcommand")
    cli = _make_test_cli_under(bridges)
    r = subprocess.run(
        [sys.executable, str(cli), "help"],
        env={**os.environ, "CORVIN_HOME": str(home)},
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "debug status" in r.stdout, r.stdout
    assert "debug send" in r.stdout, r.stdout
    print("  PASS help text lists debug subcommand")


# --------------------------------------------------------------------- driver

def main() -> None:
    cases = [
        case_status_off_when_no_profile,
        case_status_on_after_setting_flag,
        case_send_writes_outbox_when_enabled,
        case_send_denied_when_disabled,
        case_send_refuses_at_depth_3,
        case_rate_limit_blocks_at_11,
        case_missing_channel_id_errors,
        case_help_text_includes_debug,
    ]
    failures = 0
    for case in cases:
        sandbox, bridges, outbox, home = _setup_sandbox()
        try:
            case(sandbox, bridges, outbox, home)
        except Exception as exc:
            failures += 1
            print(f"  FAIL: {case.__name__}: {exc!r}")
            import traceback
            traceback.print_exc()
        finally:
            shutil.rmtree(sandbox, ignore_errors=True)

    print(f"\n=== {len(cases) - failures}/{len(cases)} cases passed ===")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
