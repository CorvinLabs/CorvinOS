"""Owner lifecycle and the encrypted store (AC-S1..S3, S8, S10, AC-O1..O10, O13, O17, O19, O20)."""
from __future__ import annotations

import json
import multiprocessing
import os
import secrets
from pathlib import Path

import pytest

from core.personal_kb import audit, capabilities, crypto, limits, model, owner, paths, session
from core.personal_kb.store import EpochChanged, PersonalStore, QuotaExceeded, Revoked, VersionConflict

from .conftest import PW, PW2, chain_records, verify_chain

T = "_default"


def _new(handle="anna", mode="convenient"):
    c = owner.create_owner(T, handle, PW, consent=True, unlock_mode=mode)
    return c, PersonalStore.open(T, c.owner_id, c.keys)


def _all_bytes(d: Path) -> bytes:
    out = b""
    for p in d.rglob("*"):
        if p.is_file():
            out += p.read_bytes()
    return out


def test_ciphertext_only_on_disk_with_positive_control(home):
    c, s = _new()
    marker = "MARK" + secrets.token_hex(6)
    s.put(model.new_entity("note", f"secret {marker}", body=f"body {marker}"))
    d = paths.owner_dir(T, c.owner_id)
    control = d / "control.txt"
    control.write_text(marker)  # positive control: the same scan must find a planted marker
    assert marker.encode() in _all_bytes(d)
    control.unlink()
    assert marker.encode() not in _all_bytes(d)
    assert marker.encode() not in _all_bytes(paths.root(T))
    assert all(marker not in json.dumps(r) for r in chain_records(home))
    assert verify_chain(home)


def test_unlock_paths_and_identical_failures(home):
    c, _ = _new()
    oid, keys = owner.unlock_with_passphrase(T, "anna", PW, session_key="s1")
    assert oid == c.owner_id and keys.dek == c.keys.dek
    with pytest.raises(owner.UnlockFailed) as e1:
        owner.unlock_with_passphrase(T, "nobody", PW, session_key="s2")
    with pytest.raises(owner.UnlockFailed) as e2:
        owner.unlock_with_passphrase(T, "anna", PW2, session_key="s3")
    assert str(e1.value) == str(e2.value)
    k2 = owner.unlock_with_device(T, c.owner_id, surface="discord")
    assert k2.dek == c.keys.dek


def test_no_lockout_only_bounded_delay(home):
    _new()
    now = 1_000_000.0
    for i in range(8):
        with pytest.raises((owner.UnlockFailed, limits.TooSoon)):
            owner.unlock_with_passphrase(T, "anna", PW2, session_key=f"x{i}", now=now)
        now += 31  # past the 30 s cap each time
    oid, _ = owner.unlock_with_passphrase(T, "anna", PW, session_key="fresh", now=now)
    assert oid
    data = json.loads(paths.lockouts_file(T).read_text()) if paths.lockouts_file(T).exists() else {}
    assert all("anna" not in k for k in data)  # handle stored only as HMAC


def test_unknown_handle_spends_no_scrypt(home, monkeypatch):
    _new()
    called = []
    monkeypatch.setattr(crypto, "kek_from_passphrase", lambda *a, **k: called.append(1) or b"x" * 32)
    with pytest.raises(owner.UnlockFailed):
        owner.unlock_with_passphrase(T, "ghost", PW, session_key="q")
    assert called == []


def test_weak_passphrase_and_consent_and_handle(home):
    for kw, msg in (({"passphrase": "short"}, "characters"), ({"consent": False}, "consent")):
        args = {"handle": "bob", "passphrase": PW, "consent": True}
        args.update(kw)
        with pytest.raises(owner.OwnerError, match=msg):
            owner.create_owner(T, args["handle"], args["passphrase"], consent=args["consent"])
    _new("bob")
    with pytest.raises(owner.OwnerError, match="taken"):
        owner.create_owner(T, "bob", PW2, consent=True)


def test_strict_mode_has_no_device_key_and_nothing_on_disk_decrypts(home):
    c, s = _new("carla", mode="strict")
    s.put(model.new_entity("note", "private"))
    assert not paths.device_key_files(T, c.owner_id)
    with pytest.raises(owner.UnlockFailed):
        owner.unlock_with_device(T, c.owner_id, surface="discord")
    # Try every 32-byte window of every on-disk artifact as a key against the device-free wraps.
    o = json.loads(paths.owner_json(T, c.owner_id).read_text())
    blobs = [p.read_bytes() for p in paths.root(T).rglob("*") if p.is_file()]
    for b in blobs:
        for i in range(0, max(0, len(b) - 31), 16):
            cand = b[i:i + 32]
            for kind in ("pass", "recovery"):
                with pytest.raises(crypto.DecryptError):
                    crypto.unwrap(o["wraps"][kind], cand, owner_id=c.owner_id, epoch=1, kind=kind)


