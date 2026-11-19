"""Review 2026-11-19, PLS-8 (the two in-scope parts).

* ``core.skills.skill_audit.audit_chain_path`` composed the tenant audit chain path by hand in
  three places. It now goes through ``tenant_audit_chain()``, the one resolver every chain
  writer shares - so a skill audit record lands in the file the boot tripwire verifies.
* ``WorkflowOptimizer._get_config_file`` put EVERY tenant's learned config into the process
  tenant's home. Each tenant's config now lives in that tenant's own home.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
for _p in (str(_REPO), str(_REPO / "corvin_operator"), str(_REPO / "corvin_operator" / "forge")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "corvin_home"
    root.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(root))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    return root


# ── skill_audit ──────────────────────────────────────────────────────────────

def test_the_skill_audit_chain_is_the_shared_resolvers(home):
    from core.skills import skill_audit
    from forge import paths as fp

    for tid in ("_default", "acme"):
        assert skill_audit.audit_chain_path(tid) == Path(fp.tenant_audit_chain(tid))
    assert skill_audit.audit_chain_path("acme") == home / "tenants" / "acme" / "global" / "forge" / "audit.jsonl"


def test_a_pinned_root_re_roots_the_resolvers_path_without_restating_the_layout(home, tmp_path):
    from core.skills import skill_audit
    from forge import paths as fp

    pinned = tmp_path / "other_home"
    got = skill_audit.audit_chain_path("acme", corvin_home=pinned)
    tail = Path(fp.tenant_audit_chain("acme")).relative_to(fp.corvin_home())
    assert got == pinned / tail
    assert str(got).startswith(str(pinned)) and str(home) not in str(got)


def test_the_resolver_is_actually_called(home, monkeypatch):
    """Call-site proof: change what the resolver answers and the skill audit path follows."""
    from core.skills import skill_audit
    from forge import paths as fp

    elsewhere = home / "moved" / "chain.jsonl"
    monkeypatch.setattr(fp, "tenant_audit_chain", lambda tenant_id: elsewhere)
    assert skill_audit.audit_chain_path("acme") == elsewhere


def test_an_invalid_tenant_id_is_refused_not_composed_into_a_path(home):
    from core.skills import skill_audit

    for bad in ("../escape", "a/b", ""):
        with pytest.raises(Exception):
            skill_audit.audit_chain_path(bad)


def test_the_module_no_longer_composes_the_chain_path_by_hand():
    src = (_REPO / "core" / "skills" / "skill_audit.py").read_text(encoding="utf-8")
    code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
    assert '"audit.jsonl"' not in code, "the file name is the resolver's business"
    assert "tenant_audit_chain(" in code


def test_an_emitted_skill_record_lands_on_the_resolvers_chain(home, monkeypatch):
    """End to end through the real writer: the record is in the tenant's one chain."""
    from core.skills import skill_audit
    from forge import paths as fp

    # the core writer refuses a record for a tenant other than the process tenant
    monkeypatch.setenv("CORVIN_TENANT_ID", "acme")

    assert skill_audit.emit_skill_audit(
        "acme", "skill.executed", details={"skill_id": "os.test", "version": "1.0.0"}) is True
    chain = Path(fp.tenant_audit_chain("acme"))
    assert chain.exists(), "nothing was written to the resolver's chain"
    events = [json.loads(line)["event_type"] for line in chain.read_text(encoding="utf-8").splitlines()]
    assert "skill.executed" in events


# ── workflow optimizer ───────────────────────────────────────────────────────

def _optimizer(**kw):
    from core.skills.os_skills.workflow_optimizer.skill import WorkflowOptimizer
    return WorkflowOptimizer(**kw)


def test_each_tenants_config_lives_in_that_tenants_home(home):
    from core.paths.tenant import tenant_home

    opt = _optimizer()
    a, b = opt._get_config_file("_default"), opt._get_config_file("acme")
    assert a == tenant_home("_default") / "workflow_optimizer_config.json"
    assert b == tenant_home("acme") / "workflow_optimizer_config.json"
    assert a.parent != b.parent
    assert str(tenant_home("_default")) not in str(b), "tenant B's file is not under tenant A"


def test_the_process_tenant_does_not_decide_where_another_tenant_is_stored(home, monkeypatch):
    from core.paths.tenant import tenant_home

    monkeypatch.setenv("CORVIN_TENANT_ID", "process_tenant")
    opt = _optimizer()
    assert opt._get_config_file("acme").parent == tenant_home("acme")
    assert opt._get_config_file("acme").parent != tenant_home("process_tenant")


def test_an_invalid_tenant_id_is_refused(home, tmp_path):
    for opt in (_optimizer(), _optimizer(config_path=str(tmp_path / "x" / "cfg.json"))):
        for bad in ("../../etc", "a/b"):
            with pytest.raises(Exception):
                opt._get_config_file(bad)


def test_an_explicit_path_keeps_its_one_directory_layout(home, tmp_path):
    opt = _optimizer(config_path=str(tmp_path / "cfgdir" / "cfg.json"))
    assert opt._get_config_file("acme") == tmp_path / "cfgdir" / "workflow_optimizer_config_acme.json"
    assert opt._legacy_config_file("acme") is None


def test_a_config_learned_before_the_fix_is_still_read_and_never_rewritten_in_place(home):
    """The pre-fix location (process tenant's home, tenant in the file name) is a read-only
    fallback, so a learned config is not lost by the move."""
    from core.paths.tenant import tenant_home

    opt = _optimizer()
    legacy = opt._legacy_config_file("acme")
    assert legacy == tenant_home("_default") / "workflow_optimizer_config_acme.json"
    new = opt._get_config_file("acme")
    assert not new.exists()

    # take the on-disk shape from the real writer, then place it where the old code put it
    writer = _optimizer()
    cfg = writer.load_config("_default")
    writer.save_config("_default", cfg)
    shape_file = writer._get_config_file("_default")
    assert shape_file.exists(), "positive control: the writer wrote the default tenant's config"
    saved_version = json.loads(shape_file.read_text(encoding="utf-8"))["version"]
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(shape_file.read_text(encoding="utf-8"), encoding="utf-8")
    before = legacy.read_bytes()

    reader = _optimizer()
    loaded = reader.load_config("acme")
    assert loaded.version == saved_version, "the legacy file was read, not the defaults"
    assert legacy.read_bytes() == before, "the legacy file is never written"

    # the next save goes to the tenant's own home; the legacy file stays as it was
    reader.save_config("acme", loaded)
    assert new.exists() and legacy.read_bytes() == before
    assert json.loads(new.read_text(encoding="utf-8"))["version"] == saved_version + 1


def test_two_tenants_configs_do_not_overwrite_each_other(home):
    opt = _optimizer()
    a = opt.load_config("_default")
    opt.save_config("_default", a)
    b = _optimizer().load_config("acme")
    assert b.version != a.version or not opt._get_config_file("acme").exists(), \
        "tenant acme has no config yet: it must read defaults, not tenant _default's file"
    assert not opt._get_config_file("acme").exists()
