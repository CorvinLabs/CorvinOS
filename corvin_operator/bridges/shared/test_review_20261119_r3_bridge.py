"""Review 2026-11-19, round 3 - bridge findings R3-BR-1, R3-BR-7, R3-NEW-16.

* R3-BR-1: a provider turn whose history re-render fails must not find the NATIVE engine's
  history view in its cwd.
* R3-BR-7: the debug-log fingerprint key heals a truncated key file and tightens a
  group/world-readable one.
* R3-NEW-16: two chat ids whose sanitised name is the same never share a working
  directory, a history view or a ledger.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pytest  # noqa: E402

from test_bridge_sensitive_reroute_e2e import _fresh_adapter, _policy, env  # noqa: E402,F401
from test_ledger_reroute_regate_e2e import (  # noqa: E402,F401
    NATIVE_A, NATIVE_Q, _record, _workdir, renv,
)

VIEW = ".corvin-history.md"


# ── R3-BR-1 ─────────────────────────────────────────────────────────────────


def _native_render(adapter, ledger, wd, chat):
    return ledger.render_context(
        wd, channel="discord", chat_key=chat, tenant_id="_default",
        withhold=adapter._ledger_withhold("claude_code", "discord", chat))


def test_br1_failed_rerender_removes_the_native_view(renv, monkeypatch):
    renv.write(_policy(allow_default=True))
    wd = _workdir("r3fail")
    _record(wd, "r3fail", NATIVE_Q, NATIVE_A, spawned=True)
    adapter = _fresh_adapter()
    import session_ledger as ledger  # type: ignore

    native = _native_render(adapter, ledger, wd, "r3fail")
    assert NATIVE_A in native
    # Positive control: the native render published a view that holds the text.
    assert NATIVE_A in (wd / VIEW).read_text(encoding="utf-8")

    def _boom(*_a, **_k):
        raise OSError("ledger unreadable")
    monkeypatch.setattr(ledger, "read_ledger", _boom)
    resolved = {"system": "P" + native}
    problem = adapter._ledger_regate_for_route(
        resolved, workdir=wd, session_dir=None, session_id=None, channel="discord",
        chat_key="r3fail", provider="never_declared", native_block=native)
    assert problem is None
    assert NATIVE_A not in resolved["system"]
    assert not (wd / VIEW).exists(), "the native view stayed in the provider worker's cwd"


def test_br1_failed_withhold_construction_removes_the_native_view(renv, monkeypatch):
    """The failure can also be raised BEFORE render_context runs (the adapter's except)."""
    renv.write(_policy(allow_default=True))
    wd = _workdir("r3fail2")
    _record(wd, "r3fail2", NATIVE_Q, NATIVE_A, spawned=True)
    adapter = _fresh_adapter()
    import session_ledger as ledger  # type: ignore

    native = _native_render(adapter, ledger, wd, "r3fail2")
    assert NATIVE_A in (wd / VIEW).read_text(encoding="utf-8")

    def _boom(*_a, **_k):
        raise RuntimeError("withhold hook broke")
    monkeypatch.setattr(adapter, "_ledger_withhold", _boom)
    resolved = {"system": "P" + native}
    assert adapter._ledger_regate_for_route(
        resolved, workdir=wd, session_dir=None, session_id=None, channel="discord",
        chat_key="r3fail2", provider="never_declared", native_block=native) is None
    assert NATIVE_A not in resolved["system"]
    assert not (wd / VIEW).exists()


def test_br1_failed_route_render_removes_the_route_view_too(renv, monkeypatch):
    renv.write(_policy(allow_default=True))
    wd = _workdir("r3route")
    _record(wd, "r3route", NATIVE_Q, NATIVE_A, spawned=True)
    adapter = _fresh_adapter()
    import session_ledger as ledger  # type: ignore

    route = wd / ".route-acme-deadbeef"
    route.mkdir()
    (route / VIEW).write_text("STALE-ROUTE-VIEW " + NATIVE_A, encoding="utf-8")
    monkeypatch.setattr(ledger, "read_ledger",
                        lambda *_a, **_k: (_ for _ in ()).throw(OSError("unreadable")))
    assert ledger.render_context(wd, channel="discord", chat_key="r3route",
                                 tenant_id="_default", session_dir=route) == ""
    assert not (route / VIEW).exists()
    assert not (wd / VIEW).exists()


def test_br1_successful_render_still_publishes(renv):
    renv.write(_policy(allow_default=True))
    wd = _workdir("r3ok")
    _record(wd, "r3ok", NATIVE_Q, NATIVE_A, spawned=True)
    adapter = _fresh_adapter()
    import session_ledger as ledger  # type: ignore
    _native_render(adapter, ledger, wd, "r3ok")
    assert (wd / VIEW).is_file()


# ── R3-BR-7 ─────────────────────────────────────────────────────────────────


def _key_path() -> Path:
    import paths  # type: ignore
    kp = Path(paths.tenant_global_dir("_default")) / "chat_debug_fp.key"
    assert "corvin" in str(kp) and str(kp).startswith(os.environ["CORVIN_HOME"]), kp
    kp.parent.mkdir(parents=True, exist_ok=True)
    return kp


def test_br7_truncated_key_file_is_replaced(env):
    adapter = _fresh_adapter()
    kp = _key_path()
    kp.write_bytes(b"short")
    adapter._chat_debug_fp_keys.clear()
    fp = adapter._chat_debug_fp("491701234567")
    assert fp, "a truncated key file left the install without a fingerprint"
    assert len(kp.read_bytes()) == 32
    assert stat.S_IMODE(kp.stat().st_mode) == 0o600
    assert [p.name for p in kp.parent.iterdir() if p.name.endswith(".tmp")
            or (p.name.startswith(".chat_debug_fp.") and p.name != ".chat_debug_fp.lock")] == []
    # Stable afterwards: a second process reads the SAME key.
    adapter._chat_debug_fp_keys.clear()
    assert adapter._chat_debug_fp("491701234567") == fp
    # An empty file is the same case.
    kp.write_bytes(b"")
    adapter._chat_debug_fp_keys.clear()
    assert adapter._chat_debug_fp("491701234567") not in ("", fp)


def test_br7_loose_mode_is_tightened_and_key_kept(env):
    adapter = _fresh_adapter()
    kp = _key_path()
    kp.write_bytes(b"A" * 32)
    os.chmod(kp, 0o644)
    adapter._chat_debug_fp_keys.clear()
    fp = adapter._chat_debug_fp("491701234567")
    assert fp
    assert stat.S_IMODE(kp.stat().st_mode) == 0o600
    assert kp.read_bytes() == b"A" * 32, "a usable key must never be replaced"


def test_br7_non_regular_or_linked_key_is_not_used(env, tmp_path):
    adapter = _fresh_adapter()
    kp = _key_path()
    known = tmp_path / "attacker-known.key"
    known.write_bytes(b"K" * 32)
    kp.symlink_to(known)
    adapter._chat_debug_fp_keys.clear()
    assert adapter._chat_debug_fp("491701234567") == ""
    assert known.read_bytes() == b"K" * 32
    kp.unlink()
    os.link(known, kp)                      # a hard link to a file someone else can read
    adapter._chat_debug_fp_keys.clear()
    assert adapter._chat_debug_fp("491701234567") == ""
    kp.unlink()
    kp.mkdir()
    adapter._chat_debug_fp_keys.clear()
    assert adapter._chat_debug_fp("491701234567") == ""


# ── R3-NEW-16 ───────────────────────────────────────────────────────────────

A, B, C = "a.b@x.com", "a_b@x.com", "a-b@x.com"


def _turn(adapter, wd, chat, text):
    import session_ledger as ledger  # type: ignore
    ledger.append_turn(wd, channel="email", chat_key=chat, user_text=f"q {text}",
                       assistant_text=text, spawned=True, tenant_id="_default")


def test_new16_colliding_chat_ids_get_separate_dirs_views_and_ledgers(env):
    adapter = _fresh_adapter()
    import session_ledger as ledger  # type: ignore
    import session_state as ss  # type: ignore

    assert ss.legacy_chat_key(A) == ss.legacy_chat_key(B) == ss.legacy_chat_key(C)
    wa = adapter._session_dir("email", A)
    wb = adapter._session_dir("email", B)
    wc = adapter._session_dir("email", C)
    assert len({wa, wb, wc}) == 3, (wa, wb, wc)
    assert wa.name == ss.legacy_chat_key(A), "the first chat keeps the name it always had"
    assert wb.name == ss.hashed_chat_key(B) and wc.name == ss.hashed_chat_key(C)
    assert len({ledger.ledger_dir(w) for w in (wa, wb, wc)}) == 3
    _turn(adapter, wa, A, "SECRET-OF-A")
    _turn(adapter, wb, B, "SECRET-OF-B")
    for w, chat, own, other in ((wa, A, "SECRET-OF-A", "SECRET-OF-B"),
                                (wb, B, "SECRET-OF-B", "SECRET-OF-A")):
        block = ledger.render_context(w, channel="email", chat_key=chat, tenant_id="_default")
        view = (w / VIEW).read_text(encoding="utf-8")
        assert own in block and own in view
        assert other not in block and other not in view
    # Stable: the same answers on every later call, and from a fresh process.
    assert adapter._session_dir("email", A) == wa and adapter._session_dir("email", B) == wb
    adapter2 = _fresh_adapter()
    assert adapter2._session_dir("email", A) == wa
    assert adapter2._session_dir("email", B) == wb
    assert adapter2._session_dir("email", C) == wc
    # The resolver the reset / completion-notify / erasure side uses agrees.
    assert ss.claude_session_dirs("email", A, tenant_id="_default")[0] == wa
    assert ss.claude_session_dirs("email", B, tenant_id="_default")[0] == wb


def test_new16_existing_chat_keeps_its_directory(env):
    """A chat that existed before the rule (ledger with only its own turns, no marker):
    nothing moves, and the marker is written on first access."""
    adapter = _fresh_adapter()
    import paths  # type: ignore
    import session_ledger as ledger  # type: ignore
    import session_state as ss  # type: ignore

    jid = "4917012345@s.whatsapp.net"
    old = Path(paths.voice_session_dir("whatsapp", ss.legacy_chat_key(jid)))
    old.mkdir(parents=True)
    (old / "project.txt").write_text("keep me", encoding="utf-8")
    _turn(adapter, old, jid, "OLD-TURN")
    marker = ledger.ledger_dir(old) / ss.CHAT_OWNER_FILE
    assert not marker.exists()
    assert adapter._session_dir("whatsapp", jid) == old
    assert (old / "project.txt").read_text(encoding="utf-8") == "keep me"
    assert json.loads(marker.read_text(encoding="utf-8"))["chat_key"] == jid
    assert stat.S_IMODE(marker.stat().st_mode) == 0o600
    # A newcomer with the same sanitised name is diverted; the old chat stays.
    other = "4917012345.s@whatsapp.net"
    assert ss.legacy_chat_key(other) == ss.legacy_chat_key(jid)
    wo = adapter._session_dir("whatsapp", other)
    assert wo != old and wo.name == ss.hashed_chat_key(other)
    assert adapter._session_dir("whatsapp", jid) == old


def test_new16_already_shared_directory_is_left_alone(env):
    """Two chat ids that already wrote into ONE directory: neither keeps it, the shared
    directory and its mixed ledger stay untouched, and neither sees the other's turns."""
    adapter = _fresh_adapter()
    import paths  # type: ignore
    import session_ledger as ledger  # type: ignore
    import session_state as ss  # type: ignore

    shared = Path(paths.voice_session_dir("email", ss.legacy_chat_key(A)))
    shared.mkdir(parents=True)
    _turn(adapter, shared, A, "MIXED-A")
    _turn(adapter, shared, B, "MIXED-B")
    before = ledger.ledger_path(shared).read_bytes()
    wa, wb = adapter._session_dir("email", A), adapter._session_dir("email", B)
    assert shared not in (wa, wb) and wa != wb
    assert ledger.ledger_path(shared).read_bytes() == before
    assert not (ledger.ledger_dir(shared) / ss.CHAT_OWNER_FILE).exists()
    for w, chat in ((wa, A), (wb, B)):
        assert ledger.render_context(w, channel="email", chat_key=chat,
                                     tenant_id="_default") == ""
        assert not (w / VIEW).exists() or "MIXED" not in (w / VIEW).read_text(encoding="utf-8")


def test_new16_plain_ids_and_boundaries_do_not_divert(env):
    adapter = _fresh_adapter()
    import session_ledger as ledger  # type: ignore
    import session_state as ss  # type: ignore

    wd = adapter._session_dir("discord", "1502103856740302964")
    assert wd.name == "1502103856740302964"
    # The inactivity sweep records a boundary under the DIRECTORY name; that is not
    # another chat's turn.
    jid = "-100123"
    w1 = adapter._session_dir("telegram", jid)
    _turn(adapter, w1, jid, "T")
    ledger.append_boundary(w1, kind="reset", reason="inactivity", channel="telegram",
                           chat_key=ss.legacy_chat_key(jid), tenant_id="_default")
    (ledger.ledger_dir(w1) / ss.CHAT_OWNER_FILE).unlink()
    assert adapter._session_dir("telegram", jid) == w1


def test_new16_unreadable_marker_never_grants_the_name(env):
    adapter = _fresh_adapter()
    import session_ledger as ledger  # type: ignore
    import session_state as ss  # type: ignore

    wa = adapter._session_dir("email", A)
    marker = ledger.ledger_dir(wa) / ss.CHAT_OWNER_FILE
    marker.write_text("not json", encoding="utf-8")
    assert adapter._session_dir("email", B).name == ss.hashed_chat_key(B)
    # A itself cannot prove the name either and moves to its own hashed directory.
    assert adapter._session_dir("email", A).name == ss.hashed_chat_key(A)


def test_new16_route_session_dir_name_is_unchanged(tmp_path):
    import session_state as ss  # type: ignore
    d = ss.route_session_dir(tmp_path, "acme-llm")
    assert d.name.startswith(".route-acme_llm-") and len(d.name) == len(".route-acme_llm-") + 8


def test_new16_anchor_store_names_do_not_collide():
    try:
        from corvin_operator.context_engineering import anchor
    except ImportError:
        pytest.skip("context_engineering package not importable here")
    plus, under, colon = "email:a+b@x.com", "email:a_b@x.com", "email:a:b@x.com"
    assert anchor._lossy_safe_key(plus) == anchor._lossy_safe_key(under) \
        == anchor._lossy_safe_key(colon)
    assert len({anchor._safe_key(k) for k in (plus, under, colon)}) == 3
    # Keys whose old name is provably theirs keep it (no store moves, erasure still matches).
    for key in ("discord:1502103856740302964", "telegram:-100123",
                "whatsapp:4917012345@s.whatsapp.net", "whatsapp:1-2@g.us",
                "email:a.b@x.com", "6wqkgf4UeNVpYbTQ8wme8g", "discord:123#4"):
        assert anchor._safe_key(key) == anchor._lossy_safe_key(key), key
    # A renamed key: old name + "_" + 20 decimal digits (+ the /new epoch) - the shape the
    # erasure handler's "<old name>(_<digits>)?" rule already matches.
    import re
    old = anchor._lossy_safe_key(under)
    assert re.fullmatch(re.escape(old) + r"_\d{20}", anchor._safe_key(under))
    assert anchor._safe_key(under + "#7") == anchor._safe_key(under) + "7"
    assert re.fullmatch(re.escape(old) + r"_\d+", anchor._safe_key(under + "#7"))
    names = {anchor._safe_key(k + e) for k in (plus, under, colon) for e in ("", "#1", "#12")}
    assert len(names) == 9


def test_new16_anchor_store_migrates_only_when_provably_the_chats(env):
    try:
        from corvin_operator.context_engineering import anchor
    except ImportError:
        pytest.skip("context_engineering package not importable here")
    from corvin_operator.forge.forge.paths import tenant_home
    root = Path(tenant_home("_default")) / "cel_anchors"
    root.mkdir(parents=True, exist_ok=True)
    key = "email:a_b@x.com"
    old = root / f"{anchor._lossy_safe_key(key)}.jsonl"
    # Positive control: a store whose every entry was written by the chat id itself moves.
    old.write_text(json.dumps({"id": "1", "kind": "goal", "text": "mine", "hash": "h",
                               "sender": "a_b@x.com"}) + "\n", encoding="utf-8")
    assert [f["text"] for f in anchor.load_facts("_default", key)] == ["mine"]
    assert not old.exists()
    # A store with an entry of unknown or foreign origin stays where it is, unread.
    other = "email:a+b@x.com"
    assert anchor.load_facts("_default", other) == []
    old.write_text(json.dumps({"id": "2", "kind": "goal", "text": "whose?", "hash": "g"}) + "\n"
                   + json.dumps({"id": "3", "kind": "goal", "text": "foreign", "hash": "f",
                                 "sender": "a_b@x.com"}) + "\n", encoding="utf-8")
    before = old.read_bytes()
    assert anchor.load_facts("_default", other) == []
    assert old.read_bytes() == before
    anchor.add_fact("_default", other, "goal", "the plus chat's goal", sender="a+b@x.com")
    assert [f["text"] for f in anchor.load_facts("_default", other)] == ["the plus chat's goal"]
    assert [f["text"] for f in anchor.load_facts("_default", key)] == ["mine"]


# ── R3-NEW-41 (Python side): attachments are files OF outputs/, never link targets ──


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    import time as _time  # noqa: F401
    base = tmp_path
    for d in ("inbox", "outbox", "processed", "bridges", "home"):
        (base / d).mkdir()
    for k, v in {
        "CORVIN_HOME": base / "corvin", "XDG_CONFIG_HOME": base / "xdg", "HOME": base / "home",
        "CORVIN_TENANT_ID": "_default", "CORVIN_AUDIT_ANCHOR_KEY": base / "anchor.key",
        "ADAPTER_INBOX": base / "inbox", "ADAPTER_OUTBOX": base / "outbox",
        "ADAPTER_PROCESSED": base / "processed", "ADAPTER_BRIDGES_DIR": base / "bridges",
        "ADAPTER_FAKE_CLAUDE": "1", "ADAPTER_FAKE_DELAY": "0", "ADAPTER_DISABLE_VOICE": "1",
        "ADAPTER_ROUTING_MODE": "off", "CLAUDE_CONFIG_DIR": base / "claude-cfg",
    }.items():
        monkeypatch.setenv(k, str(v))
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    sys.modules.pop("adapter", None)
    import adapter  # type: ignore
    adapter._house_rules_classifier = lambda task, rules, auth, **_kw: ("", 1.0, "test-benign")
    return adapter, base


def test_new41_symlinked_output_is_not_attached(sandbox, monkeypatch):
    import time
    adapter, base = sandbox
    secret = base / "home" / "id_rsa"
    secret.write_text("PRIVATE-KEY-MATERIAL", encoding="utf-8")
    elsewhere = base / "home" / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "inner.txt").write_text("OUTSIDE", encoding="utf-8")

    def _worker(prompt, **_kw):
        out = adapter._session_dir("discord", "att-1") / "outputs"
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.txt").write_text("a real output", encoding="utf-8")
        (out / "leak.txt").symlink_to(secret)
        (out / "dirlink").symlink_to(elsewhere, target_is_directory=True)
        return "done"
    monkeypatch.setattr(adapter, "call_claude_streaming", _worker)
    f = base / "inbox" / "a1.json"
    f.write_text(json.dumps({"id": "a1", "channel": "discord", "from": "424242",
                             "chat_id": "att-1", "chat_type": "private",
                             "text": "write the report", "ts": time.time()}), encoding="utf-8")
    adapter.process_one(f, settings={"whitelist": ["424242"]})
    envelopes = [json.loads(p.read_text(encoding="utf-8")) for p in (base / "outbox").glob("*.json")]
    paths = [e.get(k) for e in envelopes
             for k in ("document_path", "image_path", "video_path") if e.get(k)]
    assert any(str(p).endswith("outputs/report.txt") for p in paths), paths   # positive control
    assert not any("leak" in str(p) or "id_rsa" in str(p) for p in paths), paths
    assert "PRIVATE-KEY-MATERIAL" not in json.dumps(envelopes)
