"""T-0390 / ADR-2322 - the pure listing module ``core/projects/browse.py`` (directories only, flags, pagination, verdict = the
location rule, picker store).

Every test runs in its own temp dir that is the OWN home (``HOME``, ``XDG_DATA_HOME``) and holds ``CORVIN_HOME`` with two tenants
(``_default`` and ``acme``), so the real install is never read or written. Three OS fixtures (linux, darwin, win32 - the last one
in its pure Windows path flavour on a POSIX host) exercise ``places``; the junction and UNC cases use the win32 fixture with the
link helper / ``os.name`` of the registry module patched, the real cases run on the Windows VM (T-0394).
"""
from __future__ import annotations

import json
import os
import stat
import sys
import types
from pathlib import Path

import pytest

from core.paths import user_dirs as U
from core.projects import browse as B
from core.projects import manifest, registry

POSIX = os.name != "nt"


# ── fixtures ───────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def sb(tmp_path, monkeypatch):
    """The temp dir is the own home; CORVIN_HOME with two tenants inside it."""
    home = tmp_path / "h"
    home.mkdir()
    ch = home / "corvin_home"
    for t in ("_default", "acme"):
        (ch / "tenants" / t).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "data"))
    for k in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CORVIN_HOME", str(ch))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    ns = types.SimpleNamespace(home=home, ch=ch, tmp=tmp_path)
    return ns


def _plant_project(d: Path, pid: str, tenant: str) -> None:
    d.mkdir(parents=True, exist_ok=True)
    manifest.write(d, {"schema": manifest.SCHEMA, "id": pid, "title": pid.title(), "tenant": tenant,
                       "template": "adopted@1", "docs_mode": "plain", "features": {}})


def _register(sb, tenant: str, pid: str, path: Path) -> None:
    reg = sb.ch / "tenants" / tenant / "projects" / "registry.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(reg.read_text()) if reg.exists() else {"schema": 1, "projects": []}
    data["projects"].append({"id": pid, "path": str(path), "template": "adopted@1", "added_at": "2026-11-10T00:00:00Z"})
    reg.write_text(json.dumps(data))


@pytest.fixture
def tree(sb):
    """work/ with a file, a hidden dir, a link to a dir, an unreadable dir, a foreign project, an own project and plain dirs."""
    w = sb.home / "work"
    (w / "alpha" / "inner").mkdir(parents=True)
    (w / "Beta").mkdir()
    (w / ".hidden-dir").mkdir()
    (w / "secret-file-name.txt").write_text("x")
    (w / "target" / "child-of-target").mkdir(parents=True)
    os.symlink(w / "target", w / "linked")
    os.symlink(w / "secret-file-name.txt", w / "file-link-name")
    (w / "locked").mkdir()
    (w / "locked" / "under-locked").mkdir()
    _plant_project(w / "theirs", "theirs", "acme")
    (w / "theirs" / "foreign-child").mkdir()
    _register(sb, "acme", "theirs", w / "theirs")
    _plant_project(w / "mine", "mine", "_default")
    _register(sb, "_default", "mine", w / "mine")
    if POSIX:
        os.chmod(w / "locked", 0)
    yield w
    if POSIX:
        os.chmod(w / "locked", 0o700)


def _names(lst) -> list[str]:
    return [e["name"] for e in lst["entries"]]


def _by_name(lst) -> dict:
    return {e["name"]: e for e in lst["entries"]}


# ── AC1 directories only, flags ────────────────────────────────────────────────────────────────

def test_listing_returns_directories_only_with_flags(sb, tree):
    lst = B.listing("_default", str(tree), "create")
    names = _names(lst)
    assert names == sorted(names, key=lambda n: (n.casefold(), n))
    assert set(names) == {"alpha", "Beta", "target", "linked", "locked", "theirs", "mine"}, names
    dump = json.dumps(lst)
    assert "secret-file-name" not in dump and "file-link-name" not in dump, "a file name leaked into the listing"
    assert "child-of-target" not in dump and "foreign-child" not in dump and "inner" not in dump
    e = _by_name(lst)
    assert e["linked"]["symlink"] is True and e["linked"]["refusal"] == "symlink"
    assert e["alpha"]["symlink"] is False and "refusal" not in e["alpha"]
    assert e["alpha"]["kind"] == "dir" and e["alpha"]["path"] == str(tree / "alpha")
    if POSIX and os.geteuid() != 0:
        assert e["locked"]["readable"] is False
    assert e["alpha"]["readable"] is True
    assert e["theirs"]["project"] == {"state": "other"}, "a foreign project is existence only"
    assert e["theirs"]["refusal"] == "other_tenant"
    assert e["mine"]["project"] == {"state": "mine", "id": "mine", "title": "Mine"}
    assert e["alpha"]["project"] == {"state": "none"}
    assert lst["truncated"] is False and "next_page" not in lst
    assert lst["parent"] == str(tree.parent)
    assert lst["display"] == "~/work"
    assert lst["breadcrumbs"][-1] == {"label": "work", "path": str(tree)}


