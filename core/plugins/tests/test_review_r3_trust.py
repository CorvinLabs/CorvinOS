"""Review 2026-11-19 round 3 - plugin trust.

* R3-NEW-19: a consent is bound to the digest of what was approved; a ``vetted`` claim in
  registry.yaml that the class file's location contradicts is downgraded on read.
* R3-NEW-39: ``plugin_consent.json`` is written under a lock, atomically, mode 0600.
* R3-NEW-34: the marketplace tarball is extracted with the ``data`` filter and a size limit.
"""
from __future__ import annotations

import io
import json
import os
import stat
import sys
import tarfile
import textwrap
import threading
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "core" / "plugins",):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from corvin_plugins import bootstrap, trust  # noqa: E402
from corvin_plugins.trust import Verdict  # noqa: E402


def _record(**over) -> dict:
    rec = {"plugin_id": "com.example.r", "version": "1.0.0", "origin": "community",
           "class_path": "pkg.mod:Cls"}
    rec.update(over)
    return rec


# ── R3-NEW-19: digest ───────────────────────────────────────────────────────

def test_consent_bound_to_a_digest_covers_only_that_build(tmp_path):
    approved = _record()
    trust.grant_consent("com.example.r", corvin_home=tmp_path, operator="alice",
                        digest=trust.record_digest(approved))
    assert trust.evaluate(approved, corvin_home=tmp_path, enforcement=True).allowed
    # same id, another build: new version / another class
    for changed in (_record(version="1.0.1"), _record(class_path="pkg.other:Evil")):
        d = trust.evaluate(changed, corvin_home=tmp_path, enforcement=True)
        assert d.verdict is Verdict.COMMUNITY and d.refused, changed


def test_a_stored_digest_is_never_satisfied_by_no_digest(tmp_path):
    trust.grant_consent("com.example.r", corvin_home=tmp_path, operator="alice", digest="abc")
    assert trust.consent_granted("com.example.r", corvin_home=tmp_path, digest="abc")
    assert not trust.consent_granted("com.example.r", corvin_home=tmp_path, digest="abd")
    assert not trust.consent_granted("com.example.r", corvin_home=tmp_path)


def test_a_consent_without_a_digest_still_approves_the_plugin_id(tmp_path):
    trust.grant_consent("com.example.r", corvin_home=tmp_path, operator="alice")
    assert trust.consent_granted("com.example.r", corvin_home=tmp_path)
    assert trust.evaluate(_record(version="9.9.9"), corvin_home=tmp_path, enforcement=True).allowed


def test_record_digest_ignores_the_signature_and_is_stable():
    a = trust.record_digest(_record())
    assert a == trust.record_digest(dict(reversed(list(_record().items()))))
    assert a != trust.record_digest(_record(version="2.0.0"))
    assert len(a) == 64


# ── R3-NEW-39: the consent file ─────────────────────────────────────────────

def test_consent_file_is_0600_and_leaves_no_temp_file(tmp_path):
    trust.grant_consent("com.example.r", corvin_home=tmp_path, operator="alice")
    path = trust._consent_path(tmp_path, "_default")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [p.name for p in path.parent.iterdir() if p.name.endswith(".tmp")] == []


def test_concurrent_grants_keep_every_approval(tmp_path):
    ids = [f"com.example.p{i}" for i in range(32)]
    errors: list[BaseException] = []

    def grant(pid: str) -> None:
        try:
            trust.grant_consent(pid, corvin_home=tmp_path, operator="alice")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=grant, args=(pid,)) for pid in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    data = json.loads(trust._consent_path(tmp_path, "_default").read_text())
    assert sorted(data["approved"]) == sorted(ids)


def test_a_planted_symlink_is_replaced_not_followed(tmp_path):
    path = trust._consent_path(tmp_path, "_default")
    path.parent.mkdir(parents=True)
    victim = tmp_path / "victim.json"
    victim.write_text(json.dumps({"approved": {"com.evil": {"approved": True}}}))
    path.symlink_to(victim)
    assert not trust.consent_granted("com.evil", corvin_home=tmp_path)   # not read through
    trust.grant_consent("com.example.r", corvin_home=tmp_path, operator="alice")
    assert not path.is_symlink()
    assert json.loads(victim.read_text()) == {"approved": {"com.evil": {"approved": True}}}
    assert not trust.consent_granted("com.evil", corvin_home=tmp_path)
    assert trust.consent_granted("com.example.r", corvin_home=tmp_path)


# ── R3-NEW-19: a contradicted ``vetted`` claim ──────────────────────────────

def _registry_with(home: Path, origin: str, class_path: str) -> None:
    reg_dir = home / "tenants" / "_default" / "plugins"
    reg_dir.mkdir(parents=True, exist_ok=True)
    (reg_dir / "registry.yaml").write_text(textwrap.dedent(f"""\
        spec:
          schema_version: "1.0"
        plugins:
          r3claim:
            plugin_id: r3claim
            version: "1.0.0"
            display_name: r3claim
            plugin_type: audit_backend
            origin: {origin}
            boot_layer: installed
            enabled: false
            class_path: "{class_path}"
        """), encoding="utf-8")


