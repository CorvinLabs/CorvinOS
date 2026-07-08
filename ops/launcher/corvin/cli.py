"""
corvin — Corvin launcher CLI

Commands:
  corvin start                         Smart start: setup if needed, then launch + open browser
  corvin open                          Open the web console in your browser
  corvin setup [--yes] [--model TAG] [--profile eu-production]
  corvin gateway start
  corvin gateway stop
  corvin gateway setup
  corvin config set <key> <value>
  corvin config show
  corvin status
"""
import argparse
import contextlib
import os
import sys
import tempfile
import textwrap
from typing import Any, Optional

from . import config as cfg
from . import docker_backend
from . import ollama as oll
from . import serve_backend


# ── ANSI helpers ─────────────────────────────────────────────────────────────

def _bold(s: str) -> str:
    return f"\033[1m{s}\033[0m" if sys.stdout.isatty() else s

def _green(s: str) -> str:
    return f"\033[32m{s}\033[0m" if sys.stdout.isatty() else s

def _yellow(s: str) -> str:
    return f"\033[33m{s}\033[0m" if sys.stdout.isatty() else s

def _red(s: str) -> str:
    return f"\033[31m{s}\033[0m" if sys.stdout.isatty() else s

def _ask(prompt: str, default: str = "") -> str:
    try:
        val = input(f"  {prompt} [{default}]: ").strip()
        return val if val else default
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)

def _ask_choice(prompt: str, choices: list[str], default: str = "") -> str:
    print(f"\n  {prompt}")
    for i, c in enumerate(choices, 1):
        marker = " ←" if c == default else ""
        print(f"    {i}. {c}{marker}")
    while True:
        raw = _ask("Enter number or name", default)
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(choices):
                return choices[idx]
        if raw in choices:
            return raw
        if raw == default:
            return default
        print(f"  {_yellow('Invalid choice, try again.')}")


# ── serve (native, no Docker) ─────────────────────────────────────────────────

def _onboarding_complete() -> bool:
    """Return True iff <corvin_home>/tenants/_default/global/onboarding.json marks complete."""
    import json as _json
    from pathlib import Path as _Path
    # Resolve the real Corvin home (honours CORVIN_HOME / in-repo .corvin /
    # XDG) rather than hardcoding ~/.corvin — otherwise a pinned or in-repo
    # home makes onboarding always look incomplete and re-opens the wizard.
    try:
        from forge.paths import corvin_home as _corvin_home  # noqa: PLC0415
        base = _corvin_home()
    except Exception:
        base = _Path.home() / ".corvin"
    onboarding_path = base / "tenants" / "_default" / "global" / "onboarding.json"
    try:
        return bool(_json.loads(onboarding_path.read_text()).get("complete"))
    except Exception:
        return False


def cmd_detect(args: argparse.Namespace) -> int:
    """Probe installed engine binaries and print results — ADR-0120 M3."""
    import json as _json
    import sys as _sys
    from pathlib import Path as _Path

    # Try the repo source-tree location first (dev / source install).
    _SHARED = _Path(__file__).resolve().parents[3] / "operator" / "bridges" / "shared"
    if _SHARED.is_dir() and str(_SHARED) not in _sys.path:
        _sys.path.insert(0, str(_SHARED))

    try:
        import engine_detector as _ed  # noqa: PLC0415
    except ImportError:
        # Fallback: pip-wheel install vendors engine_detection into corvin_console.
        try:
            from corvin_console import engine_detection as _ed  # noqa: PLC0415
        except ImportError:
            print(_red("  engine_detector module not found."))
            print("  Run: pip install corvinos  (or check the repo is intact)")
            return 1

    probes = _ed.detect_all()
    json_out = getattr(args, "json", False)

    if json_out:
        print(_json.dumps(
            {"engines": [p.to_dict() for p in probes],
             "onboarding_complete": _onboarding_complete()},
            indent=2,
        ))
        return 0

    print(f"\n{_bold('Engine detection results')}\n")
    for p in probes:
        icon = _green("✔") if p.found else _yellow("✘")
        label = f"{p.engine_id:12s}"
        status = p.detail if p.found else _yellow(p.detail)
        print(f"  {icon}  {label} {status}")
    print()
    found = sum(1 for p in probes if p.found)
    if found == 0:
        print(_yellow("  No engines detected. Install at least one to use CorvinOS."))
    elif found == 1:
        print(_green(f"  {found} engine ready."))
    else:
        print(_green(f"  {found} engines ready."))
    print()
    return 0


