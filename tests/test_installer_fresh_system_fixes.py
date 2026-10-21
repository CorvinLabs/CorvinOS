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
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
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


# ═══════════════════════════════════════════════════════════════════════════
# Round 2 (2026-10-21, refutation of the fixes above + what round 1 missed)
# ═══════════════════════════════════════════════════════════════════════════

_ROOT = Path(__file__).resolve().parents[1]


def _tool_dir(tmp_path: Path, *tools: str) -> Path:
    """A PATH with ONLY the named tools (symlinks to the real ones)."""
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    for t in tools:
        real = shutil.which(t)
        if real is None:
            pytest.skip(f"{t} not on this host")
        (d / t).symlink_to(real)
    return d


# ── R2-1: install.sh's probes work on a wget-only system ─────────────────────

def test_install_sh_http_probe_answers_through_wget_when_curl_is_absent(tmp_path):
    """Phase 0 accepts wget without curl; the console probe used to be curl-only
    and printed nothing on such a system, so install.sh exited 2 'did not come
    up' while the console was serving."""
    src = (_ROOT / "install.sh").read_text(encoding="utf-8")
    start, end = src.index("_fetch() {"), src.index("# Argument parsing")
    helpers = src[start:end]
    _Shell.seen, _Shell.fail_first = 0, 1
    srv, port = _serve(_Shell)
    try:
        bindir = _tool_dir(tmp_path, "wget", "awk", "sh")
        script = f'{helpers}\n_http_code "http://127.0.0.1:{port}/"; echo; _http_code "http://127.0.0.1:{port}/"; echo; _http_code "http://127.0.0.1:1/"; echo; echo END'
        r = subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=30,
                           env={"PATH": str(bindir), "HOME": str(tmp_path)})
    finally:
        srv.shutdown()
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == ["503", "200", "", "END"], r.stdout
    assert src.count("curl -s -o /dev/null") == 1  # only inside _http_code: every probe goes through it
    assert src.count("curl -fs -m 3") == 0


# ── R2-2: ensure-node.sh verifies the archive, and copes with a space ───────

def _fake_node_release(tmp_path: Path, *, tamper: bool = False) -> Path:
    """A fake nodejs.org: the archive the script asks for plus SHASUMS256.txt."""
    rel = tmp_path / "release"
    rel.mkdir()
    name = f"node-v24.18.0-{'darwin' if sys.platform == 'darwin' else 'linux'}-{'arm64' if os.uname().machine in ('aarch64', 'arm64') else 'x64'}"
    tree = tmp_path / "tree" / name / "bin"
    tree.mkdir(parents=True)
    node = tree / "node"
    node.write_text("#!/bin/sh\necho v24.18.0\n")
    node.chmod(0o755)
    (tree / "npm").write_text("#!/bin/sh\necho 11\n")
    ext = "tar.xz" if shutil.which("xz") else "tar.gz"
    archive = rel / f"{name}.{ext}"
    with tarfile.open(archive, "w:xz" if ext == "tar.xz" else "w:gz") as tf:
        tf.add(tmp_path / "tree" / name, arcname=name)
    import hashlib
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (rel / "SHASUMS256.txt").write_text(f"{digest}  {archive.name}\n{'0' * 64}  other.tar.gz\n")
    if tamper:
        archive.write_bytes(archive.read_bytes() + b"\0")
    return rel


def _run_ensure_node(tmp_path: Path, rel: Path, corvin_home: Path) -> subprocess.CompletedProcess:
    """ensure-node.sh with a `curl` that serves the fake release instead of nodejs.org."""
    bindir = tmp_path / "shimbin"
    bindir.mkdir(exist_ok=True)
    curl = bindir / "curl"
    curl.write_text("#!/bin/sh\n# fake curl: -o DEST URL -> copy the release file\n"
                    "while [ $# -gt 1 ]; do case \"$1\" in -o) dest=\"$2\"; shift ;; esac; shift; done\n"
                    f"cp \"{rel}/$(basename \"$1\")\" \"$dest\"\n")
    curl.chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "CORVIN_HOME": str(corvin_home), "HOME": str(tmp_path)}
    return subprocess.run(["bash", str(_ROOT / "scripts" / "ensure-node.sh")], capture_output=True, text=True,
                          timeout=60, env=env)