def test_convenient_refused_when_files_cannot_be_protected(home):
    capabilities._set_for_tests({"secure_files": False})
    with pytest.raises(owner.OwnerError, match="strict"):
        owner.create_owner(T, "dora", PW, consent=True, unlock_mode="convenient")
    c = owner.create_owner(T, "dora", PW, consent=True, unlock_mode="strict")
    assert c.owner_id


def test_tamper_swap_delete_and_old_version_replay(home):
    c, s = _new()
    a = s.put(model.new_entity("note", "alpha"))
    b = s.put(model.new_entity("note", "beta"))
    a2 = dict(a, title="alpha two")
    s.put(a2)
    rec_a = s.manifest["objects"][a["uid"]]
    rec_b = s.manifest["objects"][b["uid"]]
    fa = paths.object_file(T, c.owner_id, rec_a["oid"])
    fb = paths.object_file(T, c.owner_id, rec_b["oid"])
    # replay the old version of a over the current one
    old = paths.version_file(T, c.owner_id, rec_a["oid"], 1).read_bytes()
    cur = fa.read_bytes()
    fa.write_bytes(old)
    s2 = PersonalStore.open(T, c.owner_id, c.keys)
    assert s2.problems.get(a["uid"]) == "corrupt" and a["uid"] not in s2.index
    fa.write_bytes(cur)
    # swap b's ciphertext into a's slot
    fa.write_bytes(fb.read_bytes())
    s3 = PersonalStore.open(T, c.owner_id, c.keys)
    assert s3.problems.get(a["uid"]) == "corrupt"
    fa.write_bytes(cur)
    # delete an object: reported missing, never silent
    fb.unlink()
    s4 = PersonalStore.open(T, c.owner_id, c.keys)
    assert s4.problems.get(b["uid"]) == "missing" and s4.get(a["uid"])["title"] == "alpha two"
    # another owner's ciphertext cannot be read either
    c2, t2 = _new("eve")
    e = t2.put(model.new_entity("note", "eve's"))
    fe = paths.object_file(T, c2.owner_id, t2.manifest["objects"][e["uid"]]["oid"])
    fa.write_bytes(fe.read_bytes())
    assert PersonalStore.open(T, c.owner_id, c.keys).problems.get(a["uid"]) == "corrupt"


def test_versions_restore_and_conflict(home):
    _, s = _new()
    e = s.put(model.new_entity("idea", "v1"))
    for i in range(2, 25):
        e = s.put(dict(e, title=f"v{i}"))
    assert len(s.versions(e["uid"])) == 20
    r = s.restore(e["uid"], s.versions(e["uid"])[-1])
    assert r["title"] == "v23" and r["version"] == e["version"] + 1
    with pytest.raises(VersionConflict):
        s.put(dict(r, title="x"), expected_version=1)


def test_quotas_at_write_time(home, monkeypatch):
    from core.personal_kb import store as st

    _, s = _new()
    monkeypatch.setattr(st, "MAX_OBJECTS", 3)
    for i in range(3):
        s.put(model.new_entity("note", f"n{i}"))
    with pytest.raises(QuotaExceeded):
        s.put(model.new_entity("note", "n4"))
    monkeypatch.setattr(st, "MAX_OBJECTS", 100)
    monkeypatch.setattr(st, "MANUAL_ADDS_PER_HOUR", 1)
    s.put(model.new_entity("note", "m1"), manual=True)
    with pytest.raises(QuotaExceeded):
        s.put(model.new_entity("note", "m2"), manual=True)
    monkeypatch.setattr(st, "MAX_PLAINTEXT_BYTES", 10)
    with pytest.raises(QuotaExceeded):
        s.put(model.new_entity("note", "big"))


def test_export_in_memory_no_file(home):
    c, s = _new()
    s.put(model.new_entity("note", "exported"))
    before = {p for p in paths.root(T).rglob("*")}
    data = s.export_bytes()
    assert data[:2] == b"PK"
    assert {p for p in paths.root(T).rglob("*")} == before