@pytest.fixture()
def private_plugin(tmp_path, monkeypatch):
    """A class file inside a private plugin root: community by LOCATION."""
    home = tmp_path / "home"
    (home / "tenants" / "_default").mkdir(parents=True)
    root = tmp_path / "private" / "plugins"
    pkg = root / "buildin" / "misc" / "r3claimpkg"
    pkg.mkdir(parents=True)
    (pkg / "r3claim_mod.py").write_text("class Cls:\n    pass\n", encoding="utf-8")
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.setenv("CORVIN_PRIVATE_PLUGIN_ROOTS", str(root))
    monkeypatch.syspath_prepend(str(pkg))
    assert bootstrap._origin_for_class_path("r3claim_mod:Cls")[0] == "community"
    return home


def test_a_vetted_claim_the_location_contradicts_needs_consent(private_plugin):
    from corvin_plugins.state import TenantRegistry

    _registry_with(private_plugin, "vetted", "r3claim_mod:Cls")
    rec = TenantRegistry.load(tenant_id="_default").records["r3claim"]
    assert rec.origin.value == "community"
    assert rec.consent_required() is True


def test_positive_control_an_uncontradicted_vetted_claim_is_left_alone(private_plugin, tmp_path,
                                                                     monkeypatch):
    from corvin_plugins.state import TenantRegistry

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "r3other_mod.py").write_text("class Cls:\n    pass\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(elsewhere))
    _registry_with(private_plugin, "vetted", "r3other_mod:Cls")
    rec = TenantRegistry.load(tenant_id="_default").records["r3claim"]
    assert rec.origin.value == "vetted"


# ── R3-NEW-34: tarball extraction ───────────────────────────────────────────

def _tarball(path: Path, members: list[tuple[tarfile.TarInfo, bytes | None]]) -> None:
    with tarfile.open(path, "w:gz") as tf:
        for info, data in members:
            tf.addfile(info, io.BytesIO(data) if data is not None else None)


def _file(name: str, data: bytes = b"x", mode: int = 0o644) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    return info, data


def _dir(name: str) -> tuple[tarfile.TarInfo, None]:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    return info, None


def _good_members() -> list:
    return [_dir("repo-abc"), _dir("repo-abc/plugins"), _dir("repo-abc/plugins/buildin"),
            _file("repo-abc/plugins/buildin/a.py", b"print(1)\n")]


def _extract(tmp_path, monkeypatch, members) -> Path:
    src = tmp_path / "src.tar.gz"
    _tarball(src, members)
    monkeypatch.setattr(bootstrap, "_fetch_marketplace_tarball",
                        lambda target: Path(target).write_bytes(src.read_bytes()))
    dest = tmp_path / "cache" / "Corvin-Marketplace"
    bootstrap._download_and_extract_marketplace(dest)
    return dest


def test_positive_control_a_plain_tree_is_extracted(tmp_path, monkeypatch):
    dest = _extract(tmp_path, monkeypatch, _good_members())
    assert (dest / "plugins" / "buildin" / "a.py").read_text() == "print(1)\n"


def test_setuid_bits_do_not_survive_extraction(tmp_path, monkeypatch):
    if not hasattr(tarfile, "data_filter"):
        pytest.skip("this interpreter has no extraction filters")
    dest = _extract(tmp_path, monkeypatch,
                    _good_members() + [_file("repo-abc/plugins/buildin/tool", b"#!/bin/sh\n", 0o4777)])
    mode = stat.S_IMODE((dest / "plugins" / "buildin" / "tool").stat().st_mode)
    assert not mode & (stat.S_ISUID | stat.S_ISGID) and not mode & 0o022


def test_special_members_are_refused_and_the_cache_is_untouched(tmp_path, monkeypatch):
    fifo = tarfile.TarInfo("repo-abc/plugins/buildin/pipe")
    fifo.type = tarfile.FIFOTYPE
    with pytest.raises(ValueError, match="Unsupported member type"):
        _extract(tmp_path, monkeypatch, _good_members() + [(fifo, None)])
    assert not (tmp_path / "cache" / "Corvin-Marketplace").exists()


def test_a_tarball_that_unpacks_beyond_the_limit_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "_MARKETPLACE_MAX_UNPACKED_BYTES", 1024)
    with pytest.raises(ValueError, match="size limit"):
        _extract(tmp_path, monkeypatch,
                 _good_members() + [_file("repo-abc/plugins/buildin/big.bin", b"\0" * 4096)])
    assert not (tmp_path / "cache" / "Corvin-Marketplace").exists()


def test_too_many_members_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "_MARKETPLACE_MAX_MEMBERS", 5)
    extra = [_file(f"repo-abc/plugins/buildin/f{i}.py") for i in range(6)]
    with pytest.raises(ValueError, match="too many members"):
        _extract(tmp_path, monkeypatch, _good_members() + extra)


def test_a_link_leaving_the_tree_is_refused(tmp_path, monkeypatch):
    link = tarfile.TarInfo("repo-abc/plugins/buildin/escape")
    link.type = tarfile.SYMTYPE
    link.linkname = "../../../../etc/passwd"
    with pytest.raises(Exception):
        _extract(tmp_path, monkeypatch, _good_members() + [(link, None)])