def test_ensure_node_verifies_the_archive_against_shasums256_and_a_root_with_a_space(tmp_path):
    rel = _fake_node_release(tmp_path)
    home = tmp_path / "root with space" / ".corvin"
    r = _run_ensure_node(tmp_path, rel, home)
    assert r.returncode == 0, r.stderr
    assert "verified against SHASUMS256.txt" in r.stderr
    assert (home / "node" / "bin" / "node").is_file()
    out = subprocess.run([str(home / "node" / "bin" / "node"), "--version"], capture_output=True, text=True)
    assert out.stdout.strip() == "v24.18.0"


def test_ensure_node_refuses_a_tampered_archive(tmp_path):
    rel = _fake_node_release(tmp_path, tamper=True)
    home = tmp_path / ".corvin"
    r = _run_ensure_node(tmp_path, rel, home)
    assert r.returncode == 1
    assert "checksum mismatch" in r.stderr
    assert not (home / "node").exists(), "nothing may be extracted from an unverified archive"


# ── R2-3: no systemd user session → setup.sh is a no-op success ───────────────

def test_setup_sh_without_systemd_is_a_noop_success_not_an_error(tmp_path):
    """macOS (launchd keeps the console alive) and a Linux without a user
    session: the watchdog unit cannot and need not be registered. setup.sh
    exited 1 there and install.sh printed 'Watchdog setup encountered an error'
    on every such install."""
    bindir = _tool_dir(tmp_path, "bash", "mkdir", "dirname", "printf", "echo", "cat", "chmod", "sleep", "grep")
    home = tmp_path / "home"
    r = subprocess.run(["bash", str(_ROOT / "scripts" / "setup.sh")], capture_output=True, text=True, timeout=30,
                       env={"PATH": str(bindir), "HOME": str(tmp_path), "CORVIN_HOME": str(home)})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "watchdog not needed here" in r.stdout
    assert (home / "logs").is_dir()
    assert not (tmp_path / ".config" / "systemd").exists()
    inst = (_ROOT / "install.sh").read_text(encoding="utf-8")
    assert "No systemd user session here" in inst  # install.sh does not even call it then


# ── R2-4: one resolver answer for the checkout root ──────────────────────────

def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_core_paths_tenant_agrees_with_the_forge_resolver_inside_a_checkout(monkeypatch):
    """core/paths/tenant.py adopted <checkout>/.corvin only once the directory
    existed and said ~/.corvin before — the one resolver of four that could
    flip mid-process. In a checkout all four answer <checkout>/.corvin."""
    monkeypatch.delenv("CORVIN_HOME", raising=False)
    from core.paths import tenant  # the real module (needs core.tenants)
    forge = _load(_ROOT / "corvin_operator" / "forge" / "forge" / "paths.py", "r2_forge_paths")
    shared = _load(_ROOT / "corvinOS" / "shared" / "paths.py", "r2_shared_paths")
    assert tenant.corvin_home() == forge.corvin_home() == shared.corvin_home() == _ROOT / ".corvin"
    src = (_ROOT / "core" / "paths" / "tenant.py").read_text(encoding="utf-8")
    assert "repo_local.is_dir()" not in src


# ── R2-5: hooks run from Claude Code's plugin cache, outside any checkout ────

_HOOKS = ("path_gate.py", "web_trust_gate.py", "notification_relay.py")


def _hooks_in_a_plugin_cache(tmp_path: Path) -> Path:
    """Copies of the hooks where Claude Code runs them: ~/.claude/plugins/cache/…
    — a tree whose ancestor ~/.claude has a plugins/ directory."""
    home = tmp_path / "home"
    cache = home / ".claude" / "plugins" / "cache" / "corvin-voice-local" / "voice" / "0.3.0" / "hooks"
    cache.mkdir(parents=True)
    for h in _HOOKS:
        shutil.copy(_ROOT / "corvin_operator" / "voice" / "hooks" / h, cache / h)
    return home