def test_crash_between_object_and_manifest_is_repaired_or_reported(home, monkeypatch):
    from core.personal_kb import store as st

    c, s = _new()
    e = s.put(model.new_entity("note", "one"))
    real = st.PersonalStore._write_manifest
    monkeypatch.setattr(st.PersonalStore, "_write_manifest", lambda self: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        s.put(dict(e, title="two"))
    monkeypatch.setattr(st.PersonalStore, "_write_manifest", real)
    assert any(r.get("event_type", r.get("event")) == "personal_kb.write_failed" for r in chain_records(home))
    s2 = PersonalStore.open(T, c.owner_id, c.keys)
    # the object one version ahead of the manifest is adopted (ADR-2325 item 4), never shown as the old version
    assert s2.problems == {} and s2.get(e["uid"])["title"] == "two"


def test_rekey_destroys_old_secrets_and_keeps_data(home):
    c, s = _new()
    e = s.put(model.new_entity("note", "survives rekey"))
    s.forget(s.put(model.new_entity("preference", "brief", key="verbosity"))["uid"], fingerprint="verbosity brief")
    old_dev = paths.device_key_files(T, c.owner_id)[0].read_bytes()
    new, code = owner.rekey(T, c.owner_id, c.keys, PW)
    assert new.epoch == 2 and not new.prev
    o = json.loads(paths.owner_json(T, c.owner_id).read_text())
    assert "prev" not in o and o["key_epoch"] == 2
    assert paths.device_key_files(T, c.owner_id)[0].read_bytes() != old_dev
    with pytest.raises(owner.UnlockFailed):
        owner.unlock_with_recovery(T, "anna", c.recovery_code, PW2, session_key="r1", now=1e9)
    oid, keys = owner.unlock_with_recovery(T, "anna", code, PW2, session_key="r2", now=1e9 + 31)
    s2 = PersonalStore.open(T, oid, keys)
    assert s2.get(e["uid"])["title"] == "survives rekey"
    assert s2.is_forgotten("brief verbosity")
    # a writer holding the old keyring is refused
    with pytest.raises(EpochChanged):
        s.put(model.new_entity("note", "stale writer"))


def test_rekey_interrupted_resumes_at_next_unlock(home, monkeypatch):
    from core.personal_kb import store as st

    c, s = _new()
    ents = [s.put(model.new_entity("note", f"n{i}")) for i in range(5)]
    with monkeypatch.context() as mp:
        mp.setattr(owner, "resume_rekey_if_pending", lambda *a, **k: False)
        owner.rekey(T, c.owner_id, c.keys, PW)  # crash simulated: the sweep never ran
    o = json.loads(paths.owner_json(T, c.owner_id).read_text())
    assert o.get("prev")
    oid, keys = owner.unlock_with_passphrase(T, "anna", PW, session_key="k")
    assert "prev" not in json.loads(paths.owner_json(T, c.owner_id).read_text())
    s2 = PersonalStore.open(T, oid, keys)
    assert {e["uid"] for e in ents} <= set(s2.index)
    for f in paths.owner_dir(T, oid).rglob("*.enc"):
        assert crypto.blob_epoch(f.read_bytes()) == 2


def _writer(home_dir, owner_id, dek_hex, n, q):
    import sys
    os.environ["CORVIN_HOME"] = home_dir
    from core.personal_kb import model as m
    from core.personal_kb.store import Keyring, PersonalStore as PS
    s = PS.open("_default", owner_id, Keyring(epoch=1, dek=bytes.fromhex(dek_hex)))
    for i in range(n):
        s.put(m.new_entity("note", f"w{os.getpid()}-{i}"))
    q.put("ok")


def test_two_processes_lose_no_update(home):
    c, s = _new()
    ctx = multiprocessing.get_context("spawn")
    q = ctx.Queue()
    procs = [ctx.Process(target=_writer, args=(str(home), c.owner_id, c.keys.dek.hex(), 15, q)) for _ in range(2)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
        assert p.exitcode == 0
    s.reload_if_changed()
    s2 = PersonalStore.open(T, c.owner_id, c.keys)
    assert len(s2.index) == 30 and not s2.problems
    assert verify_chain(home)


def test_audit_records_are_content_free(home):
    c, s = _new()
    s.put(model.new_entity("note", "Titel mit Inhalt"))
    for r in chain_records(home):
        d = json.dumps(r)
        assert "Titel" not in d and c.owner_id not in d and "anna" not in d
    assert any(audit.owner_hash(c.owner_id) in json.dumps(r) for r in chain_records(home))