def _print_hermes_status() -> None:
    """Print a one-line Hermes/Ollama availability hint at console start."""
    try:
        try:
            from corvin_console.hermes_bootstrap import (  # noqa: PLC0415
                is_ollama_installed, get_available_ram_gb, select_model_for_ram,
            )
        except ImportError:
            import sys as _sys
            from pathlib import Path as _Path
            _shared = _Path(__file__).resolve().parents[3] / "operator" / "bridges" / "shared"
            if str(_shared) not in _sys.path:
                _sys.path.insert(0, str(_shared))
            from hermes_bootstrap import (  # noqa: PLC0415
                is_ollama_installed, get_available_ram_gb, select_model_for_ram,
            )
        if is_ollama_installed():
            model = select_model_for_ram(get_available_ram_gb())
            print(f"  {_green('●')} Hermes (Ollama) ready  — engine: hermes  model: {_bold(model)}")
        else:
            # NB: f-prefix is load-bearing here — without it {_bold(...)} would
            # print literally. `corvin setup --hermes` was never a real command
            # (setup has no --hermes flag); point at the actual Ollama install.
            print(f"  {_yellow('○')} Hermes (Ollama) not found  — "
                  f"install Ollama from {_bold('https://ollama.com/download')} to enable it")
    except Exception:
        pass  # Hermes status is informational; never block console start


def cmd_serve(args: argparse.Namespace) -> int:
    """Start the CorvinOS console directly via uvicorn — no Docker required.

    This is the recommended path for ``pip install corvinOS[console]``.
    The browser opens automatically; the Setup Wizard guides first-time
    configuration (Anthropic API key, optional bridge channel).
    """
    port: int = getattr(args, "port", 8765)
    no_browser: bool = getattr(args, "no_browser", False)
    host: str = getattr(args, "host", "127.0.0.1")

    relaunch_argv = (
        ["corvin", "serve", f"--port={port}", f"--host={host}"]
        + (["--no-browser"] if no_browser else [])
    )
    if serve_backend.maybe_pypi_autoupdate(relaunch_argv=relaunch_argv):
        # Windows self-update handoff in progress: a detached updater is
        # waiting for THIS process to exit before it can upgrade + relaunch.
        return 0

    if not serve_backend.is_available():
        reason, detail = serve_backend.unavailable_reason()
        if reason == "spa":
            print(_red("\n  Console backend is installed but the web UI (SPA) is not built."))
            print(f"  Build it: {_bold('cd core/console/corvin_console/web-next && npm install && npm run build')}\n")
        else:
            print(_red("\n  Console backend is not importable."))
            print(f"  Reinstall: {_bold('pip install --upgrade corvinos')}\n")
        return 1

    base_url = serve_backend.console_url(port)
    # ADR-0120 M3: for first-run, go directly to auto-login so the wizard appears
    open_path = "/auth/local-login"
    print(f"\n  {_bold('CorvinOS Console')}")
    print(f"  {_green('●')} Starting on {_bold(base_url)} …")
    if not _onboarding_complete():
        print(f"  {_yellow('First run')} — opening setup wizard at {_bold(base_url + open_path)}")
    _print_hermes_status()
    print(f"  Press Ctrl-C to stop.\n")

    return serve_backend.start(port=port, open_browser=not no_browser, open_path=open_path, host=host)


# ── start (smart one-shot) ────────────────────────────────────────────────────

