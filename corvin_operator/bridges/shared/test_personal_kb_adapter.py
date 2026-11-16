#!/usr/bin/env python3
"""Personal knowledge base through the REAL bridge adapter (``process_one``), PLAN-0973 section 8.

Real inbox/outbox files, the real ``/personal`` handler, the real prompt assembly (the
``ADAPTER_FAKE_CLAUDE`` hook dumps the args it built), the real session ledger and the real
capture path: extraction runs a fake ``claude`` BINARY (a subprocess) through
``CORVIN_CLAUDE_BIN``. Only the L44 classifier is a test-local double, as in test_adapter_btw.py.

Covers: /personal on/remember/status in a private chat; refusal in a group chat; the block reaches
the system prompt only in a private chat and before the L28.2 slot; the ledger keeps a /personal
turn as a length only; the /personal envelope is not archived; automatic capture stores a taxonomy
preference after a plain private turn.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
sys.path.insert(0, str(ROOT))
if str(REPO) not in sys.path:
    sys.path.append(str(REPO))


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    base = tmp_path
    for d in ("inbox", "outbox", "processed", "bridges", "home", "xdg"):
        (base / d).mkdir()
    fake = base / "fake_claude.py"
    fake.write_text(
        "#!" + sys.executable + "\n"
        "import json, sys\n"
        "sys.stdin.read()\n"
        "print(json.dumps({'items': [{'type': 'preference', 'key': 'verbosity', 'value': 'brief',"
        " 'confidence': 0.9, 'sensitive': False, 'about_other_person': False, 'uncertain': False}]}))\n",
        encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    env = {
        "CORVIN_HOME": str(base / "corvin"), "XDG_CONFIG_HOME": str(base / "xdg"), "HOME": str(base / "home"),
        "CORVIN_TENANT_ID": "_default", "CORVIN_AUDIT_ANCHOR_KEY": str(base / "anchor.key"),
        "ADAPTER_INBOX": str(base / "inbox"), "ADAPTER_OUTBOX": str(base / "outbox"),
        "ADAPTER_PROCESSED": str(base / "processed"), "ADAPTER_BRIDGES_DIR": str(base / "bridges"),
        "ADAPTER_FAKE_CLAUDE": "1", "ADAPTER_FAKE_DELAY": "0", "ADAPTER_FAKE_ARGS_DUMP": str(base / "args.jsonl"),
        "CORVIN_CLAUDE_BIN": str(fake),
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    for mod in list(sys.modules):
        if mod == "adapter":
            del sys.modules[mod]
    import adapter  # type: ignore
    adapter._house_rules_classifier = lambda task, rules, auth, **_kw: ("", 1.0, "test-benign")
    from core.personal_kb import capabilities, crypto, limits, session
    monkeypatch.setattr(crypto, "KDF_PARAMS", {"n": 2 ** 10, "r": 8, "p": 1})
    limits._reset_for_tests()
    session._reset_for_tests()
    capabilities._set_for_tests(None)
    yield adapter, base
    session._reset_for_tests()


def _send(adapter, base, text, *, mid, chat_type="private", sender="424242", chat_id="dm-1"):
    env = {"id": mid, "channel": "discord", "from": sender, "chat_id": chat_id, "chat_type": chat_type,
           "text": text, "ts": time.time()}
    f = base / "inbox" / f"{mid}.json"
    f.write_text(json.dumps(env), encoding="utf-8")
    adapter.process_one(f, settings={"whitelist": [sender]})
    outs = sorted((base / "outbox").glob(f"{mid}_*.json"))
    return [json.loads(o.read_text(encoding="utf-8"))["text"] for o in outs], f


def _system_prompts(base):
    out = []
    p = base / "args.jsonl"
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        args = json.loads(line)["args"]
        txt = json.dumps(args)
        for i, a in enumerate(args):
            if a in ("--append-system-prompt-file", "--system-prompt-file") and i + 1 < len(args):
                with open(args[i + 1], encoding="utf-8") as fh:
                    txt += fh.read()
        out.append(txt)
    return out


def test_personal_commands_block_ledger_and_capture(sandbox):
    adapter, base = sandbox
    from core.personal_kb import owner, render
    from core.personal_kb.store import PersonalStore

    replies, f = _send(adapter, base, "/personal on", mid="m1")
    assert "/personal on yes" in replies[0] and not f.exists() and not (base / "processed" / "m1.json").exists()
    replies, _ = _send(adapter, base, "/personal on yes", mid="m2")
    assert "ready" in replies[0]
    replies, _ = _send(adapter, base, "/personal remember I like the metric system", mid="m3")
    assert "Remembered" in replies[0]
    replies, _ = _send(adapter, base, "/personal status", mid="m4", chat_type="group", chat_id="guild-1")
    assert "private chat" in replies[0]

    # The ledger holds /personal turns as a length only: no text, no reply, no hash.
    ledgers = list((base / "corvin").rglob("ledger.jsonl"))
    assert ledgers
    lines = [json.loads(x) for p in ledgers for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    pk = [r for r in lines if r.get("refused") == "personal_command"]
    assert len(pk) >= 3
    for r in pk:
        texts = [r.get(k) for k in ("user", "assistant", "user_text", "assistant_text", "observer_text") if k in r]
        assert texts and all(v == "" for v in texts), r
        assert "user_sha256" not in r and r["user_chars"] > 0
    assert "metric" not in "".join(p.read_text(encoding="utf-8") for p in ledgers)

    # A plain private turn: the framed block reaches the system prompt; a group turn: it does not.
    _send(adapter, base, "Can you help me plan the week?", mid="m5")
    _send(adapter, base, "Can you help me plan the week?", mid="m6", chat_type="group", chat_id="guild-1")
    prompts = _system_prompts(base)
    assert len(prompts) >= 2
    priv = [p for p in prompts if "metric system" in p]
    assert priv and render.HEADER[:40] in priv[0]
    assert sum(1 for p in prompts if render.HEADER[:40] in p) == 1  # the group turn got nothing

    # Automatic capture after a plain private turn: the fake engine binary returns a taxonomy item.
    _send(adapter, base, "I prefer brief answers from now on", mid="m7")
    oid = owner.resolve_owner("_default", channel="discord", uid="424242")
    deadline = time.time() + 30
    found = None
    while time.time() < deadline and found is None:
        keys = owner.unlock_with_device("_default", oid, surface="test")
        s = PersonalStore.open("_default", oid, keys)
        found = next((e for e in s.list(type="preference") if e.get("key") == "verbosity"), None)
        time.sleep(0.3)
    assert found is not None and found["origin"] == "auto" and found["review"] == "pending"


def test_owned_chat_is_marked_and_rerouted_provider_regate_removes_the_block(sandbox, monkeypatch, tmp_path):
    adapter, base = sandbox
    marked = []

    class _Recording(set):
        def add(self, k):
            marked.append(k)
            super().add(k)
    monkeypatch.setattr(adapter, "_PKB_OWNED", _Recording())
    _send(adapter, base, "/personal on yes", mid="r1")
    _send(adapter, base, "/personal remember I like the metric system", mid="r2")
    _send(adapter, base, "What should I cook tonight?", mid="r3")
    # the sender owns a space: during the turn the chat is marked, so the legacy L28.2 block is skipped
    assert ("discord", "dm-1") in marked
    blk = adapter._pkb_bridge().context_block("_default", channel="discord", uid="424242", chat_type="private",
                                              task_text="dinner")
    assert blk and "metric system" in blk
    adapter._PKB_BLOCKS[("discord", "dm-1")] = blk   # the state a rerouted turn is in

    # A rerouted provider the L34 gate refuses: the block is removed from the system prompt file.
    import spawn_gates  # type: ignore
    spf = tmp_path / "sys.txt"
    spf.write_text("BASE PROMPT\n\n" + blk + "\n\nTAIL", encoding="utf-8")
    resolved = {"system_prompt_file": str(spf)}
    monkeypatch.setattr(spawn_gates, "check_l34", lambda *a, **k: "refused for this provider")
    adapter._pkb_regate_for_provider(resolved, "openrouter", "discord", "dm-1")
    txt = spf.read_text(encoding="utf-8")
    assert "metric system" not in txt and "BASE PROMPT" in txt and "TAIL" in txt

    # Allowed: unchanged. A gate error: removed (fail-closed).
    spf.write_text("BASE\n\n" + blk, encoding="utf-8")
    monkeypatch.setattr(spawn_gates, "check_l34", lambda *a, **k: None)
    adapter._pkb_regate_for_provider(resolved, "openrouter", "discord", "dm-1")
    assert "metric system" in spf.read_text(encoding="utf-8")
    monkeypatch.setattr(spawn_gates, "check_l34", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    adapter._pkb_regate_for_provider(resolved, "openrouter", "discord", "dm-1")
    assert "metric system" not in spf.read_text(encoding="utf-8")


def test_forwarded_private_turn_is_not_learned(sandbox):
    adapter, base = sandbox
    from core.personal_kb import owner
    from core.personal_kb.store import PersonalStore

    _send(adapter, base, "/personal on yes", mid="f1")
    env = {"id": "f2", "channel": "discord", "from": "424242", "chat_id": "dm-1", "chat_type": "private",
           "forwarded": True, "text": "I prefer brief answers from now on", "ts": time.time()}
    f = base / "inbox" / "f2.json"
    f.write_text(json.dumps(env), encoding="utf-8")
    adapter.process_one(f, settings={"whitelist": ["424242"]})
    time.sleep(2.0)
    oid = owner.resolve_owner("_default", channel="discord", uid="424242")
    s = PersonalStore.open("_default", oid, owner.unlock_with_device("_default", oid, surface="test"))
    assert [e for e in s.list(type="preference")] == []


def test_observer_personal_command_is_burned_and_never_buffered(sandbox):
    adapter, base = sandbox
    env = {"id": "o1", "channel": "discord", "from": "999", "chat_id": "guild-1", "chat_type": "group",
           "_observer": True, "text": "/personal link ABCD-EFGH", "ts": time.time()}
    f = base / "inbox" / "o1.json"
    f.write_text(json.dumps(env), encoding="utf-8")
    adapter.process_one(f, settings={"whitelist": ["424242"]})
    assert not f.exists() and not (base / "processed" / "o1.json").exists()
    assert not adapter._observer_buffer_path("discord", "guild-1").exists()
    # a failed /personal turn is never quarantined with its text
    g = base / "inbox" / "p1.json"
    g.write_text(json.dumps({"id": "p1", "text": " /personal remember my secret"}), encoding="utf-8")
    assert adapter._is_pkb_envelope(g)


def test_personal_block_is_masked_in_persisted_prompts_and_withheld_for_unknown_providers(sandbox, tmp_path):
    adapter, _base = sandbox
    blk = "<personal_settings>\nverbosity: brief\n</personal_settings>"
    masked = adapter._mask_pkb_block("BASE\n\n" + blk + "\n\nTAIL", blk)
    assert "verbosity" not in masked and "not persisted" in masked and "TAIL" in masked
    spf = tmp_path / "sys.txt"
    spf.write_text("BASE\n\n" + blk, encoding="utf-8")
    adapter._PKB_BLOCKS[("discord", "dm-x")] = blk
    adapter._pkb_regate_for_provider({"system_prompt_file": str(spf)}, None, "discord", "dm-x", force_withhold=True)
    assert "verbosity" not in spf.read_text(encoding="utf-8")


def test_group_mentions_and_bot_suffixes_do_not_hide_a_personal_command(sandbox):
    adapter, base = sandbox
    assert adapter._PKB_COMMAND_RE.match(adapter._pkb_norm_text("<at>Corvin</at> /personal link ABCD"))
    assert adapter._PKB_COMMAND_RE.match(adapter._pkb_norm_text("/personal@CorvinBot link ABCD"))
    assert adapter._PKB_COMMAND_RE.match(adapter._pkb_norm_text("/me@CorvinBot status"))
    assert not adapter._PKB_COMMAND_RE.match(adapter._pkb_norm_text("please mention /personal later"))




def test_observer_bot_suffixed_link_is_burned(sandbox):
    adapter, base = sandbox
    from core.personal_kb import owner
    _send(adapter, base, "/personal on yes", mid="b1")
    oid = owner.resolve_owner("_default", channel="discord", uid="424242")
    code = owner.issue_pair_code("_default", oid)
    pairs = next((base / "corvin").rglob("_pairs.json"))
    assert len(json.loads(pairs.read_text())["codes"]) == 1
    env = {"id": "o9", "channel": "discord", "from": "999", "chat_id": "guild-9", "chat_type": "group",
           "_observer": True, "text": f"/personal@CorvinBot link {code}", "ts": time.time()}
    f = base / "inbox" / "o9.json"
    f.write_text(json.dumps(env), encoding="utf-8")
    adapter.process_one(f, settings={"whitelist": ["424242"]})
    assert json.loads(pairs.read_text())["codes"] == {}   # burned although the command carried a bot suffix


def test_mentions_before_the_command_do_not_hide_it(sandbox):
    adapter, _base = sandbox
    for t in ("<@U12345> /personal link ABCD", "@corvin_bot /personal link ABCD", "@corvin_bot /me status",
              "<at>Corvin</at> @Corvin /personal link ABCD"):
        assert adapter._PKB_COMMAND_RE.match(adapter._pkb_norm_text(t)), t
    assert not adapter._PKB_COMMAND_RE.match(adapter._pkb_norm_text("hello @corvin_bot please /personal later"))


def test_unicode_mention_before_a_command_is_recognised(sandbox):
    adapter, _base = sandbox
    assert adapter._PKB_COMMAND_RE.match(adapter._pkb_norm_text("@Jürgen /personal remember x"))


def test_announcer_and_adapter_share_one_mention_class(sandbox):
    adapter, _base = sandbox
    for t in ("@CorvinBot, /me remember x", "@Corvin: /personal x", "@Ana\U0001F338 /me x", "@a​b /me x"):
        assert adapter._PKB_COMMAND_RE.match(adapter._pkb_norm_text(t)), repr(t)


def test_python_and_announcer_agree_on_odd_whitespace(sandbox):
    adapter, _base = sandbox
    for t in ("\x85/personal remember x", "\x1f/personal remember x", "<@123>\x1f/personal x", "﻿/personal x",
              "/personal\x85remember x"):
        assert adapter._PKB_COMMAND_RE.match(adapter._pkb_norm_text(t)), repr(t)