def test_hooks_in_the_plugin_cache_never_resolve_under_dot_claude(tmp_path, monkeypatch):
    home = _hooks_in_a_plugin_cache(tmp_path)
    cache = home / ".claude" / "plugins" / "cache" / "corvin-voice-local" / "voice" / "0.3.0" / "hooks"
    monkeypatch.delenv("CORVIN_HOME", raising=False)
    monkeypatch.delenv("VOICE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    pg = _load(cache / "path_gate.py", "r2_cache_path_gate")
    wt = _load(cache / "web_trust_gate.py", "r2_cache_web_trust_gate")
    nr = _load(cache / "notification_relay.py", "r2_cache_notification_relay")
    # No installer record: the documented last resort, never ~/.claude/.corvin.
    assert pg._corvin_home() == home / ".corvin"
    assert wt._corvin_home() == home / ".corvin"
    assert nr._voice_dir() == home / ".corvin" / "voice"
    for mod in (pg, wt, nr):
        assert ".claude" not in str(mod._corvin_home() if hasattr(mod, "_corvin_home") else mod._voice_dir())

    # The installer recorded the live root (corvin-install step 18): that wins.
    cfg = tmp_path / "xdg" / "corvin-voice"
    cfg.mkdir(parents=True)
    (cfg / "installer.json").write_text(json.dumps({"corvin_home": str(tmp_path / "srv" / "CorvinOS" / ".corvin")}))
    assert pg._corvin_home() == tmp_path / "srv" / "CorvinOS" / ".corvin"
    assert wt._corvin_home() == tmp_path / "srv" / "CorvinOS" / ".corvin"
    assert nr._voice_dir() == tmp_path / "srv" / "CorvinOS" / ".corvin" / "voice"
    # The tenant chain, not the pre-ADR-0007 <home>/global/forge/audit.jsonl.
    assert wt._tenant_chain() == tmp_path / "srv" / "CorvinOS" / ".corvin" / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"


def test_hooks_inside_the_checkout_still_resolve_the_checkout_root(monkeypatch):
    monkeypatch.delenv("CORVIN_HOME", raising=False)
    for h in _HOOKS:
        mod = _load(_ROOT / "corvin_operator" / "voice" / "hooks" / h, f"r2_repo_{h[:-3]}")
        got = mod._corvin_home() if hasattr(mod, "_corvin_home") else mod._voice_dir().parent
        assert got == _ROOT / ".corvin", (h, got)
        src = (_ROOT / "corvin_operator" / "voice" / "hooks" / h).read_text(encoding="utf-8")
        assert 'if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():' not in src, h


def test_web_trust_gate_audit_target_is_the_tenant_chain(monkeypatch, tmp_path):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "root"))
    monkeypatch.setenv("CORVIN_TENANT_ID", "acme")
    wt = _load(_ROOT / "corvin_operator" / "voice" / "hooks" / "web_trust_gate.py", "r2_wt")
    assert wt._tenant_chain() == tmp_path / "root" / "tenants" / "acme" / "global" / "forge" / "audit.jsonl"
    src = (_ROOT / "corvin_operator" / "voice" / "hooks" / "web_trust_gate.py").read_text(encoding="utf-8")
    assert 'audit_target = _corvin_home() / "global"' not in src


# ── R2-6: log levels on a clean boot ─────────────────────────────────────────

def test_a2a_ingress_listening_is_an_info_line_not_a_warning(monkeypatch, caplog):
    import logging
    from types import SimpleNamespace

    from core.a2a import a2a_connectivity as conn

    monkeypatch.setattr(conn.a2a_ingress, "load_config", lambda: SimpleNamespace(host="0.0.0.0", port=8775))
    monkeypatch.setattr(conn, "_audit", lambda *a, **k: None)
    fake = SimpleNamespace(ingress=SimpleNamespace(ensure=lambda cfg: "started", last_error=""),
                           wake=lambda: None, _my_url_note="")
    with caplog.at_level(logging.INFO, logger="corvin.a2a.connectivity"):
        conn.ConnectivityManager._ensure_ingress(fake)
    rec = [r for r in caplog.records if "ingress listening" in r.getMessage()]
    assert rec and rec[0].levelno == logging.INFO, [(r.levelname, r.getMessage()) for r in caplog.records]


# ── R2-7: the Windows installer/updater use the same root as Python ─────────