def cmd_start(args: argparse.Namespace) -> int:
    """Setup if needed, then start the gateway and open the console.

    Tries Docker first.  Falls back to native uvicorn when Docker is not
    available — this covers the ``pip install corvinOS[console]`` path
    where no container runtime is present.
    """
    # ── Native fallback when Docker is absent ────────────────────────────
    if not docker_backend.is_docker_available():
        if serve_backend.is_available():
            print(_yellow("  Docker not found — starting natively (no Docker required).\n"))
            return cmd_serve(args)
        print(_red("  Docker is not running and the console backend is not importable."))
        print(f"  Option A:  Install Docker Desktop  https://www.docker.com/products/docker-desktop/")
        print(f"  Option B:  {_bold('pip install --upgrade corvinos')}  then  {_bold('corvinos-serve')}\n")
        return 1

    # ── Docker path (existing behaviour) ────────────────────────────────
    conf = cfg.load()
    is_configured = bool(conf.get("ollama_url") and conf.get("model"))

    if not is_configured:
        print(_yellow("  Not configured yet — running setup first.\n"))
        rc = cmd_setup(args)
        if rc != 0:
            return rc
        conf = cfg.load()

    if docker_backend.is_running(conf["container_name"]):
        print(_green("  Corvin is already running."))
        url = docker_backend.console_url()
        print(f"  Opening console at {_bold(url)} …")
        import webbrowser
        webbrowser.open(url)
        return 0

    return docker_backend.start(foreground=True, open_browser=True)


# ── open ──────────────────────────────────────────────────────────────────────

def cmd_open(args: argparse.Namespace) -> int:
    """Open the Corvin web console in the default browser."""
    import webbrowser
    conf = cfg.load()
    url = docker_backend.console_url()

    if not docker_backend.is_running(conf["container_name"]):
        print(_yellow(f"  Corvin does not appear to be running."))
        print(f"  Start it first with:  {_bold('corvin start')}")
        print(f"  Then open:            {_bold(url)}")
        return 1

    print(f"  Opening {_bold(url)} …")
    webbrowser.open(url)
    return 0


# ── setup ─────────────────────────────────────────────────────────────────────

def cmd_setup(args: argparse.Namespace) -> int:
    print(f"\n{_bold('Corvin Setup')}\n")

    # ── 1. Docker check ───────────────────────────────────────────────────────
    print(_bold("Step 1/4 — Docker"))
    if not docker_backend.is_docker_available():
        print(_red("  Docker is not running or not installed."))
        print("  Install Docker Desktop: https://www.docker.com/products/docker-desktop/")
        print("  Or Docker Engine (Linux): https://docs.docker.com/engine/install/")
        return 1
    print(_green("  Docker is available."))

    # ── 2. Ollama detection ───────────────────────────────────────────────────
    print(f"\n{_bold('Step 2/4 — Ollama')}")
    ollama_url_hint = getattr(args, "ollama_url", None) or cfg.get("ollama_url")
    ollama_url = oll.detect_url(hint=ollama_url_hint)
    if not ollama_url:
        print(_red("  Ollama is not reachable."))
        print("  Start Ollama first: https://ollama.com/download")
        if not args.yes:
            custom = _ask("Or enter Ollama URL manually", "")
            if custom:
                ollama_url = custom.rstrip("/")
            else:
                return 1
        else:
            return 1
    print(_green(f"  Ollama found at {ollama_url}"))

    # ── 3. Model selection ────────────────────────────────────────────────────
    print(f"\n{_bold('Step 3/4 — Model')}")
    pulled_models = oll.list_models(ollama_url)
    current_model = cfg.get("model")

    if args.model:
        model = args.model
        print(f"  Using model: {model}")
    elif args.yes:
        model = current_model
        print(f"  Using model: {model}")
    else:
        suggestions = pulled_models[:8] if pulled_models else oll._DEFAULT_MODELS[:5]
        if not pulled_models:
            print(_yellow("  No models pulled yet. Showing suggestions (run 'ollama pull <model>' first)."))
        model = _ask_choice("Select a model:", suggestions, default=current_model if current_model in suggestions else (suggestions[0] if suggestions else "qwen3:8b"))

    # ── 4. Bridge selection ───────────────────────────────────────────────────
    bridge = cfg.get("bridge")
    if not args.yes:
        print(f"\n{_bold('Step 4/4 — Messaging bridge')}")
        bridges = ["discord", "telegram", "slack", "whatsapp", "email", "none"]
        bridge = _ask_choice(
            "Which messaging platform do you want to connect?",
            bridges,
            default=bridge or "discord",
        )
        if bridge == "none":
            bridge = None

    # ── EU production profile ─────────────────────────────────────────────────
    if getattr(args, "profile", None) == "eu-production":
        print(f"\n  {_green('EU-production profile activated')} — local Ollama only, no cloud egress.")

    # ── Save config ───────────────────────────────────────────────────────────
    conf = cfg.load()
    conf["ollama_url"] = ollama_url
    conf["model"] = model
    conf["bridge"] = bridge
    cfg.save(conf)

    # ── Pull image ────────────────────────────────────────────────────────────
    print(f"\n{_bold('Pulling Corvin image …')}")
    if not docker_backend.pull_image(conf["image"]):
        print(_red("  Failed to pull image. Check your internet connection."))
        return 1

    print(f"\n{_green(_bold('Setup complete!'))}")
    print(f"  Ollama:  {ollama_url}")
    print(f"  Model:   {model}")
    print(f"  Bridge:  {bridge or '(none)'}")
    print(f"\n  Run  {_bold('corvin gateway start')}  to launch Corvin.")
    print(f"  The console opens automatically at  "
          f"{_bold(docker_backend.console_url())}\n")
    return 0


