"""ADR-0178 M1 — ACO L5 Actuating Self-Repair (Tier LOCAL).

Proves: bounded to CORVIN_HOME (code-immutable), each SAFE action repairs its
fault, loss-gated rollback when a fix doesn't take, kill switch + risky gating,
dry-run is read-only.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "core" / "console", _REPO / "operator" / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pytest
from corvin_console.aco import repair_actions as RA  # type: ignore


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("CORVIN_ACO_L5_OFF", raising=False)
    monkeypatch.delenv("CORVIN_ACO_L5_RISKY", raising=False)


def _ctx(tmp_path: Path, now: float = 0.0) -> RA.RepairContext:
    return RA.RepairContext(corvin_home=tmp_path, tenant_id="_default", now=now)


# ── scope guard (the code-immutability guarantee) ─────────────────────────────

def test_assert_within_home_refuses_outside(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    RA._assert_within_home(home, home / "ok" / "file")          # inside → fine
    with pytest.raises(RA.RepairScopeError):
        RA._assert_within_home(home, tmp_path / "outside")       # sibling → refused
    with pytest.raises(RA.RepairScopeError):
        RA._assert_within_home(home, Path("/usr/lib/python3/site-packages/x.py"))


# ── session_workdir_missing ───────────────────────────────────────────────────

def test_recreates_missing_session_workdir(tmp_path):
    home = tmp_path
    wd = home / "tenants" / "_default" / "sessions" / "web_abc123"
    meta_dir = home / "tenants" / "_default" / "global" / "web_chat" / "sessions"
    meta_dir.mkdir(parents=True)
    (meta_dir / "abc123.json").write_text(f'{{"sid":"abc123","workdir":"{wd.as_posix()}"}}',
                                          encoding="utf-8")
    assert not wd.exists()
    out = RA.run_local_repairs(_ctx(home))
    applied = {o.action_id: o for o in out if o.status == "applied"}
    assert "session_workdir_missing" in applied
    assert wd.is_dir()


def test_workdir_action_refuses_path_outside_home(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    evil = tmp_path / "escape"          # outside the home
    meta_dir = home / "tenants" / "_default" / "global" / "web_chat" / "sessions"
    meta_dir.mkdir(parents=True)
    (meta_dir / "x.json").write_text(f'{{"workdir":"{evil.as_posix()}"}}', encoding="utf-8")
    RA.run_local_repairs(_ctx(home))
    assert not evil.exists()            # never created outside the home


# ── stale_lock ────────────────────────────────────────────────────────────────

def test_removes_stale_lock_keeps_fresh(tmp_path):
    home = tmp_path
    (home / "global").mkdir(parents=True)
    stale = home / "global" / "old.lock"
    fresh = home / "global" / "new.lock"
    stale.write_text("x"); fresh.write_text("y")
    old_t = time.time() - 25200  # 7 h ago (> 6h TTL)
    os.utime(stale, (old_t, old_t))
    RA.run_local_repairs(_ctx(home, now=time.time()))
    assert not stale.exists()   # stale removed
    assert fresh.exists()       # fresh kept


# ── orphan_tmp ────────────────────────────────────────────────────────────────

def test_removes_old_orphan_tmp(tmp_path):
    home = tmp_path
    (home / "d").mkdir()
    tmp = home / "d" / "partial.tmp"
    tmp.write_text("half")
    old = time.time() - 200000  # > 24 h
    os.utime(tmp, (old, old))
    RA.run_local_repairs(_ctx(home, now=time.time()))
    assert not tmp.exists()


# ── secret_file_mode (POSIX) ──────────────────────────────────────────────────

@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits only")
def test_tightens_loose_secret_file(tmp_path):
    home = tmp_path
    (home / "config").mkdir()
    sec = home / "config" / "service.env"
    sec.write_text("KEY=v")
    sec.chmod(0o644)  # too open
    RA.run_local_repairs(_ctx(home))
    assert (sec.stat().st_mode & 0o777) == 0o600


# ── loss-gated rollback ───────────────────────────────────────────────────────

def test_rollback_when_fault_persists(tmp_path, monkeypatch):
    # An action whose apply() does NOT clear the fault must be undone.
    undone = {"v": False}

    class _Stubborn(RA.RepairAction):
        action_id = "stubborn_test"
        def precondition(self, ctx): return ["fault"]          # ALWAYS present
        def apply(self, ctx, faults): return 1                  # pretends to fix
        def undo(self, ctx): undone["v"] = True

    monkeypatch.setitem(RA._REGISTRY, "stubborn_test", _Stubborn())
    out = {o.action_id: o for o in RA.run_local_repairs(_ctx(tmp_path))}
    RA._REGISTRY.pop("stubborn_test", None)
    assert out["stubborn_test"].status == "reverted"
    assert undone["v"] is True


# ── gating ────────────────────────────────────────────────────────────────────

def test_kill_switch_disables_all(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_ACO_L5_OFF", "1")
    (home := tmp_path / "global").mkdir(parents=True)
    stale = home / "x.lock"; stale.write_text("x")
    os.utime(stale, (time.time() - 25200,) * 2)
    assert RA.run_local_repairs(_ctx(tmp_path, now=time.time())) == []
    assert stale.exists()  # nothing touched


def test_risky_actions_gated(tmp_path, monkeypatch):
    ran = {"v": False}

    class _Risky(RA.RepairAction):
        action_id = "risky_test"; risk = RA.RISK_RISKY
        def precondition(self, ctx): return ["f"] if not ran["v"] else []
        def apply(self, ctx, faults): ran["v"] = True; return 1
        def undo(self, ctx): pass

    monkeypatch.setitem(RA._REGISTRY, "risky_test", _Risky())
    try:
        RA.run_local_repairs(_ctx(tmp_path))          # risky NOT enabled
        assert ran["v"] is False
        monkeypatch.setenv("CORVIN_ACO_L5_RISKY", "1")
        RA.run_local_repairs(_ctx(tmp_path))          # now enabled
        assert ran["v"] is True
    finally:
        RA._REGISTRY.pop("risky_test", None)


def test_dry_run_is_read_only(tmp_path):
    home = tmp_path
    (home / "g").mkdir()
    stale = home / "g" / "z.lock"; stale.write_text("x")
    os.utime(stale, (time.time() - 25200,) * 2)
    out = RA.run_local_repairs(_ctx(home, now=time.time()), dry_run=True)
    assert any(o.status == "would_apply" for o in out)
    assert stale.exists()  # dry-run changed nothing


def test_partial_fix_is_kept_not_reverted(tmp_path, monkeypatch):
    # Progress-based loss gate: an action that clears SOME faults must keep them,
    # not roll back everything because one fault remains (review HIGH fix).
    state = {"faults": ["a", "b"], "undone": False}

    class _Partial(RA.RepairAction):
        action_id = "partial_test"
        def precondition(self, ctx): return list(state["faults"])
        def apply(self, ctx, faults):
            state["faults"] = ["b"]      # cleared 'a', 'b' remains
            return 1
        def undo(self, ctx): state["undone"] = True

    monkeypatch.setitem(RA._REGISTRY, "partial_test", _Partial())
    out = {o.action_id: o for o in RA.run_local_repairs(_ctx(tmp_path))}
    RA._REGISTRY.pop("partial_test", None)
    assert out["partial_test"].status == "applied"   # progress made → kept
    assert state["undone"] is False
    assert state["faults"] == ["b"]                  # the partial fix stuck


def test_live_owner_lock_not_swept(tmp_path):
    home = tmp_path
    (home / "g").mkdir()
    lk = home / "g" / "live.lock"
    lk.write_text(str(os.getpid()), encoding="utf-8")   # owned by THIS live process
    os.utime(lk, (time.time() - 99999,) * 2)            # old, but owner alive
    RA.run_local_repairs(_ctx(home, now=time.time()))
    assert lk.exists()                                   # live owner → never swept


# ── M2 risky actions ──────────────────────────────────────────────────────────

def test_corrupt_config_reset_risky(tmp_path, monkeypatch):
    home = tmp_path
    (home / "c").mkdir()
    bad = home / "c" / "engine.config.json"
    bad.write_text("{ this is not json ", encoding="utf-8")
    good = home / "c" / "ok.config.json"
    good.write_text('{"a":1}', encoding="utf-8")
    # risky OFF → untouched
    RA.run_local_repairs(_ctx(home))
    assert bad.read_text().startswith("{ this")
    # risky ON → backed up + reset; valid config untouched
    monkeypatch.setenv("CORVIN_ACO_L5_RISKY", "1")
    RA.run_local_repairs(_ctx(home))
    assert bad.read_text() == "{}\n"
    assert (home / "c" / "engine.config.json.corrupt").exists()
    assert good.read_text() == '{"a":1}'


def test_corrupt_config_reset_never_touches_forbidden(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_ACO_L5_RISKY", "1")
    home = tmp_path
    (home / "g").mkdir()
    # audit.jsonl is not *.config.json and must never be a candidate
    audit = home / "g" / "audit.jsonl"
    audit.write_text("not-json-hash-chain", encoding="utf-8")
    RA.run_local_repairs(_ctx(home))
    assert audit.read_text() == "not-json-hash-chain"


def test_stale_running_task_reset(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_ACO_L5_RISKY", "1")
    home = tmp_path
    td = home / "tenants" / "_default" / "sessions" / "web_x" / "tasks"
    td.mkdir(parents=True)
    dead = td / "dead.json"
    dead.write_text('{"status":"running","pid":2147483646}', encoding="utf-8")
    live = td / "live.json"
    live.write_text(f'{{"status":"running","pid":{os.getpid()}}}', encoding="utf-8")
    RA.run_local_repairs(_ctx(home))
    import json as _j
    assert _j.loads(dead.read_text())["status"] == "failed"      # dead pid → failed
    assert _j.loads(live.read_text())["status"] == "running"     # live pid → untouched


# ── VoiceTtsPinnedProviderReset ───────────────────────────────────────────────

def test_voice_tts_pinned_provider_reset_clears_unusable_provider(tmp_path, monkeypatch):
    """When tts_provider is pinned to an unusable provider, the action resets it to None."""
    import json as _j
    import sys as _sys

    # Build a minimal stub profile module pointing at tmp_path/profile.json.
    profile_path = tmp_path / "profile.json"
    profile_path.write_text('{"tts_provider": "openai"}', encoding="utf-8")

    _cache: dict = {}

    class _FakeProfileModule:
        def load(self, force=False):
            return _j.loads(profile_path.read_text())
        def save(self, data):
            profile_path.write_text(_j.dumps(data), encoding="utf-8")
        def set_value(self, key, value):
            # Mirrors the real profile.py's single-key contract closely
            # enough for this test double: VoiceTtsPinnedProviderReset now
            # routes its writes through set_value() (2026-07-13 fix) so it
            # shares profile.py's real _write_lock instead of calling
            # load()/save() unlocked.
            d = self.load(force=True)
            if value is None:
                d.pop(key, None)
            else:
                d[key] = value
            self.save(d)
            return d

    fake_mod = _FakeProfileModule()

    action = RA.VoiceTtsPinnedProviderReset()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)  # ensure OpenAI is "broken"

    # Patch _load_profile_module to return our fake module.
    monkeypatch.setattr(action, "_load_profile_module", lambda ctx: fake_mod)

    ctx = _ctx(tmp_path)
    faults = action.precondition(ctx)
    assert faults == ["openai"], "should detect unusable pinned OpenAI provider"

    n = action.apply(ctx, faults)
    assert n == 1
    saved = _j.loads(profile_path.read_text())
    assert saved.get("tts_provider") is None, "tts_provider should be cleared"

    # undo restores the previous value
    action.undo(ctx)
    restored = _j.loads(profile_path.read_text())
    assert restored.get("tts_provider") == "openai"


def test_voice_tts_pinned_provider_reset_ignores_auto(tmp_path, monkeypatch):
    """Action must not fire when tts_provider is 'auto' or absent."""
    import json as _j

    action = RA.VoiceTtsPinnedProviderReset()

    for value in (None, "auto"):
        profile_path = tmp_path / "profile.json"
        data = {}
        if value is not None:
            data["tts_provider"] = value
        profile_path.write_text(_j.dumps(data), encoding="utf-8")

        class _FakeMod:
            def load(self):
                return _j.loads(profile_path.read_text())
            def save(self, d):
                profile_path.write_text(_j.dumps(d), encoding="utf-8")

        monkeypatch.setattr(action, "_load_profile_module", lambda ctx, m=_FakeMod(): m)
        ctx = _ctx(tmp_path)
        faults = action.precondition(ctx)
        assert faults == [], f"should not fire for tts_provider={value!r}"


def test_voice_tts_pinned_provider_reset_skips_usable_provider(tmp_path, monkeypatch):
    """Action must not fire when the pinned provider is actually usable (key present)."""
    import json as _j

    profile_path = tmp_path / "profile.json"
    profile_path.write_text('{"tts_provider": "openai"}', encoding="utf-8")

    class _FakeMod:
        def load(self):
            return _j.loads(profile_path.read_text())

    action = RA.VoiceTtsPinnedProviderReset()
    monkeypatch.setattr(action, "_load_profile_module", lambda ctx: _FakeMod())
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")  # provider appears usable

    ctx = _ctx(tmp_path)
    faults = action.precondition(ctx)
    assert faults == [], "should not fire when OpenAI key is present"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