def test_hidden_entries_only_with_hidden(sb, tree):
    assert ".hidden-dir" not in _names(B.listing("_default", str(tree), "create"))
    lst = B.listing("_default", str(tree), "create", hidden=True)
    e = _by_name(lst)[".hidden-dir"]
    assert e["hidden"] is True and e["refusal"] == "hidden"


def test_an_unreadable_folder_answers_with_no_entries(sb, tree):
    if not POSIX or os.geteuid() == 0:
        pytest.skip("POSIX permissions as a non-root user")
    lst = B.listing("_default", str(tree / "locked"), "adopt")
    assert lst["entries"] == [] and lst["readable"] is False
    assert "under-locked" not in json.dumps(lst)


# ── AC2 CORVIN_HOME and system prefixes ──────────────────────────────────────────────────────────

def test_corvin_home_is_a_row_that_cannot_be_entered(sb, tree):
    (sb.ch / "tenants" / "_default" / "planted-child").mkdir()
    lst = B.listing("_default", str(sb.home), "create")
    assert _by_name(lst)["corvin_home"]["refusal"] == "corvin_home"
    for p in (sb.ch, sb.ch / "tenants", sb.ch / "tenants" / "_default"):
        inner = B.listing("_default", str(p), "create")
        assert inner["entries"] == []
        assert inner["verdict"] == {"ok": False, "reason_class": "corvin_home", "message": registry.SENTENCES["corvin_home"]}
        assert "planted-child" not in json.dumps(inner)


@pytest.mark.skipif(not POSIX, reason="POSIX prefixes")
def test_system_prefixes_are_not_entered():
    for p in ("/etc", "/usr/share", "/proc", "/var/lib"):
        lst = B.listing("_default", p, "create")
        assert lst["entries"] == [], p
        assert lst["verdict"]["reason_class"] == "system_prefix", p
        assert lst["verdict"]["message"] == registry.SENTENCES["system_prefix"]


@pytest.mark.skipif(not POSIX, reason="POSIX prefixes")
def test_root_lists_system_prefixes_as_refused_rows(sb):
    lst = B.listing("_default", "/", "projects_root")
    e = _by_name(lst)
    assert e["etc"]["refusal"] == "system_prefix"
    assert e["usr"]["refusal"] == "system_prefix"


# ── AC3 a linked ancestor ───────────────────────────────────────────────────────────────────────

def test_a_linked_ancestor_answers_symlink(sb, tree):
    lst = B.listing("_default", str(tree / "linked"), "create")
    assert lst["entries"] == []
    assert lst["verdict"]["reason_class"] == "symlink"
    assert "child-of-target" not in json.dumps(lst)
    deeper = B.listing("_default", str(tree / "linked" / "child-of-target"), "adopt")
    assert deeper["entries"] == [] and deeper["verdict"]["reason_class"] == "symlink"


# ── AC4 pagination ──────────────────────────────────────────────────────────────────────────────

def test_250_directories_paginate_at_200(sb):
    d = sb.home / "many"
    d.mkdir()
    names = [f"D{i:03d}" if i % 2 else f"d{i:03d}" for i in range(250)]
    for n in names:
        (d / n).mkdir()
    first = B.listing("_default", str(d), "projects_root")
    assert len(first["entries"]) == 200 and first["truncated"] is True
    want = sorted(names, key=lambda n: (n.casefold(), n))
    assert _names(first) == want[:200]
    assert first["next_page"] == want[199]
    second = B.listing("_default", str(d), "projects_root", page=first["next_page"])
    assert _names(second) == want[200:]
    assert second["truncated"] is False and "next_page" not in second
    assert B.listing("_default", str(d), "projects_root") == first, "stable order"


# ── AC5 verdict = validate_location ─────────────────────────────────────────────────────────────

def _rule(tenant, path, purpose, project_id=None):
    try:
        registry.validate_location(tenant, path, purpose=purpose, project_id=project_id)
        return True, None
    except registry.ProjectPathError as exc:
        return False, exc.reason_class