def test_windows_installer_and_updater_resolve_the_checkout_root():
    inst = (_ROOT / "install.ps1").read_text(encoding="utf-8")
    assert "$CorvinHome = if ($env:CORVIN_HOME) { $env:CORVIN_HOME } else { $RepoLocalCorvinHome }" in inst
    assert "ADOPTED, never created" not in inst
    # The VBS line is written from a PowerShell string, hence the doubled quotes.
    assert 'Environment(""PROCESS"")(""CORVIN_HOME"")' in inst  # the logon task carries the root
    upd = (_ROOT / "update.ps1").read_text(encoding="utf-8")
    assert '$CorvinHome = Join-Path $Src ".corvin"' in upd


# ── R2-8: the harness cannot pass for the wrong reason ──────────────────────

def test_fresh_harness_keeps_the_install_output_and_the_live_test_has_a_positive_control():
    fresh = (_ROOT / "tests" / "e2e" / "fresh_system" / "fresh.sh").read_text(encoding="utf-8")
    assert "tee ~/install.log" in fresh
    live = (_ROOT / "tests" / "e2e" / "fresh_system" / "test_fresh_install_live.py").read_text(encoding="utf-8")
    assert '"[Step 17] Starting Web Console" in log' in live
    assert "/tmp/corvinos-install.log" not in live.split("def _install_log_text")[1].split("def test_")[0].replace(
        "NOT /tmp/corvinos-install.log", "")


def test_launchd_log_hint_names_the_resolved_root_not_tilde_corvin():
    core = (_ROOT / "corvinOS" / "installer" / "core.py").read_text(encoding="utf-8")
    assert "~/.corvin/logs/launchd" not in core


def test_no_installer_side_shell_script_defaults_to_tilde_corvin():
    """Every shell script the installer runs or registers resolves the checkout
    root first (CORVIN_HOME → <checkout>/.corvin); a bare ``~/.corvin`` default
    is how a fresh install got its second root (F10)."""
    for rel in ("install.sh", "update.sh", "scripts/setup.sh", "scripts/ensure-node.sh",
                "scripts/watchdog-health-check.sh", "scripts/corvin-watchdog-alert.sh"):
        src = (_ROOT / rel).read_text(encoding="utf-8")
        assert 'CORVIN_HOME="${CORVIN_HOME:-$HOME/.corvin}"' not in src, rel


# ── R2-9: a clean first boot carries no false WARNING/CRITICAL ──────────────

def test_the_instance_seed_exists_before_the_first_audit_record_of_a_boot(monkeypatch, tmp_path):
    """On every fresh install the licence loader wrote ``license.token_source``
    55 ms before it created ``instance_seed.key`` and then reported the seed as
    NEWLY CREATED on an existing chain — CRITICAL, 'possible G-vector attack',
    on a system nobody had touched."""
    from corvin_operator.license import validator as v

    order: list[str] = []
    monkeypatch.setattr(v, "_init_instance_seed", lambda: order.append("seed"))
    monkeypatch.setattr(v, "_audit", lambda event, **kw: order.append(event))
    monkeypatch.setattr(v, "_find_token", lambda: None)
    monkeypatch.setattr(v, "_set_feature_root_key", lambda *a, **k: None)
    monkeypatch.setattr(v, "_set_active_license", lambda *a, **k: None)
    monkeypatch.setattr(v, "_CONFIG_DIR_SNAPSHOT", tmp_path / "cfg", raising=False)
    monkeypatch.setattr(v, "_CORVIN_HOME_SNAPSHOT", tmp_path / "home", raising=False)
    monkeypatch.delenv("CORVIN_LICENSE_KEY", raising=False)
    v.load_license_from_env(force=True)
    assert order and order[0] == "seed", order
    assert "license.token_source" in order and order.index("license.token_source") > 0


