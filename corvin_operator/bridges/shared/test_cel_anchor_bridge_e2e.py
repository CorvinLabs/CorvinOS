"""E2E: the bridge gives CEL a per-chat session identity (anchor store).

Before 2026-10-02 the adapter called the CEL with ``session=None``; with the
``cel_load_bearing_anchor`` flag on (it is on, via the console overlay, on the
maintainer install) every chat of the tenant wrote to and read from ONE
``_nosession`` anchor bucket — one chat's goal and decisions were re-injected
into every other chat.

Drives the real adapter process (deterministic CEL, ``vibe_engineering`` +
``cel_load_bearing_anchor`` on in the sandbox overlay) and the real
``session_reset.py`` CLI, then asserts on the anchor store on disk:
  * two chats → two separate stores, no ``_nosession`` bucket;
  * an explicit ``/new`` starts a new store (``#<epoch>``), an inactivity
    reset does not (its facts are exactly what must survive).

Run: python3 corvin_operator/bridges/shared/test_cel_anchor_bridge_e2e.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_session_ledger_e2e as h  # noqa: E402  (shared sandbox harness)


def _send(sb: Path, chat: str, text: str, n: int) -> None:
    inbox = sb / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    item = {"id": f"a{n}", "channel": h.CHANNEL, "chat_id": chat, "from": chat, "text": text}
    (inbox / f"a{n}.json").write_text(json.dumps(item))
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if len(list((sb / "processed").glob("*.json"))) >= n:
            return
        time.sleep(0.05)
    raise AssertionError(f"turn {n} not processed; see {sb / 'adapter.log'}")


def _stores(sb: Path) -> list[str]:
    return sorted(p.name for p in (sb / "home").rglob("cel_anchors/*.jsonl"))


def main() -> int:
    sb = Path(tempfile.mkdtemp(prefix="anchor-e2e-"))
    env = h._env(sb)
    chats = ("anchor-chat-a", "anchor-chat-b")
    (sb / "bridges" / h.CHANNEL).mkdir(parents=True)
    (sb / "bridges" / h.CHANNEL / "settings.json").write_text(
        json.dumps({"chat_profiles": {c: {} for c in chats}}))
    overlay = sb / "home" / "tenants" / "_default" / "global" / "features.json"
    overlay.parent.mkdir(parents=True, exist_ok=True)
    overlay.write_text(json.dumps({"flags": {"vibe_engineering": True,
                                             "cel_load_bearing_anchor": True}}))
    proc = subprocess.Popen([sys.executable, str(h.ADAPTER)], env=env,
                            stdout=open(sb / "adapter.log", "a"), stderr=subprocess.STDOUT)
    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        print(("  ok   " if cond else "  FAIL ") + msg)
        if not cond:
            failures.append(msg)

    try:
        time.sleep(0.5)
        _send(sb, chats[0], "Goal for this chat: migrate the billing export to Parquet", 1)
        _send(sb, chats[1], "Goal for this chat: plan the spring garden beds", 2)
        st = _stores(sb)
        print("stores:", st)
        check("_nosession.jsonl" not in st, "no shared _nosession bucket")
        check(any(chats[0] in s for s in st) and any(chats[1] in s for s in st),
              "each chat has its own anchor store")
        a = next((p for p in (sb / "home").rglob(f"cel_anchors/*{chats[0]}*.jsonl")), None)
        b_txt = "".join(p.read_text() for p in (sb / "home").rglob(f"cel_anchors/*{chats[1]}*.jsonl"))
        check(a is not None and "billing export" in a.read_text(), "chat A's goal is in chat A's store")
        check("billing export" not in b_txt, "chat A's goal never reaches chat B's store")

        r = subprocess.run([sys.executable, str(h.RESET), "--channel", h.CHANNEL, "--chat-id",
                            chats[0], "--reason", "timeout"], env=env, capture_output=True, text=True)
        check(r.returncode == 0, "timeout reset ran")
        _send(sb, chats[0], "continue with the export", 3)
        st2 = _stores(sb)
        # The anchored goal of turn 1 (promoted once turn 1 was answered) must
        # reach the WORKER prompt of this turn — deterministic CEL path.
        calls = [json.loads(l) for l in (sb / "args.jsonl").read_text().splitlines() if l.strip()]
        a_calls = [c for c in calls if c.get("chat_key") == chats[0]]
        args = a_calls[-1]["args"]
        sp = (args[args.index("--append-system-prompt") + 1] if "--append-system-prompt" in args
              else Path(args[args.index("--append-system-prompt-file") + 1]).read_text())
        check("migrate the billing export to Parquet" in sp.split("Load-bearing facts", 1)[-1]
              if "Load-bearing facts" in sp else False,
              "chat A's anchored goal reaches the worker's system prompt")
        b_calls = [c for c in calls if c.get("chat_key") == chats[1]]
        b_args = b_calls[-1]["args"]
        b_sp = (b_args[b_args.index("--append-system-prompt") + 1] if "--append-system-prompt" in b_args
                else Path(b_args[b_args.index("--append-system-prompt-file") + 1]).read_text())
        check("billing export" not in b_sp, "chat A's goal never reaches chat B's worker")
        check(st2 == st, f"an unwanted reset keeps the same store: {st2}")

        # /new needs a session to wipe; the fake engine leaves none, so mark one.
        wd = next(p.parent.parent for p in (sb / "home").rglob(".corvin-ledger/ledger.jsonl")
                  if json.loads(p.read_text().splitlines()[0])["chat_key"] == chats[0])
        (wd / ".session_started").touch()
        r = subprocess.run([sys.executable, str(h.RESET), "--channel", h.CHANNEL, "--chat-id",
                            chats[0], "--reason", "manual"], env=env, capture_output=True, text=True)
        check(r.returncode == 0, "/new ran")
        _send(sb, chats[0], "Goal for this chat: write release notes", 4)
        st3 = _stores(sb)
        new = [s for s in st3 if s not in st2]
        # The key is "<channel>:<chat>#<seq>"; the store sanitises ":#" to "_".
        check(len(new) == 1 and new[0].startswith(f"telegram_{chats[0]}_")
              and new[0][len(f"telegram_{chats[0]}_"):-len(".jsonl")].isdigit(),
              f"/new starts a new epoch store: {new}")
        if new:
            fresh = next((sb / "home").rglob(f"cel_anchors/{new[0]}")).read_text()
            check("billing export" not in fresh and "release notes" in fresh,
                  "the new epoch does not inherit the old goal")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    if failures:
        print(f"\nFAILED ({len(failures)}); sandbox kept at {sb}")
        return 1
    shutil.rmtree(sb, ignore_errors=True)
    print("\nPASS")
    return 0


def test_cel_anchor_bridge_e2e():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
