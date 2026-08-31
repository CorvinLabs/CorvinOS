#!/usr/bin/env python3
"""End-to-End-Test: voice-Adapter resolved chat_profile.persona via cowork.

Setzt Sandbox auf, writes settings.json mit `persona: browser`, schickt
inbox message, checks dass die fertigen claude-CLI-Args MCP-Config + add-dir +
allowed-tools aus der Persona enthalten — without dass voice die Persona-Felder
selber kennen muss.

Run: python3 operator/bridges/shared/test_adapter_cowork.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ADAPTER = ROOT / "adapter.py"
SANDBOX = Path(os.environ.get("ADAPTER_TEST_SANDBOX", "/tmp/adapter-cowork-sandbox"))
INBOX = SANDBOX / "inbox"
OUTBOX = SANDBOX / "outbox"
PROCESSED = SANDBOX / "processed"
LOG_FILE = SANDBOX / "adapter.log"
ARGS_DUMP = SANDBOX / "args.jsonl"

TEST_CHANNEL = "coworktest"
TEST_CHANNEL_DIR = ROOT.parent / TEST_CHANNEL


def setup_sandbox(profiles: dict) -> None:
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)
    for d in (INBOX, OUTBOX, PROCESSED):
        d.mkdir(parents=True, exist_ok=True)
    TEST_CHANNEL_DIR.mkdir(exist_ok=True)
    (TEST_CHANNEL_DIR / "settings.json").write_text(
        json.dumps({"chat_profiles": profiles}, indent=2)
    )


def teardown_sandbox() -> None:
    if TEST_CHANNEL_DIR.exists():
        shutil.rmtree(TEST_CHANNEL_DIR)


def write_inbox(item_id: str, chat_key: str, text: str) -> None:
    # Re-mkdir defensiv: zwischen setup_sandbox und Adapter-Boot poll'd der
    # Adapter mit 0.1s — da kann es Race-Conditions geben falls FS gerade
    # rmtree-overlebt. Idempotent.
    INBOX.mkdir(parents=True, exist_ok=True)
    payload = {"id": item_id, "channel": TEST_CHANNEL,
               "chat_id": chat_key, "from": chat_key, "text": text}
    (INBOX / f"{item_id}.json").write_text(json.dumps(payload))


def wait_for_processed(n: int, timeout: float = 10.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        c = len(list(PROCESSED.glob("*.json")))
        if c >= n:
            return c
        time.sleep(0.05)
    return len(list(PROCESSED.glob("*.json")))


def load_args_dump() -> list[dict]:
    if not ARGS_DUMP.exists():
        return []
    return [json.loads(line) for line in ARGS_DUMP.read_text().splitlines() if line.strip()]


def find_call(dump: list[dict], chat_key: str) -> dict | None:
    for entry in dump:
        if entry.get("chat_key") == chat_key:
            return entry
    return None


def system_prompt_from_args(args: list) -> str:
    """Return the merged system prompt from a dumped claude argv.

    Understands BOTH spawn shapes: the historical inline
    ``--append-system-prompt <text>`` and the post-e4dec5c
    ``--append-system-prompt-file <path>`` (Windows command-line-length
    fix), where the text lives in a temp file inside the session dir.
    In ADAPTER_FAKE_CLAUDE dump mode the adapter deliberately leaks that
    temp file (see _build_claude_args docstring), so reading it here is
    safe as long as the sandbox is still alive.
    """
    if "--append-system-prompt" in args:
        return args[args.index("--append-system-prompt") + 1]
    if "--append-system-prompt-file" in args:
        path = Path(args[args.index("--append-system-prompt-file") + 1])
        if not path.exists():
            raise AssertionError(
                f"--append-system-prompt-file points at a missing file: {path}"
            )
        return path.read_text(encoding="utf-8")
    raise AssertionError(f"no --append-system-prompt[-file] in argv: {args}")


def run_adapter_once(env: dict) -> None:
    proc = subprocess.Popen(
        ["python3", str(ADAPTER)],
        env=env, stdout=open(LOG_FILE, "a"), stderr=subprocess.STDOUT,
    )
    try:
        time.sleep(0.4)
    except Exception:
        pass
    return proc


def main() -> int:
    failures: list[str] = []

    # Use a sandbox MCP cache so the test never touches the real cache dir.
    mcp_cache = SANDBOX / "mcp-cache"
    cowork_user = SANDBOX / "cowork-user"

    # ── Case A: chat_profile.persona = "research" (browser removed f1e3246) ─
    setup_sandbox({
        "+49170...@s.whatsapp.net": {"persona": "research"},
    })
    env = os.environ.copy()
    env["ADAPTER_INBOX"] = str(INBOX)
    env["ADAPTER_OUTBOX"] = str(OUTBOX)
    env["ADAPTER_PROCESSED"] = str(PROCESSED)
    env["ADAPTER_FAKE_CLAUDE"] = "1"
    env["ADAPTER_FAKE_DELAY"] = "0.05"
    env["ADAPTER_FAKE_ARGS_DUMP"] = str(ARGS_DUMP)
    env["ADAPTER_POLL_INTERVAL"] = "0.1"
    env["ADAPTER_DISABLE_VOICE"] = "1"
    env["BRIDGE_PROGRESS_UPDATES"] = "0"
    env["COWORK_USER_DIR"] = str(cowork_user)
    env["COWORK_MCP_CACHE"] = str(mcp_cache)

    proc = run_adapter_once(env)
    try:
        write_inbox("01_browser", "+49170...@s.whatsapp.net", "such was")
        wait_for_processed(1, timeout=10.0)
        time.sleep(0.2)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    dump = load_args_dump()
    print(f"[case A] dumped {len(dump)} fake claude-call(s)")
    try:
        e = find_call(dump, "+49170...@s.whatsapp.net")
        if e is None:
            raise AssertionError("no dump entry for research-persona chat")
        args = e["args"]
        # Aus der research-Persona muss kommen:
        #   --dangerously-skip-permissions     (bypassPermissions)
        #   --mcp-config <path>                (playwright im persona)
        #   --add-dir ~/cowork/research
        # research hat allowed_tools=[] + bypassPermissions → kein --allowedTools für
        # mcp__playwright__* nötig; bypassPermissions öffnet alles.
        if "--dangerously-skip-permissions" not in args:
            raise AssertionError(f"missing bypassPermissions flag: {args}")
        if "--mcp-config" not in args:
            raise AssertionError(f"missing --mcp-config: {args}")
        mcp_path = args[args.index("--mcp-config") + 1]
        if not Path(mcp_path).is_file():
            raise AssertionError(f"--mcp-config points to non-existent file {mcp_path}")
        mcp_doc = json.loads(Path(mcp_path).read_text())
        if "playwright" not in mcp_doc.get("mcpServers", {}):
            raise AssertionError(f"playwright server missing in {mcp_doc}")
        # add_dirs aus persona → mindestens ein --add-dir, das auf cowork/research zeigt
        add_dir_count = args.count("--add-dir")
        if add_dir_count < 1:
            raise AssertionError(f"no --add-dir: {args}")
        add_dir_values = [args[i + 1] for i, a in enumerate(args) if a == "--add-dir"]
        if not any("cowork/research" in v or "cowork\\research" in v for v in add_dir_values):
            raise AssertionError(f"persona add_dirs nicht propagiert: {add_dir_values}")
        print("PASS: research-persona → permission-mode + MCP-config + add-dir")
    except AssertionError as e:
        failures.append(f"case-A: {e}")
        print(f"FAIL: case-A: {e}")

    # ── Case B: chat_profile MIT persona + chat-spezifischen overrides ───
    # chat_profile.allowed_tools sollte mit persona.allowed_tools mergen.
    setup_sandbox({
        "team-chat-id": {
            "persona": "research",
            "allowed_tools": ["mcp__shared__listen"],   # extra
            "append_system": "replye especially knapp.",
        },
    })
    ARGS_DUMP.unlink(missing_ok=True)
    for d in (INBOX, OUTBOX, PROCESSED):
        for f in d.glob("*.json"):
            f.unlink()

    proc = run_adapter_once(env)
    try:
        write_inbox("02_team", "team-chat-id", "merge-test")
        wait_for_processed(1, timeout=10.0)
        time.sleep(0.2)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    dump = load_args_dump()
    print(f"[case B] dumped {len(dump)} fake claude-call(s)")
    try:
        e = find_call(dump, "team-chat-id")
        if e is None:
            raise AssertionError("no dump entry for merged-profile chat")
        args = e["args"]
        allowed_idx = args.index("--allowedTools")
        allowed = args[allowed_idx + 1]
        # Persona-Rework v0.9: browser-Persona's allowed_tools ist leer
        # (bypassPermissions öffnet alles). Override muss aber im merge erscheinen.
        if "mcp__shared__listen" not in allowed:
            raise AssertionError(f"chat-override fehlte im merge — allowed={allowed}")
        sys_prompt = system_prompt_from_args(args)
        if "especially knapp" not in sys_prompt:
            raise AssertionError("chat-spezifischer append_system nicht im prompt")
        # research persona contains "research agent" / "web research" in append_system
        if ("research agent" not in sys_prompt.lower()
                and "web research" not in sys_prompt.lower()
                and "websearch" not in sys_prompt.lower()):
            raise AssertionError("persona-prompt nicht im final prompt")
        print("PASS: persona + chat-overrides → tools mergen, append_system konkateniert")
    except AssertionError as e:
        failures.append(f"case-B: {e}")
        print(f"FAIL: case-B: {e}")

    # ── Case C: persona-key OHNE installiertes cowork → graceful fallback
    # We simulieren das, indem we einen unbekannten Persona-Namen set.
    # cowork.resolve gibt dann overrides unchanged back.
    setup_sandbox({
        "fallback-chat": {
            "persona": "this-persona-does-not-exist",
            "permission_mode": "plan",
            "allowed_tools": ["Read"],
        },
    })
    ARGS_DUMP.unlink(missing_ok=True)
    for d in (INBOX, OUTBOX, PROCESSED):
        for f in d.glob("*.json"):
            f.unlink()

    proc = run_adapter_once(env)
    try:
        write_inbox("03_fallback", "fallback-chat", "fallback-test")
        wait_for_processed(1, timeout=10.0)
        time.sleep(0.2)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    dump = load_args_dump()
    print(f"[case C] dumped {len(dump)} fake claude-call(s)")
    try:
        e = find_call(dump, "fallback-chat")
        if e is None:
            raise AssertionError("no dump entry for fallback chat")
        args = e["args"]
        if "--permission-mode" not in args or args[args.index("--permission-mode") + 1] != "plan":
            raise AssertionError(f"profile-permission_mode nicht durchgereicht: {args}")
        allowed = args[args.index("--allowedTools") + 1]
        if allowed != "Read":
            raise AssertionError(f"profile-allowed_tools nicht durchgereicht: {allowed}")
        if "--mcp-config" in args:
            raise AssertionError("unbekannte persona sollte keine MCP-Config liefern")
        print("PASS: unbekannte persona → profile-Felder werden durchgereicht (graceful)")
    except AssertionError as e:
        failures.append(f"case-C: {e}")
        print(f"FAIL: case-C: {e}")

    # ── Case D: Auto-Routing — KEINE persona im profile, aber router an
    # Settings haben routing.mode=auto per Default. Mit ROUTER_FAKE set
    # we die Router-reply statt Haiku zu callen.
    setup_sandbox({})  # leere chat_profiles → Router darf entscheiden
    ARGS_DUMP.unlink(missing_ok=True)
    for d in (INBOX, OUTBOX, PROCESSED):
        for f in d.glob("*.json"):
            f.unlink()

    env_router = env.copy()
    env_router["ROUTER_FAKE"] = "1"
    env_router["ROUTER_FAKE_RESULT"] = (
        '{"persona":"browser","confidence":0.95,"why":"explicit web task"}'
    )
    proc = run_adapter_once(env_router)
    try:
        write_inbox("04_auto", "auto-route-chat", "open example.com und screenshot")
        wait_for_processed(1, timeout=10.0)
        time.sleep(0.2)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    dump = load_args_dump()
    print(f"[case D] dumped {len(dump)} fake claude-call(s)")
    try:
        e = find_call(dump, "auto-route-chat")
        if e is None:
            raise AssertionError("no dump entry for auto-routed chat")
        args = e["args"]
        # Erwartung: Router pickte browser → MCP-Config + browser-Tools.
        if "--mcp-config" not in args:
            raise AssertionError(f"auto-routing → kein --mcp-config: {args}")
        mcp_path = args[args.index("--mcp-config") + 1]
        mcp_doc = json.loads(Path(mcp_path).read_text())
        if "playwright" not in mcp_doc.get("mcpServers", {}):
            raise AssertionError(f"router → browser, aber kein playwright-MCP: {mcp_doc}")
        sys_prompt = system_prompt_from_args(args)
        # router may pick research (now carries Playwright) instead of browser
        if ("research agent" not in sys_prompt.lower()
                and "browser automation agent" not in sys_prompt.lower()
                and "web research" not in sys_prompt.lower()):
            raise AssertionError("auto-routing: web-persona prompt fehlt")
        print("PASS: auto-routing greift bei leerem profile + Router-Pick")
    except AssertionError as e:
        failures.append(f"case-D: {e}")
        print(f"FAIL: case-D: {e}")

    # ── Case E: Router returns nichts (ROUTER_FAKE leer) → Fallback assistant
    setup_sandbox({})
    ARGS_DUMP.unlink(missing_ok=True)
    for d in (INBOX, OUTBOX, PROCESSED):
        for f in d.glob("*.json"):
            f.unlink()

    env_fallback = env.copy()
    env_fallback["ROUTER_FAKE"] = "1"
    env_fallback["ROUTER_FAKE_RESULT"] = ""  # → router gibt None
    proc = run_adapter_once(env_fallback)
    try:
        write_inbox("05_fallback", "ambiguous-chat", "hi")
        wait_for_processed(1, timeout=10.0)
        time.sleep(0.2)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    dump = load_args_dump()
    print(f"[case E] dumped {len(dump)} fake claude-call(s)")
    try:
        e = find_call(dump, "ambiguous-chat")
        if e is None:
            raise AssertionError("no dump entry for fallback chat")
        args = e["args"]
        # Allrounder = bypassPermissions, so --dangerously-skip-permissions
        # UND playwright-MCP UND Allrounder-System-Prompt.
        if "--dangerously-skip-permissions" not in args:
            raise AssertionError(f"fallback erwartet bypass: {args}")
        if "--mcp-config" not in args:
            raise AssertionError(f"fallback erwartet MCP (assistant hat playwright): {args}")
        sys_prompt = system_prompt_from_args(args)
        # Match either the original German Allrounder marker or its
        # English translation — the persona has been migrated DE → EN.
        sp_low = sys_prompt.lower()
        if ("toole" not in sys_prompt and "viele" not in sp_low
                and "all tools" not in sp_low and "generalist" not in sp_low):
            raise AssertionError(f"fallback erwartet assistant-Prompt: {sys_prompt[:200]}")
        print("PASS: Router unsicher → Fallback assistant greift")
    except AssertionError as e:
        failures.append(f"case-E: {e}")
        print(f"FAIL: case-E: {e}")

    # ── Case F: routing.mode = "off" → kein Auto-Routing, legacy max-open
    # We usen ADAPTER_ROUTING_MODE=off statt die LIVE shared/settings.json
    # zu mutaten — bei Test-Crash bliebe otherwise die Live-Konfig kaputt.
    setup_sandbox({})
    # In-Block try/finally bleibt intentionally — sympathy for die alte Struktur.
    try:
        ARGS_DUMP.unlink(missing_ok=True)
        for d in (INBOX, OUTBOX, PROCESSED):
            for f in d.glob("*.json"):
                f.unlink()

        env_off = env.copy()
        env_off["ROUTER_FAKE"] = "1"
        env_off["ROUTER_FAKE_RESULT"] = (
            '{"persona":"browser","confidence":0.99,"why":"x"}'
        )
        env_off["ADAPTER_ROUTING_MODE"] = "off"
        proc = run_adapter_once(env_off)
        try:
            write_inbox("06_off", "no-routing-chat", "open example.com")
            wait_for_processed(1, timeout=10.0)
            time.sleep(0.2)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

        dump = load_args_dump()
        try:
            e = find_call(dump, "no-routing-chat")
            if e is None:
                raise AssertionError("no dump entry")
            args = e["args"]
            if "--dangerously-skip-permissions" not in args:
                raise AssertionError(f"mode=off erwartet legacy bypass: {args}")
            if "--mcp-config" in args:
                raise AssertionError(f"mode=off sollte keine MCP-Config laden: {args}")
            print("PASS: routing.mode=off → kein Auto-Routing, legacy bypass")
        except AssertionError as e:
            failures.append(f"case-F: {e}")
            print(f"FAIL: case-F: {e}")
    finally:
        pass  # nichts mehr backzuset — we mutaten keine Live-Settings.

    teardown_sandbox()
    shutil.rmtree(SANDBOX, ignore_errors=True)

    if failures:
        print(f"\n{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nALL CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
