"""Fresh-system review 2026-10-21 — the install and first boot on a bare system.

Four Ubuntu 24.04 containers with only git + curl ran the README promise
(``git clone … && ./install.sh --lan``). What they showed, and what this file
pins:

* F3  — step 17 reported "Web Console did not respond within 15 s" on EVERY
        fresh install: ``corvin-install --yes`` runs in quick mode, whose real
        budget was 10 × 0.5 s = 5 s, and the first boot (marketplace sync from
        GitHub inside the lifespan) takes 10–12 s. The wait is now HTTP-level,
        120 s, and the message names the real budget and what it last saw.
* F10 — THREE audit chains after one fresh install: the adapter unit carried
        no CORVIN_HOME (only the webui unit did); the installer created a
        legacy ``sessions/`` that made the tenant migration write to the
        legacy ``global/forge`` chain; the self-test fell back to ``~/.corvin``.
        Every unit now carries CORVIN_HOME; step 2 creates the tenant layout
        only (see test_installer_integration.py); the self-test resolves the
        canonical root (see corvin_operator/bridges/shared/test_self_test.py).
* F8  — the nvm ``curl | bash`` Node fallback (edits ~/.bashrc, installs an
        unpinned Node) is replaced by the repository's own ensure-node.sh.
* F9  — Playwright's first two mirrors redirect to a hostname that no longer
        resolves; the CDN that does is pre-picked when that is the case.
        Progress bars are not written as carriage-return lines into a log.
"""
from __future__ import annotations

import http.server
import os
import socket
import sys
import threading
from pathlib import Path
from unittest import mock

import pytest

from corvinOS.installer import core as core_mod
from corvinOS.installer.steps import browser as browser_mod
from corvinOS.installer.steps import dependencies as deps_mod
from corvinOS.installer.steps import piper as piper_mod

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="systemd/launchd paths")


# ── F10: every unit the installer writes carries CORVIN_HOME ───────────────

def _installer(tmp_path: Path) -> core_mod.CorvinInstaller:
    inst = core_mod.CorvinInstaller(interactive=False, repo_root=tmp_path / "repo")
    inst.corvin_home = tmp_path / "home"
    inst.voice_config = tmp_path / "cfg"
    inst.service_manager = mock.Mock(spec=["install_service", "restart_service", "_run_systemctl"])
    return inst


def test_adapter_bridges_and_webui_units_all_carry_the_same_corvin_home(tmp_path, monkeypatch):
    inst = _installer(tmp_path)
    inst.selected_bridges = ["discord", "telegram"]
    monkeypatch.setattr(inst, "_get_adapter_command", lambda: '"/py" "/adapter.py"')
    monkeypatch.setattr(inst, "_get_bridge_command", lambda b: f'"/node" "/{b}/daemon.js"')

    inst.step_14_register_services()

    calls = {c.kwargs["name"]: c.kwargs for c in inst.service_manager.install_service.call_args_list}
    assert set(calls) == {"voice-bridge-adapter", "voice-bridge-discord", "voice-bridge-telegram", "webui"}
    for name, kw in calls.items():
        assert kw["env_vars"]["CORVIN_HOME"] == str(tmp_path / "home"), name
    # The webui additionally gets the source-tree PYTHONPATH; the adapter does
    # NOT (it builds its own sys.path from its file location).
    assert "PYTHONPATH" not in calls["voice-bridge-adapter"]["env_vars"]


# ── F3: step 17 waits for the SPA over HTTP, with an honest message ─────────

class _Shell(http.server.BaseHTTPRequestHandler):
    fail_first = 2
    seen = 0

    def do_GET(self):  # noqa: N802
        type(self).seen += 1
        code = 503 if type(self).seen <= type(self).fail_first else 200
        body = b"<html>console</html>" if code == 200 else b"build failed"
        self.send_response(code)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # silence
        pass