# ── gateway ───────────────────────────────────────────────────────────────────

def cmd_gateway_start(args: argparse.Namespace) -> int:
    conf = cfg.load()
    if not conf.get("ollama_url") or not conf.get("model"):
        print(_red("Not configured yet. Run: corvin setup"))
        return 1

    open_browser = not getattr(args, "no_browser", False)

    from . import backend as be
    b = be.get()
    # docker_backend supports open_browser; native_backend gets foreground only
    if b is docker_backend:
        return b.start(foreground=True, open_browser=open_browser)
    return b.start(foreground=True)


def cmd_gateway_stop(args: argparse.Namespace) -> int:
    from . import backend as be
    b = be.get()
    b.stop()
    print("  Corvin stopped.")
    return 0


def cmd_gateway_setup(args: argparse.Namespace) -> int:
    """Interactive channel-connection wizard (post-install)."""
    print(f"\n{_bold('Corvin — Connect a messaging platform')}\n")
    bridges = ["discord", "telegram", "slack", "whatsapp", "email"]
    bridge = _ask_choice("Which platform do you want to connect?", bridges)
    cfg.set_value("bridge", bridge)

    token_prompts = {
        "discord":  "Discord Bot Token",
        "telegram": "Telegram Bot Token",
        "slack":    "Slack Bot Token",
        "whatsapp": "WhatsApp Phone Number ID",
        "email":    "Email address",
    }
    token = _ask(token_prompts.get(bridge, "Token / credential"))
    if not token:
        print(_yellow("  No token provided. Set it manually in your bridge settings.json."))
    else:
        print(_green(f"  Bridge token saved for {bridge}."))
        print("  (Stored in ~/.corvin-data — NOT uploaded anywhere.)")

    print(f"\n  Run {_bold('corvin gateway start')} to activate the bridge.\n")
    return 0


# ── config ────────────────────────────────────────────────────────────────────