def test_verdict_equals_validate_location_for_every_planted_case(sb, tree):
    (sb.home / "empty").mkdir()
    (sb.home / ".dot" / "x").mkdir(parents=True)
    (sb.home / "repo" / ".git" / "sub").mkdir(parents=True)
    cases = [tree, tree / "alpha", tree / "alpha" / "new", tree / "linked", tree / "linked" / "child-of-target",
             tree / "theirs", tree / "theirs" / "foreign-child", tree / "mine", tree / "mine" / "sub",
             sb.home / "empty", sb.home / "absent", sb.home / ".dot" / "x", sb.home / "repo" / ".git" / "sub",
             sb.ch / "tenants", sb.home, Path("/etc/ssh") if POSIX else sb.home, tree / "secret-file-name.txt"]
    seen = set()
    for p in cases:
        for purpose in registry.PURPOSES:
            pid = "mine" if purpose in ("move", "relocate") else None
            v = B.verdict("_default", str(p), purpose, pid)
            ok, cls = _rule("_default", str(p), purpose, pid)
            assert v["ok"] is ok, (p, purpose, v)
            assert v.get("reason_class") == cls, (p, purpose, v, cls)
            if not ok:
                assert v["message"]
                seen.add(cls)
    assert {"symlink", "corvin_home", "other_tenant", "nested", "hidden", "protected", "not_empty", "missing",
            "top_level"} <= seen, seen


def test_listing_verdict_equals_the_rule_where_the_listing_is_not_blocked(sb, tree):
    (sb.home / "empty").mkdir()
    for p in (tree, tree / "alpha", tree / "mine", sb.home / "empty"):
        for purpose in registry.PURPOSES:
            pid = "mine" if purpose in ("move", "relocate") else None
            lst = B.listing("_default", str(p), purpose, pid)
            ok, cls = _rule("_default", str(p), purpose, pid)
            assert (lst["verdict"]["ok"], lst["verdict"].get("reason_class")) == (ok, cls), (p, purpose)


def test_the_own_managed_root_is_enterable_and_answers_workspace_root(sb):
    root, _o = registry.ensure_managed_root("_default")
    (root / "proj-a").mkdir()
    (root / ".trash").mkdir()
    lst = B.listing("_default", str(root), "create", hidden=True)
    assert _names(lst) == ["proj-a"], "hidden entries never under the managed place"
    assert lst["verdict"]["reason_class"] == "workspace_root"


# ── AC6 picker store ────────────────────────────────────────────────────────────────────────────

def test_picker_store_recent_favourites_and_mode(sb):
    for i in range(10):
        B.record_recent("_default", str(sb.home / f"r{i}"))
    B.record_recent("_default", str(sb.home / "r3"))
    st = B.load_picker("_default")
    assert len(st["recent"]) == 8
    assert st["recent"][0] == str(sb.home / "r3")
    assert len(set(st["recent"])) == 8, "deduplicated"
    favs = [str(sb.home / f"f{i}") for i in range(10)]
    B.set_favourites("_default", favs)
    assert B.load_picker("_default")["favourites"] == favs
    with pytest.raises(B.BrowseInputError) as ei:
        B.set_favourites("_default", favs + [str(sb.home / "f10")])
    assert str(ei.value) == "At most 10 favourites."
    with pytest.raises(B.BrowseInputError):
        B.set_favourites("_default", ["relative/x"])
    B.clear_recent("_default")
    assert B.load_picker("_default")["recent"] == []
    assert B.load_picker("_default")["favourites"] == favs
    f = B.picker_path("_default")
    assert f == sb.ch / "tenants" / "_default" / "projects" / "picker.json"
    if POSIX:
        assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert B.load_picker("acme") == {"recent": [], "favourites": []}, "per tenant"


def test_places_carry_managed_root_user_places_recent_and_favourites(sb):
    root, _o = registry.ensure_managed_root("_default")
    (sb.home / "Documents").mkdir()
    B.record_recent("_default", str(sb.home / "Documents"))
    B.set_favourites("_default", [str(sb.home)])
    pl = B.places("_default")
    kinds = [p["kind"] for p in pl]
    assert kinds[0] == "managed" and pl[0]["path"] == str(root) and pl[0]["label"] == "Corvin workspace"
    assert "home" in kinds and "documents" in kinds and "recent" in kinds and "favourite" in kinds
    assert "root" not in kinds
    registry.set_projects_root("_default", str(sb.home / "projects"))
    assert "root" in [p["kind"] for p in B.places("_default")]


@pytest.mark.parametrize("fx", ["linux", "darwin", "win32"])
def test_places_on_three_os_fixtures(sb, monkeypatch, fx):
    monkeypatch.setattr(sys, "platform", fx)
    if fx == "win32":
        monkeypatch.setenv("USERPROFILE", r"C:\Users\u")
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\u\AppData\Local")
        monkeypatch.setattr(U, "_fixed_drive_letters", lambda: ["C"])
    pl = B.places("_default")
    home = [p for p in pl if p["kind"] == "home"][0]
    if fx == "win32" and POSIX:
        assert home["path"] == r"C:\Users\u" and home["available"] is False   # a simulated platform is never touched
    else:
        assert home["path"] == str(sb.home)
    assert all(set(p) >= {"kind", "label", "path", "available"} for p in pl)