def _serve(handler) -> tuple[http.server.HTTPServer, int]:
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def test_step_17_waits_until_the_spa_answers_200_and_reports_the_seconds(tmp_path, monkeypatch, capsys):
    _Shell.seen = 0
    srv, port = _serve(_Shell)
    try:
        inst = _installer(tmp_path)
        inst.CONSOLE_PORT = port
        inst.CONSOLE_START_BUDGET_S = 15.0
        monkeypatch.setattr(core_mod.time, "sleep", lambda s: None)
        inst.step_17_start_console(quick_mode=True)
    finally:
        srv.shutdown()
    inst.service_manager.restart_service.assert_called_once_with("webui")
    out = capsys.readouterr().out
    assert "✓ Web Console running on http://127.0.0.1:%d/console/ (" % port in out
    assert "did not" not in out
    assert _Shell.seen == 3  # two 503s (booted-without-dist shell) were NOT accepted


def test_step_17_names_the_real_budget_and_what_it_saw_when_nothing_listens(tmp_path, monkeypatch, capsys):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    inst = _installer(tmp_path)
    inst.CONSOLE_PORT = port
    inst.CONSOLE_START_BUDGET_S = 2.0
    monkeypatch.setattr(core_mod.time, "sleep", lambda s: None)
    clock = iter([0.0, 0.5, 1.0, 1.5, 2.5, 3.0])
    monkeypatch.setattr(core_mod.time, "monotonic", lambda: next(clock))

    inst.step_17_start_console(quick_mode=False)

    out = capsys.readouterr().out
    assert "did not answer within 2 s (not listening)" in out
    assert "15 s" not in out
    assert "journalctl --user -u corvin-webui" in out


def test_the_default_budget_covers_a_fresh_first_boot():
    """Measured 2026-10-21: 10.6 s and 12.3 s from `Starting` to `Uvicorn
    running` on two fresh systems. The unit's TimeoutStartSec is 300."""
    assert 60 <= core_mod.CorvinInstaller.CONSOLE_START_BUDGET_S <= 300


# ── F8: Node fallback is the repository's bootstrap, not nvm ───────────────

def test_node_fallback_runs_ensure_node_sh_under_the_resolved_root(tmp_path, monkeypatch, capsys):
    home = tmp_path / "root" / ".corvin"
    script = Path(deps_mod.__file__).resolve().parents[3] / "scripts" / "ensure-node.sh"
    assert script.is_file()
    monkeypatch.setattr("corvinOS.shared.paths.corvin_home", lambda: home)
    ran = {}

    def _run(cmd, **kw):
        ran.setdefault("cmd", cmd)
        ran.setdefault("env", kw.get("env", {}))
        (home / "node" / "bin").mkdir(parents=True, exist_ok=True)
        node = home / "node" / "bin" / "node"
        node.write_text("#!/bin/sh\necho v24.18.0\n")
        node.chmod(0o755)
        npm = home / "node" / "bin" / "npm"
        npm.write_text("#!/bin/sh\necho 11\n")
        npm.chmod(0o755)
        return mock.Mock(returncode=0, stdout="v24.18.0\n")

    monkeypatch.setattr(deps_mod.subprocess, "run", _run)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    assert deps_mod._ensure_node_via_bootstrap() is True
    assert ran["cmd"] == ["bash", str(script)]
    assert ran["env"]["CORVIN_HOME"] == str(home)
    assert os.environ["PATH"].startswith(str(home / "node" / "bin"))


def test_the_nvm_curl_pipe_bash_path_is_gone():
    src = Path(deps_mod.__file__).read_text(encoding="utf-8")
    assert "nvm-sh/nvm" not in src
    assert "nvm install --lts" not in src


# ── F9: Playwright mirror pre-pick, progress without a terminal ────────────

def test_playwright_host_is_pinned_only_when_the_dead_mirror_does_not_resolve(monkeypatch):
    monkeypatch.delenv("PLAYWRIGHT_DOWNLOAD_HOST", raising=False)

    def _dead(host, *a, **k):
        raise socket.gaierror(-3008, "Name or service not known")
    monkeypatch.setattr(socket, "getaddrinfo", _dead)
    assert browser_mod._playwright_env()["PLAYWRIGHT_DOWNLOAD_HOST"] == browser_mod._PLAYWRIGHT_LIVE_MIRROR

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [("ok",)])
    assert "PLAYWRIGHT_DOWNLOAD_HOST" not in browser_mod._playwright_env()

    monkeypatch.setenv("PLAYWRIGHT_DOWNLOAD_HOST", "https://mirror.example")
    monkeypatch.setattr(socket, "getaddrinfo", _dead)
    assert browser_mod._playwright_env()["PLAYWRIGHT_DOWNLOAD_HOST"] == "https://mirror.example"