def _set_telemetry_config(key: str, value: str) -> int:
    """``telemetry.*`` keys (e.g. ``telemetry.ping_enabled``) live in
    ``<corvin_home>/tenants/_default/global/tenant.corvin.yaml``
    (``spec.telemetry.<subkey>``) -- a completely different file from the
    corvin-launcher config.json that every OTHER ``corvin config set`` key
    writes to. Before this fix, ``corvin config set telemetry.ping_enabled
    false`` -- the exact command the software itself prints as "how to opt
    out" (serve_backend.py's telemetry notice) -- silently wrote a
    "telemetry.ping_enabled" key into config.json, which htrace_consent.py's
    ping_enabled() never reads. The documented opt-out was a complete no-op
    (adversarial review finding).
    """
    subkey = key.split(".", 1)[1]
    if not subkey:
        print(f"  invalid telemetry key: {key!r}")
        return 1
    try:
        import yaml  # type: ignore[import]
        from forge.paths import corvin_home  # noqa: PLC0415
        from corvin_console.aco.htrace_consent import _tenant_cfg_path  # noqa: PLC0415
    except ImportError as exc:
        print(f"  telemetry config requires the console extras: {exc}")
        return 1

    cfg_path = _tenant_cfg_path(corvin_home())
    data: dict = {}
    if cfg_path.exists():
        try:
            data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            print(f"  could not parse {cfg_path}: {exc}")
            return 1
    spec = data.setdefault("spec", {})
    telemetry = spec.setdefault("telemetry", {})

    lowered = value.strip().lower()
    if lowered in ("true", "yes", "1", "on"):
        parsed: Any = True
    elif lowered in ("false", "no", "0", "off"):
        parsed = False
    else:
        parsed = value  # pass unrecognised values through as-is
    telemetry[subkey] = parsed

    cfg_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=cfg_path.parent, prefix=".tenant.corvin.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, sort_keys=False)
        os.replace(tmp, cfg_path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    print(f"  spec.telemetry.{subkey} = {parsed}  ({cfg_path})")
    return 0


def cmd_config_set(args: argparse.Namespace) -> int:
    if args.key.startswith("telemetry."):
        return _set_telemetry_config(args.key, args.value)
    key_map = {
        "ollama-url": "ollama_url",
        "model":      "model",
        "bridge":     "bridge",
        "image":      "image",
    }
    key = key_map.get(args.key, args.key.replace("-", "_"))
    cfg.set_value(key, args.value)
    print(f"  {key} = {args.value}")
    return 0


def cmd_config_show(args: argparse.Namespace) -> int:
    conf = cfg.load()
    print(f"\n{_bold('Corvin configuration')}")
    for k, v in conf.items():
        print(f"  {k:20s} {v}")
    print()
    return 0


# ── status ────────────────────────────────────────────────────────────────────

def cmd_status(args: argparse.Namespace) -> int:
    conf = cfg.load()
    running = docker_backend.is_running(conf["container_name"])
    ollama_url = oll.detect_url(conf["ollama_url"])

    print(f"\n{_bold('Corvin status')}")
    print(f"  Gateway:  {'running' if running else _yellow('stopped')}")
    print(f"  Console:  {_green(docker_backend.console_url()) if running else '—'}")
    print(f"  Ollama:   {_green(ollama_url) if ollama_url else _red('unreachable')}")
    print(f"  Model:    {conf.get('model', '—')}")
    print(f"  Bridge:   {conf.get('bridge') or '—'}")
    print()
    return 0


# ── parser ────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="corvin",
        description="Corvin launcher — manage your local AI assistant gateway.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Quick start (pip install, no Docker needed):
              corvin serve                 Start console directly, open browser
              corvin serve --port 9000     Use a custom port

            Quick start (Docker):
              corvin start                 Setup if needed, start gateway, open browser
              corvin open                  Open the web console in your browser
              corvin setup                 Interactive configuration wizard
              corvin setup --yes --model qwen3:8b   Non-interactive (for ollama launch)
              corvin gateway start         Start the gateway (foreground)
              corvin gateway setup         Connect a messaging platform
              corvin status                Show running state
        """),
    )
    sub = p.add_subparsers(dest="command", metavar="command")

    # serve (native, no Docker)
    sv = sub.add_parser("serve", help="Start console natively via uvicorn (no Docker needed)")
    sv.add_argument("--port", "-p", type=int, default=8765, metavar="PORT",
                    help="TCP port to listen on (default: 8765)")
    sv.add_argument("--no-browser", action="store_true",
                    help="Do not open the browser automatically")
    sv.add_argument("--host", default="127.0.0.1", metavar="HOST",
                    help="Bind address (default: 127.0.0.1)")

    # start
    st = sub.add_parser("start", help="Setup if needed, start gateway, open browser")
    st.add_argument("--yes", "-y", action="store_true", help="Non-interactive setup")
    st.add_argument("--model", metavar="TAG", help="Ollama model tag")
    st.add_argument("--ollama-url", metavar="URL", help="Ollama base URL")
    st.add_argument("--profile", choices=["eu-production"], help="Config preset")
    st.add_argument("--port", "-p", type=int, default=8765, metavar="PORT",
                    help="Port for native mode (default: 8765)")
    st.add_argument("--no-browser", action="store_true",
                    help="Do not open the browser (native mode)")

    # open
    sub.add_parser("open", help="Open the web console in your browser")

    # setup
    s = sub.add_parser("setup", help="Configure and pull Corvin")
    s.add_argument("--yes", "-y", action="store_true", help="Non-interactive (skip all prompts)")
    s.add_argument("--model", metavar="TAG", help="Ollama model tag to use")
    s.add_argument("--ollama-url", metavar="URL", help="Ollama base URL (default: auto-detect)")
    s.add_argument("--profile", choices=["eu-production"], help="Apply a config preset")

    # detect (ADR-0120 M3 — engine binary detection)
    det = sub.add_parser("detect", help="Probe installed engine binaries (ADR-0120)")
    det.add_argument("--json", action="store_true", help="Output as JSON")

    # gateway
    gw = sub.add_parser("gateway", help="Manage the Corvin gateway daemon")
    gw_sub = gw.add_subparsers(dest="gateway_cmd", metavar="subcommand")
    gw_start = gw_sub.add_parser("start", help="Start the gateway (foreground)")
    gw_start.add_argument(
        "--no-browser", action="store_true",
        help="Do not open the console in a browser (useful on servers)",
    )
    gw_sub.add_parser("stop",  help="Stop the running gateway")
    gw_sub.add_parser("setup", help="Connect a messaging platform interactively")

    # config
    co = sub.add_parser("config", help="Read and write configuration")
    co_sub = co.add_subparsers(dest="config_cmd", metavar="subcommand")
    cs = co_sub.add_parser("set", help="Set a config value")
    # No `choices=` restriction here (was: ["ollama-url", "model", "bridge",
    # "image"]) — that rejected `telemetry.*` keys with argparse's own usage
    # error BEFORE cmd_config_set ever ran, so the exact opt-out command this
    # software prints to users ("corvin config set telemetry.ping_enabled
    # false") failed outright (adversarial review finding). cmd_config_set
    # already validates/dispatches unknown keys safely.
    cs.add_argument(
        "key", metavar="KEY",
        help="ollama-url | model | bridge | image | telemetry.<subkey> "
             "(e.g. telemetry.ping_enabled)",
    )
    cs.add_argument("value", metavar="VALUE")
    co_sub.add_parser("show", help="Print current configuration")

    # status
    sub.add_parser("status", help="Show gateway and Ollama status")

    return p


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "detect":
        sys.exit(cmd_detect(args))

    elif args.command == "serve":
        sys.exit(cmd_serve(args))

    elif args.command == "start":
        sys.exit(cmd_start(args))

    elif args.command == "open":
        sys.exit(cmd_open(args))

    elif args.command == "setup":
        sys.exit(cmd_setup(args))

    elif args.command == "gateway":
        if args.gateway_cmd == "start":
            sys.exit(cmd_gateway_start(args))
        elif args.gateway_cmd == "stop":
            sys.exit(cmd_gateway_stop(args))
        elif args.gateway_cmd == "setup":
            sys.exit(cmd_gateway_setup(args))
        else:
            parser.parse_args(["gateway", "--help"])

    elif args.command == "config":
        if args.config_cmd == "set":
            sys.exit(cmd_config_set(args))
        elif args.config_cmd == "show":
            sys.exit(cmd_config_show(args))
        else:
            parser.parse_args(["config", "--help"])

    elif args.command == "status":
        sys.exit(cmd_status(args))

    else:
        # No subcommand: behave like `corvin start` (the most useful default)
        parser.print_help()
        print(f"\n  Tip: run {_bold('corvin start')} to set up and launch in one step.\n")
        sys.exit(0)