def test_house_rules_boot_check_success_is_info_and_problems_stay_warning(monkeypatch, caplog):
    import logging

    from corvin_operator.bridges.shared import house_rules as hr

    # Explicit sinks: the success line goes to ok_fn, never to log_fn.
    warns: list[str] = []
    oks: list[str] = []
    monkeypatch.setattr(hr, "_resolve_helper_claude_bin", lambda: sys.executable)
    hr.house_rules_boot_health_check(log_fn=warns.append, ok_fn=oks.append)
    assert oks and "CLI resolved" in oks[0] and warns == []
    monkeypatch.setattr(hr, "_resolve_helper_claude_bin", lambda: "/nonexistent/claude-r2")
    hr.house_rules_boot_health_check(log_fn=warns.append, ok_fn=oks.append)
    assert warns and "not found" in warns[0]
    # Defaults: INFO for the success line, WARNING for the problem line
    # (the function fetches its logger by name at call time).
    fake = mock.Mock()
    real = logging.getLogger
    monkeypatch.setattr(logging, "getLogger", lambda name=None: fake if name == "corvin.house_rules" else real(name))
    monkeypatch.setattr(hr, "_resolve_helper_claude_bin", lambda: sys.executable)
    hr.house_rules_boot_health_check()
    assert fake.info.call_count == 1 and "CLI resolved" in fake.info.call_args[0][0]
    assert fake.warning.call_count == 0
    monkeypatch.setattr(hr, "_resolve_helper_claude_bin", lambda: "/nonexistent/claude-r2")
    hr.house_rules_boot_health_check()
    assert fake.warning.call_count == 1 and "not found" in fake.warning.call_args[0][0]
    gw = (_ROOT / "core" / "gateway" / "corvin_gateway" / "app.py").read_text(encoding="utf-8")
    assert "ok_fn=_hr_log.info" in gw


def test_seal_loader_stub_is_the_normal_state_logged_at_info(caplog):
    import logging

    from corvin_operator.license import seal_loader

    with caplog.at_level(logging.INFO, logger=seal_loader.log.name):
        seal_loader._load_stub()
    rec = [r for r in caplog.records if "seal" in r.getMessage().lower()]
    assert rec and all(r.levelno == logging.INFO for r in rec), [(r.levelname, r.getMessage()) for r in rec]


# ── R2-10: ONE updater — the plugin's tag updater stands down in a checkout ──