def test_chromium_download_output_is_captured_without_a_terminal_and_shown_on_failure(monkeypatch, capsys):
    monkeypatch.setattr(browser_mod, "_chromium_present", lambda: False)
    monkeypatch.setattr(browser_mod, "_pip_install", lambda *a, **k: True)
    monkeypatch.setattr(browser_mod, "_stdout_is_tty", lambda: False)
    monkeypatch.setattr(browser_mod, "_playwright_env", lambda: {"X": "1"})
    seen = {}

    def _run(cmd, **kw):
        seen.update(kw)
        return mock.Mock(returncode=1, stdout="line1\nError: getaddrinfo ENOTFOUND host\n", stderr="")
    monkeypatch.setattr(browser_mod.subprocess, "run", _run)
    browser_mod.ensure_browser(interactive=False)
    assert seen.get("capture_output") is True and seen.get("env") == {"X": "1"}
    out = capsys.readouterr().out
    assert "ENOTFOUND" in out and "Chromium download failed" in out


def test_piper_progress_without_a_terminal_is_a_few_lines_not_carriage_returns(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(piper_mod, "_stdout_is_tty", lambda: False)

    def _fetch(url, dest, *, attempts, progress):
        for done in range(0, 101):
            progress(done, 100)
        return True
    monkeypatch.setattr(piper_mod._vm, "fetch", _fetch)
    assert piper_mod._fetch("http://x/model.onnx", tmp_path / "m.onnx") is True
    out = capsys.readouterr().out
    assert "\r" not in out
    assert [line.split()[0] for line in out.strip().splitlines()] == ["0%", "25%", "50%", "75%", "100%"]


# ── F17: nothing creates ~/.corvin next to the real runtime root ───────────

def test_instance_registry_lives_under_corvin_home_and_makes_no_dir_at_import(tmp_path, monkeypatch):
    """Attributed with a Python audit hook on the fresh system: importing
    corvin_console.routes.vibe_metrics_api → core.learning.instance_registry
    ran InstanceRegistry() at module level with the literal
    "~/.corvin/instances.json" and mkdir'd ~/.corvin on every console boot."""
    import importlib
    import core.learning.instance_registry as ir

    root = tmp_path / "root"
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(root))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    ir = importlib.reload(ir)
    reg = ir.get_instance_registry()
    assert reg.registry_path == root / "instances.json"
    assert not root.exists(), "no directory may be created at import"
    assert not (fake_home / ".corvin").exists()

    reg.register_instance("inst-1", "host", "2.0.0")
    assert (root / "instances.json").is_file()
    assert not (fake_home / ".corvin").exists()


def test_every_product_restart_path_clears_a_tripped_start_limit():
    """systemd counts every start toward StartLimitBurst, deliberate ones too; a unit that
    tripped it refuses `restart` for the rest of the window. The watchdog, update.sh and the
    installer's restore path therefore reset-failed first (fresh system, 2026-10-21: the
    console sat in start-limit-hit while the watchdog kept failing to restart it)."""
    root = Path(__file__).resolve().parents[1]
    watchdog = (root / "scripts" / "setup.sh").read_text(encoding="utf-8")
    assert "systemctl --user reset-failed corvin-webui.service 2>/dev/null; systemctl --user restart corvin-webui.service" in watchdog
    updater = (root / "update.sh").read_text(encoding="utf-8")
    assert 'systemctl --user reset-failed "$_u"' in updater
    core = (root / "corvinOS" / "installer" / "core.py").read_text(encoding="utf-8")
    assert '["systemctl", "--user", "reset-failed", "corvin-webui.service"]' in core
