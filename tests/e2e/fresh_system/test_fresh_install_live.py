"""Fresh-system E2E — the install and the first boot (cluster "installation").

Runs against a container that `fresh.sh install NAME` has just set up (and
`fresh.sh login NAME` for the model call). Everything goes through the
installed product on the fresh system — its journal, its units, its audit
chain, its HTTP surface via scripts/verify_install.py, and the Claude Code
CLI the installer provisioned — never through Python functions on the host.

    tests/e2e/fresh_system/fresh.sh up corvin-x && fresh.sh install corvin-x && fresh.sh login corvin-x
    CORVIN_FRESH_E2E=1 CORVIN_FRESH_CONTAINER=corvin-x pytest tests/e2e/fresh_system/test_fresh_install_live.py -q

What it pins (fresh-system review 2026-10-21):
  F10  ONE runtime root, ONE audit chain — no ~/.corvin, no sibling chain, no
       `audit_chain_split` from the boot tripwire, CORVIN_HOME on every unit,
       no false `tenant.home_exists` from the adapter's self-test.
  F3   step 17 reports the console up (with its seconds), never "did not …".
  F7   no "Index not found at /home/<user>/corvin_operator/…" on boot.
  README  the install proof (app shell + local login) passes on the system.
  engine  the Claude Code the installer put there answers a real prompt.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("CORVIN_FRESH_E2E") != "1",
    reason="fresh-system E2E: set CORVIN_FRESH_E2E=1 and CORVIN_FRESH_CONTAINER=<name>",
)

_FRESH = Path(__file__).resolve().parent / "fresh.sh"
_CONTAINER = os.environ.get("CORVIN_FRESH_CONTAINER", "corvin-a")
_INSTALL_LOG = os.environ.get("CORVIN_FRESH_INSTALL_LOG", "")  # host path, optional
_REPO_IN = "/home/tester/CorvinOS"


def _ex(cmd: str, timeout: float = 300) -> subprocess.CompletedProcess:
    return subprocess.run([str(_FRESH), "ex", _CONTAINER, cmd], capture_output=True,
                          text=True, timeout=timeout, check=False)


def _out(cmd: str, timeout: float = 300) -> str:
    r = _ex(cmd, timeout)
    assert r.returncode == 0, f"{cmd!r} failed ({r.returncode}): {r.stderr[-800:]}{r.stdout[-800:]}"
    return r.stdout


# ── F10: one root, one chain ────────────────────────────────────────────────

def test_only_the_checkout_root_exists_and_holds_the_one_chain():
    chains = _out(f"find ~/.corvin {_REPO_IN}/.corvin -name audit.jsonl -printf '%p\\n' 2>/dev/null; true").split()
    assert chains == [f"{_REPO_IN}/.corvin/tenants/_default/global/forge/audit.jsonl"], chains
    home_root = _ex("ls -A ~/.corvin 2>/dev/null").stdout.split()
    assert home_root == [], f"~/.corvin must not be created by a fresh install, found {home_root}"
    assert _out(f"test -x {_REPO_IN}/.corvin/node/bin/node && echo ok").strip() == "ok"


def _current_boot_journal(unit: str) -> str:
    """The journal of the unit's CURRENT invocation only — a system that was
    installed, uninstalled and installed again keeps the earlier boots."""
    inv = _out(f"systemctl --user show {unit}.service -p InvocationID --value").strip()
    assert inv, f"{unit} has no invocation id (not running?)"
    return _out(f"journalctl --user -u {unit}.service _SYSTEMD_INVOCATION_ID={inv} --no-pager -o cat")


def test_the_boot_tripwire_saw_no_chain_split_and_every_unit_carries_corvin_home():
    journal = _current_boot_journal("corvin-webui") + _current_boot_journal("corvin-voice-bridge-adapter")
    assert "Application startup complete" in journal  # positive control: we read the real boot
    assert "audit_chain_split" not in journal
    assert "tenant.home_exists" not in journal
    assert "Index not found at" not in journal
    for unit in ("corvin-webui", "corvin-voice-bridge-adapter"):
        env = _out(f"systemctl --user show {unit}.service -p Environment --value")
        assert f"CORVIN_HOME={_REPO_IN}/.corvin" in env, (unit, env)


def test_the_adapter_runs_the_tenant_migration_as_a_noop():
    marker = _out(f"cat {_REPO_IN}/.corvin/.tenant-migrated")
    assert "status=noop" in marker, marker
    assert not _ex(f"test -e {_REPO_IN}/.corvin/sessions").returncode == 0, "legacy sessions/ must not exist"


# ── F3 / README: the installer's own words and the proof ────────────────────

def _install_log_text() -> str:
    if _INSTALL_LOG and Path(_INSTALL_LOG).is_file():
        return Path(_INSTALL_LOG).read_text(encoding="utf-8", errors="replace")
    r = _ex("cat /tmp/corvinos-install.log 2>/dev/null; cat ~/install.log 2>/dev/null")
    return r.stdout


def test_step_17_reported_the_console_up_with_its_seconds():
    log = _install_log_text()
    assert log, "no install log: pass CORVIN_FRESH_INSTALL_LOG=<host path of the install output>"
    assert "Web Console running on http://127.0.0.1:8765/console/ (" in log, log[-2000:]
    assert "did not respond within" not in log and "did not answer within" not in log


def test_the_install_proof_passes_on_the_installed_system():
    py = "/home/tester/.local/share/uv/tools/corvinos/bin/python"
    out = _out(f"{py} {_REPO_IN}/scripts/verify_install.py --url http://127.0.0.1:8765 "
               f"--dist {_REPO_IN}/core/console/corvin_console/web-next/dist --wait 60", timeout=200)
    assert "PASS" in out, out
    assert "local login issues a session + CSRF token" in out


# ── the engine the installer provisioned answers a REAL prompt ─────────────

def test_the_installed_claude_code_answers_a_real_prompt():
    r = _ex('claude -p "Reply with exactly: PONG" --model haiku', timeout=180)
    assert r.returncode == 0, f"claude CLI failed — run fresh.sh login first? {r.stderr[-500:]}{r.stdout[-500:]}"
    assert "PONG" in r.stdout.upper(), r.stdout
