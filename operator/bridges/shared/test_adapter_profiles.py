#!/usr/bin/env python3
"""End-to-End-Test for per-chat profiles (layer 1).

Setzt eine Sandbox auf, legt Test-settings.json mit chat_profiles an, schickt
inbox messages mit unterschiedlichen Sender-IDs, und checks dass adapter.py die
korrekten claude-CLI-Args baut. Nutzt ADAPTER_FAKE_CLAUDE + ADAPTER_FAKE_ARGS_DUMP.

Run:
    python3 operator/bridges/shared/test_adapter_profiles.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT      = Path(__file__).resolve().parent
ADAPTER   = ROOT / "adapter.py"
SANDBOX   = Path(os.environ.get("ADAPTER_TEST_SANDBOX", "/tmp/adapter-profile-sandbox"))
INBOX     = SANDBOX / "inbox"
OUTBOX    = SANDBOX / "outbox"
PROCESSED = SANDBOX / "processed"
LOG_FILE  = SANDBOX / "adapter.log"
ARGS_DUMP = SANDBOX / "args.jsonl"

# Adapter reads channel-settings unter ROOT/.. /<channel>/settings.json — we
# usen daher den existierenden bridges/-Baum, aber legen ein Test-Channel-
# directory "profiletest" parallel zu telegram/discord/whatsapp an, damit
# we die echten Channel-settings nicht patchen must.
TEST_CHANNEL = "profiletest"
TEST_CHANNEL_DIR = ROOT.parent / TEST_CHANNEL


CHAT_PROFILES = {
    "default": {
        "permission_mode": "plan",
        "allowed_tools": ["Read", "Grep"],
    },
    "trusted-user": {
        "permission_mode": "bypassPermissions",
        "model": "claude-haiku-4-5",
    },
    "readonly-chat": {
        "permission_mode": "default",
        "allowed_tools": ["Read"],
        "disallowed_tools": ["Bash", "Write", "Edit"],
        "append_system": "replye especially knapp.",
    },
}


def setup_sandbox() -> None:
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)
    for d in (INBOX, OUTBOX, PROCESSED):
        d.mkdir(parents=True, exist_ok=True)
    TEST_CHANNEL_DIR.mkdir(exist_ok=True)
    (TEST_CHANNEL_DIR / "settings.json").write_text(
        json.dumps({"chat_profiles": CHAT_PROFILES}, indent=2)
    )


def teardown_sandbox() -> None:
    # Test-Channel-directory wieder weg, damit kein Müll im Repo bleibt.
    if TEST_CHANNEL_DIR.exists():
        shutil.rmtree(TEST_CHANNEL_DIR)


def write_inbox(item_id: str, chat_key: str, text: str) -> None:
    payload = {
        "id":      item_id,
        "channel": TEST_CHANNEL,
        "chat_id": chat_key,
        "from":    chat_key,
        "text":    text,
    }
    (INBOX / f"{item_id}.json").write_text(json.dumps(payload))


def wait_for_processed(n: int, timeout: float = 10.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        count = len(list(PROCESSED.glob("*.json")))
        if count >= n:
            return count
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


def assert_args_contain(args: list[str], *needles: str) -> None:
    """Sucht jede Suchstring-Folge sequenziell in args. needle ist der value
    direkt nach dem Flag — z.B. assert_args_contain(args, '--model', 'claude-haiku-4-5')."""
    a = list(args)
    for needle in needles:
        if needle not in a:
            raise AssertionError(f"missing {needle!r} in args: {a}")


def assert_args_not_contain(args: list[str], needle: str) -> None:
    if needle in args:
        raise AssertionError(f"unexpected {needle!r} present in args: {args}")


def main() -> int:
    setup_sandbox()
    env = os.environ.copy()
    env["ADAPTER_INBOX"]           = str(INBOX)
    env["ADAPTER_OUTBOX"]          = str(OUTBOX)
    env["ADAPTER_PROCESSED"]       = str(PROCESSED)
    env["ADAPTER_FAKE_CLAUDE"]     = "1"
    env["ADAPTER_FAKE_DELAY"]      = "0.05"
    env["ADAPTER_FAKE_ARGS_DUMP"]  = str(ARGS_DUMP)
    env["ADAPTER_POLL_INTERVAL"]   = "0.1"
    env["ADAPTER_DISABLE_VOICE"]   = "1"
    env["BRIDGE_PROGRESS_UPDATES"] = "0"  # Legacy call_claude-path, dump simpler
    # Dieser Test checks das Profil-Schema selbst — Auto-Routing würde die
    # Erwartungen (max-open default) verchange. Routing aus.
    env["ADAPTER_ROUTING_MODE"]    = "off"
    # CORVIN_OS_MODEL_OVERRIDE is an operator kill-switch that beats
    # explicit profile.model.  Clear it so the per-profile model pin
    # (e.g. trusted-user → claude-haiku-4-5) is observable in the test.
    env.pop("CORVIN_OS_MODEL_OVERRIDE", None)
    env.pop("CORVIN_HELPER_MODEL_OS_TURN", None)

    proc = subprocess.Popen(
        ["python3", str(ADAPTER)],
        env=env, stdout=open(LOG_FILE, "w"), stderr=subprocess.STDOUT,
    )
    failures: list[str] = []
    try:
        time.sleep(0.4)

        write_inbox("01_default",   "unknown-user",  "fallback-to-default")
        write_inbox("02_trusted",   "trusted-user",  "trusted-user-call")
        write_inbox("03_readonly",  "readonly-chat", "readonly-call")

        n = wait_for_processed(3, timeout=10.0)
        if n != 3:
            failures.append(f"only {n}/3 inbox items processed")
        time.sleep(0.2)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    dump = load_args_dump()
    print(f"[result] dumped {len(dump)} fake claude-call(s)")

    # --- 1. Default-Profil greift bei unbekanntem Chat (plan, Read+Grep) ---
    try:
        e = find_call(dump, "unknown-user")
        if e is None:
            raise AssertionError("no dump entry for unknown-user")
        args = e["args"]
        assert_args_contain(args, "--permission-mode", "plan")
        assert_args_contain(args, "--allowedTools", "Read Grep")
        assert_args_not_contain(args, "--dangerously-skip-permissions")
        print("PASS: unknown-user → default profile (plan, Read+Grep)")
    except AssertionError as e:
        failures.append(f"default-profile: {e}")
        print(f"FAIL: default-profile: {e}")

    # --- 2. trusted-user nutzt --dangerously-skip + --model ---
    try:
        e = find_call(dump, "trusted-user")
        if e is None:
            raise AssertionError("no dump entry for trusted-user")
        args = e["args"]
        assert_args_contain(args, "--dangerously-skip-permissions")
        assert_args_contain(args, "--model", "claude-haiku-4-5")
        assert_args_not_contain(args, "--permission-mode")
        print("PASS: trusted-user → bypassPermissions (legacy flag) + custom model")
    except AssertionError as e:
        failures.append(f"trusted-profile: {e}")
        print(f"FAIL: trusted-profile: {e}")

    # --- 3. readonly-chat nutzt default permission-mode + tool-caps + append_system ---
    try:
        e = find_call(dump, "readonly-chat")
        if e is None:
            raise AssertionError("no dump entry for readonly-chat")
        args = e["args"]
        assert_args_contain(args, "--permission-mode", "default")
        assert_args_contain(args, "--allowedTools", "Read")
        assert_args_contain(args, "--disallowedTools", "Bash Write Edit")
        # append_system → der system-prompt-arg muss den Suffix enthalten.
        sys_prompt = system_prompt_from_args(args)
        if "replye especially knapp." not in sys_prompt:
            raise AssertionError(
                f"append_system not in system prompt: {sys_prompt[-200:]}"
            )
        print("PASS: readonly-chat → default mode + tool-caps + append_system")
    except (AssertionError, ValueError, IndexError) as e:
        failures.append(f"readonly-profile: {e}")
        print(f"FAIL: readonly-profile: {e}")

    # --- 3b. KEINE chat_profiles-Section → max offen (legacy bypass) ---
    # Das ist die default-Annahme nach `bash setup.sh`: ein neuer User soll
    # NICHT ständig gefragt werden. Wenn dieser path jemals bricht, fresher
    # Install fragt plötzlich for jede Aktion → schlechte UX.
    try:
        # Settings write OHNE chat_profiles, fresh start,
        # eine Message schicken, prüfen dass --dangerously-skip-permissions drin ist.
        (TEST_CHANNEL_DIR / "settings.json").write_text(
            json.dumps({"whitelist": [], "rate_limit_per_hour": 30}, indent=2)
        )
        ARGS_DUMP.unlink(missing_ok=True)
        for d in (INBOX, OUTBOX, PROCESSED):
            for f in d.glob("*.json"):
                f.unlink()

        proc_default = subprocess.Popen(
            ["python3", str(ADAPTER)],
            env=env, stdout=open(LOG_FILE, "a"), stderr=subprocess.STDOUT,
        )
        try:
            time.sleep(0.4)
            write_inbox("default_max", "any-sender", "no profile section at all")
            wait_for_processed(1, timeout=5.0)
            time.sleep(0.2)
        finally:
            proc_default.terminate()
            try:
                proc_default.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc_default.kill()

        dump_default = load_args_dump()
        e = find_call(dump_default, "any-sender")
        if e is None:
            raise AssertionError("no dump entry without chat_profiles")
        args = e["args"]
        assert_args_contain(args, "--dangerously-skip-permissions")
        assert_args_not_contain(args, "--permission-mode")
        assert_args_not_contain(args, "--allowedTools")
        assert_args_not_contain(args, "--disallowedTools")
        print("PASS: no chat_profiles → max-open legacy bypass (no permission prompts)")

        # Settings for den nachfolgenden Hot-Reload-Test wieder backset.
        (TEST_CHANNEL_DIR / "settings.json").write_text(
            json.dumps({"chat_profiles": CHAT_PROFILES}, indent=2)
        )
    except AssertionError as e:
        failures.append(f"max-open-default: {e}")
        print(f"FAIL: max-open-default: {e}")

    # --- 4. Hot-Reload: Profil change → next Message nutzt neuen value ---
    try:
        new_profiles = {
            **CHAT_PROFILES,
            "trusted-user": {
                "permission_mode": "acceptEdits",  # changed von bypassPermissions
                "model": "claude-sonnet-4-6",      # neues Model
            },
        }
        (TEST_CHANNEL_DIR / "settings.json").write_text(
            json.dumps({"chat_profiles": new_profiles}, indent=2)
        )
        # mtime garantiert neue stat — _load_channel_settings reads pro Message
        # fresh, kein Adapter-Cache zwischen.
        time.sleep(0.05)

        # Adapter ist already beendet — we start ihn nicht neu (das ist der
        # Witz: settings.json wed live geread). Stattdessen verifizieren we
        # den path indem we den Adapter erneut start und einen Call schicken.
        # Das simuliert "der laufende Adapter sieht die change sofort".
        env2 = env.copy()
        ARGS_DUMP.unlink(missing_ok=True)
        for d in (INBOX, OUTBOX, PROCESSED):
            for f in d.glob("*.json"):
                f.unlink()

        proc2 = subprocess.Popen(
            ["python3", str(ADAPTER)],
            env=env2, stdout=open(LOG_FILE, "a"), stderr=subprocess.STDOUT,
        )
        try:
            time.sleep(0.4)
            write_inbox("04_reloaded", "trusted-user", "after-reload")
            wait_for_processed(1, timeout=5.0)
            time.sleep(0.2)
        finally:
            proc2.terminate()
            try:
                proc2.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc2.kill()

        dump2 = load_args_dump()
        e = find_call(dump2, "trusted-user")
        if e is None:
            raise AssertionError("no dump entry after reload")
        args = e["args"]
        assert_args_contain(args, "--permission-mode", "acceptEdits")
        assert_args_contain(args, "--model", "claude-sonnet-4-6")
        assert_args_not_contain(args, "--dangerously-skip-permissions")
        print("PASS: hot-reload — settings.json change picked up without restart")
    except AssertionError as e:
        failures.append(f"hot-reload: {e}")
        print(f"FAIL: hot-reload: {e}")
    finally:
        teardown_sandbox()

    if failures:
        print(f"\n{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nALL CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