def test_voice_autoupdate_stands_down_inside_a_corvinos_checkout(tmp_path):
    """Real execution: a git checkout carrying .corvin_repo, the plugin's
    scripts copied in, a fake `git` that records every call. The guard must
    fire BEFORE any fetch/checkout."""
    repo = tmp_path / "repo"
    (repo / "corvin_operator" / "voice" / "scripts").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / ".corvin_repo").write_text("")
    for f in ("autoupdate.sh", "voice_lib.sh"):
        shutil.copy(_ROOT / "corvin_operator" / "voice" / "scripts" / f, repo / "corvin_operator" / "voice" / "scripts" / f)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "git_calls"
    fake_git = bindir / "git"
    fake_git.write_text("#!/bin/bash\necho \"$@\" >> \"$GIT_CALLS\"\n"
                        "case \"$*\" in *show-toplevel*) echo \"$REPO\";; *fetch*|*checkout*) exit 1;; esac\nexit 0\n")
    fake_git.chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "GIT_CALLS": str(calls), "REPO": str(repo),
           "HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / "xdg"), "VOICE_CONFIG_DIR": str(tmp_path / "vc")}
    r = subprocess.run(["bash", str(repo / "corvin_operator" / "voice" / "scripts" / "autoupdate.sh")],
                       capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, r.stderr
    log = "".join(p.read_text(encoding="utf-8", errors="replace") for p in (tmp_path / "vc").rglob("*.log")) \
        + "".join(p.read_text(encoding="utf-8", errors="replace") for p in (tmp_path / "xdg").rglob("*.log")) if (tmp_path / "vc").exists() or (tmp_path / "xdg").exists() else ""
    recorded = calls.read_text() if calls.exists() else ""
    assert "fetch" not in recorded and "checkout" not in recorded, recorded
    assert "is a CorvinOS checkout" in (log + r.stdout + r.stderr), (log, r.stdout, r.stderr, recorded)


def test_auto_update_is_scheduled_once_per_process_and_logs_into_the_corvin_namespace(monkeypatch):
    import asyncio

    from corvin_console import update_manager as um

    assert um.logger.name.startswith("corvin.")
    monkeypatch.setattr(um, "_SCHEDULED_FOR", set())
    started: list[str] = []

    async def _fake(tid, *, enabled):
        started.append(tid)
        return "up-to-date"
    monkeypatch.setattr(um, "auto_update_on_start", _fake)
    monkeypatch.setattr("corvin_console.routes.settings._read_auto_update", lambda: True)

    async def _run():
        um.schedule_auto_update("t1")
        um.schedule_auto_update("t1")  # the second host's lifespan, same process
        await asyncio.sleep(0.05)
    asyncio.run(_run())
    assert started == ["t1"]


# ── R2-11: the hooks run on a system without python3 ─────────────────────────

def test_hooks_launcher_finds_the_tool_venv_python_when_python3_is_absent(tmp_path):
    """Real execution with a PATH that has no python3: the launcher must find
    the corvinos tool venv's interpreter (here: a fake venv whose python3 is a
    copy of this interpreter's shim) and run the hook with stdin intact."""
    bindir = _tool_dir(tmp_path, "sh", "dirname", "basename", "cat", "env")
    venv_bin = tmp_path / "data" / "uv" / "tools" / "corvinos" / "bin"
    venv_bin.mkdir(parents=True)
    py = venv_bin / "python3"
    py.write_text(f"#!/bin/sh\nexec {sys.executable} \"$@\"\n")
    py.chmod(0o755)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    shutil.copy(_ROOT / "corvin_operator" / "voice" / "hooks" / "with_python.sh", hooks / "with_python.sh")
    (hooks / "probe.py").write_text("import sys; print('ran with', sys.version_info[0], 'stdin=' + sys.stdin.read().strip())\n")
    r = subprocess.run(["sh", str(hooks / "with_python.sh"), "probe.py"], input="PAYLOAD", capture_output=True,
                       text=True, timeout=30, env={"PATH": str(bindir), "HOME": str(tmp_path),
                                                   "XDG_DATA_HOME": str(tmp_path / "data")})
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "ran with 3 stdin=PAYLOAD"
    # No interpreter anywhere: a loud failure, never a silent 127.
    r2 = subprocess.run(["sh", str(hooks / "with_python.sh"), "probe.py"], capture_output=True, text=True,
                        timeout=30, env={"PATH": str(bindir), "HOME": str(tmp_path / "nowhere")})
    assert r2.returncode == 1 and "no Python 3 found" in r2.stderr
    manifest = json.loads((_ROOT / "corvin_operator" / "voice" / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    cmds = [h["command"] for group in manifest["hooks"].values() for entry in group for h in entry["hooks"]]
    assert not [c for c in cmds if c.startswith("python3 ")], cmds
    assert all("with_python.sh" in c for c in cmds if c.endswith(".py")), cmds


def test_update_sh_re_provisions_units_written_before_the_start_limit_and_root_fixes():
    """A pre-2026-10-21 install carries `StartLimitBurst=5` on the webui unit and no
    CORVIN_HOME on the adapter unit; update.sh must notice either and re-run the
    installer's service registration (static review round 2, Patch D)."""
    upd = (_ROOT / "update.sh").read_text(encoding="utf-8")
    assert "grep -q '^StartLimitBurst=5$'" in upd
    assert "list-unit-files 'corvin-*adapter*.service'" in upd and "grep -q 'CORVIN_HOME='" in upd
    assert '[ "$_units_stale" = 1 ]' in upd
    # The adapter unit's real name matches that glob (service_manager prefixes `corvin-`).
    core = (_ROOT / "corvinOS" / "installer" / "core.py").read_text(encoding="utf-8")
    assert 'name="voice-bridge-adapter"' in core


def test_every_unit_carries_the_product_interpreter(tmp_path, monkeypatch):
    """A bare README install has no `python3`; the bridges spawn the product's
    CLIs through CORVIN_PYTHON (shared/js/python_bin.js), which every unit the
    installer writes must carry (round 2, 2026-10-21)."""
    import sys

    inst = _installer(tmp_path)
    env = inst._service_env_vars()
    assert env["CORVIN_PYTHON"] == sys.executable
    assert env["CORVIN_HOME"] == str(inst.corvin_home)