# ── AC7 managed parent and foreign workspaces ───────────────────────────────────────────────────

def test_the_managed_parent_is_one_row_and_foreign_workspaces_are_withheld(sb):
    own, _o = registry.ensure_managed_root("_default")
    foreign, _o = registry.ensure_managed_root("acme")
    (foreign / "foreign-planted").mkdir()
    (own / "own-planted").mkdir()
    parent = Path(str(U.managed_parent()))
    data = parent.parent                                      # <data>/corvin
    lst = B.listing("_default", str(data), "create")
    rows = [e for e in lst["entries"] if e["path"] == str(own)]
    assert len(rows) == 1 and rows[0]["name"] == "Corvin workspaces"
    assert foreign.name not in json.dumps(lst) and own.name not in [e["name"] for e in lst["entries"]]
    for p in (parent, foreign, foreign / "foreign-planted"):
        r = B.listing("_default", str(p), "create")
        assert r["entries"] == [] and r["verdict"]["reason_class"] == "other_tenant", p
        text = json.dumps(r["entries"])                       # the client's own path is echoed; nothing below it is named
        assert "foreign-planted" not in text and "own-planted" not in text and own.name not in text
    assert foreign.name not in json.dumps(B.listing("_default", str(parent), "create"))


def test_a_foreign_project_cannot_be_entered(sb, tree):
    for p in (tree / "theirs", tree / "theirs" / "foreign-child"):
        r = B.listing("_default", str(p), "adopt")
        assert r["entries"] == [] and r["verdict"]["reason_class"] == "other_tenant"
        assert "foreign-child" not in json.dumps(r["entries"])


# ── AC8 junctions (win32 stand-in) and Python 3.11 ──────────────────────────────────────────────

def test_a_junction_entry_is_a_link_and_not_enterable(sb, tree, monkeypatch):
    j = tree / "junc"
    (j / "behind-junction").mkdir(parents=True)
    monkeypatch.setattr(B, "dirent_is_junction", lambda e: e.name == "junc")
    real_is_link = registry.is_link
    monkeypatch.setattr(registry, "is_link", lambda p: Path(str(p)) == j or real_is_link(p))
    e = _by_name(B.listing("_default", str(tree), "create"))["junc"]
    assert e["symlink"] is True and e["refusal"] == "symlink"
    r = B.listing("_default", str(j), "create")
    assert r["entries"] == [] and r["verdict"]["reason_class"] == "symlink"
    assert "behind-junction" not in json.dumps(r)


def test_the_module_never_calls_the_312_only_apis():
    src = Path(B.__file__).read_text(encoding="utf-8")
    for bad in (".is_junction(", "isjunction(", "listdrives("):
        assert bad not in src, bad


# ── AC9 UNC refused before any filesystem access ────────────────────────────────────────────────

class _NtOs:
    name = "nt"

    def __getattr__(self, k):
        return getattr(os, k)


def test_a_unc_path_is_refused_without_a_filesystem_call(sb, monkeypatch):
    calls = []
    for fn in ("stat", "lstat", "scandir", "listdir", "access"):
        real = getattr(os, fn)
        monkeypatch.setattr(os, fn, lambda *a, _r=real, _n=fn, **k: (calls.append(_n), _r(*a, **k))[1])
    monkeypatch.setattr(registry, "os", _NtOs())
    for bad in ("\\\\host\\share\\p", "\\\\?\\UNC\\host\\share\\p", "//host/share/p"):
        with pytest.raises(B.BrowseInputError) as ei:
            B.listing("_default", bad, "adopt")
        assert "map the share to a drive letter" in str(ei.value)
        assert ei.value.reason_class == "path_rule"
    assert calls == [], calls


@pytest.mark.parametrize("bad", ["", "relative/x", "~/x", "/a/../b", "/a\x00b", "/" + "x" * 1001])
def test_the_input_rule(sb, bad):
    with pytest.raises(B.BrowseInputError) as ei:
        B.listing("_default", bad, "create")
    assert ei.value.reason_class == "path_rule"


def test_purpose_and_project_id_are_checked(sb):
    with pytest.raises(B.BrowseInputError):
        B.listing("_default", str(sb.home), "browse")
    with pytest.raises(B.BrowseInputError):
        B.listing("_default", str(sb.home), "move")
    with pytest.raises(B.BrowseInputError):
        B.listing("_default", str(sb.home), "relocate", None)
