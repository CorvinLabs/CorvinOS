"""Forge Bundle import + tool quarantine — library level (ADR-2229 Phase 3).

Bundles are produced by the real Phase 2 exporter from real forge stores, then
the originals are removed so the import lands in an install that does not
have them — the "another install" case, without a second process. Test names
carrying a finding id (A1, S7, C4, …) are the proof for that adversarial-review
finding of 2026-10-06.
"""
from __future__ import annotations

import io
import json
import threading
import zipfile

import pytest

from core.forge_bundle import (
    BundleImportError,
    LayerSelection,
    PluginSelection,
    QuarantineConflict,
    QuarantineError,
    QuarantineNotFound,
    SkillSelection,
    ToolQuarantine,
    ToolSelection,
    build_bundle,
    import_bundle,
)
from core.forge_bundle import tool_quarantine as tq
from core.forge_bundle.audit import ForgeBundleAuditError
from core.forge_bundle.envelope import Requirement
from core.forge_bundle.import_module import BundleImportAborted

T = "_default"


def _import(data, *, skills=True):
    return import_bundle(data, tenant_id=T, actor="test", may_install_skills=skills)


def _remove_originals(tool: str | None = "csv.count", layers=(("acme.audit-l34", "1.0.0"),)):
    from forge.multi_registry import MultiRegistry

    from core.orchestration.layer_forge.orchestrator import layer_forge_home

    if tool:
        MultiRegistry(tenant_id=T).delete(tool)
    for lid, ver in layers:
        (layer_forge_home(T) / "registry" / f"{lid}@{ver}.json").unlink()


@pytest.fixture
def full_bundle(make_skill, make_tool, make_layer, make_plugin_package) -> bytes:
    make_skill("summarize", "1.0.0")
    make_tool("csv.count")
    make_layer("acme.audit-l34", "1.0.0")
    pkg = make_plugin_package("acme-audit-sink", "0.5.0")
    data = build_bundle(
        bundle_id="acme-automation", bundle_version="2.0.0", tenant_id=T,
        selections=[
            SkillSelection("summarize", "1.0.0"),
            ToolSelection("csv.count", "0.2.0"),
            LayerSelection("acme.audit-l34", "1.0.0"),
            PluginSelection("acme-audit-sink", "0.5.0", package_path=pkg),
        ],
    ).data
    _remove_originals()
    return data


@pytest.fixture
def tool_bundle(make_tool) -> bytes:
    make_tool("csv.count")
    data = build_bundle(bundle_id="b", bundle_version="1.0.0", tenant_id=T,
                        selections=[ToolSelection("csv.count", "0.2.0")]).data
    _remove_originals(layers=())
    return data


def _registry_tool(name: str):
    from forge.multi_registry import MultiRegistry

    return MultiRegistry(tenant_id=T).get(name)


def _qid(result) -> str:
    return next(o.detail for o in result.outcomes if o.kind == "tool")


def _types(chain_events):
    return [e["event_type"] for e in chain_events() if e["event_type"].startswith("forge_bundle.")]


# ── happy path ───────────────────────────────────────────────────────────────


def test_every_kind_enters_through_its_own_forge(full_bundle, tmp_corvin_home):
    result = _import(full_bundle)
    status = {o.kind: o.status for o in result.outcomes}
    assert status == {"skill": "installed", "tool": "quarantined",
                      "layer": "forged", "plugin": "pending_approval"}, result.outcomes
    assert result.failed == 0 and result.origin_verified is False
    assert (tmp_corvin_home / "skills_installed" / "summarize" / "1.0.0").is_dir()
    from core.orchestration.layer_forge.orchestrator import layer_forge_home
    assert (layer_forge_home(T) / "registry" / "acme.audit-l34@1.0.0.json").exists()
    from core.plugins.staging import StagingManager
    upload_id = next(o.detail for o in result.outcomes if o.kind == "plugin")
    staged = StagingManager(T).get_staged_upload(upload_id)
    assert staged["status"] == "pending_approval"
    assert staged["file_name"] == "acme-audit-sink-0.5.0.zip"  # C17: not a temp name


def test_imported_tool_is_not_callable_until_accepted(full_bundle):
    qid = _qid(_import(full_bundle))
    assert _registry_tool("csv.count") is None
    tq.accept(T, qid, actor="test")
    spec = _registry_tool("csv.count")
    assert spec.meta["origin"] == "forge_bundle" and spec.meta["origin_verified"] is False
    assert ToolQuarantine(T).list() == []


def test_reject_drops_the_tool_and_never_creates_it(full_bundle):
    qid = _qid(_import(full_bundle))
    tq.reject(T, qid, actor="test")
    assert _registry_tool("csv.count") is None and ToolQuarantine(T).list() == []
    with pytest.raises(QuarantineNotFound):
        tq.accept(T, qid, actor="test")


def test_A2_intent_and_outcome_are_separate_records(full_bundle, chain_events):
    qid = _qid(_import(full_bundle))
    tq.accept(T, qid, actor="test")
    types = _types(chain_events)
    validated = types.index("forge_bundle.import_validated")
    started = [i for i, t in enumerate(types) if t == "forge_bundle.artifact_intake_started"]
    staged = [i for i, t in enumerate(types) if t == "forge_bundle.artifact_staged"]
    assert len(started) == len(staged) == 4 and validated < min(started)
    assert all(s < o for s, o in zip(started, staged))
    assert types[-2:] == ["forge_bundle.quarantine_accepted", "forge_bundle.artifact_created"]


# ── refusals ─────────────────────────────────────────────────────────────────


def test_tampered_bundle_is_refused_and_nothing_is_staged(full_bundle, chain_events):
    src = zipfile.ZipFile(io.BytesIO(full_bundle))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename.endswith("spec.json"):
                data = data.replace(b"test tool", b"evil tool")
            dst.writestr(info.filename, data)
    with pytest.raises(BundleImportError) as exc:
        _import(out.getvalue())
    assert exc.value.stage == "integrity"
    assert ToolQuarantine(T).list() == []
    assert "forge_bundle.import_rejected" in _types(chain_events)
    assert "forge_bundle.artifact_intake_started" not in _types(chain_events)


def test_requirement_this_install_lacks_is_refused_as_stale(make_tool):
    make_tool("csv.count")
    data = build_bundle(bundle_id="b", bundle_version="1.0.0", tenant_id=T, selections=[
        ToolSelection("csv.count", "0.2.0", requires=(Requirement("layer", "not.here", "1.0.0"),))]).data
    _remove_originals(layers=())
    with pytest.raises(BundleImportError) as exc:
        _import(data)
    assert exc.value.stage == "staleness"


def test_existing_tool_satisfies_a_versioned_requirement(make_tool):
    make_tool("csv.count")
    make_tool("helper.tool")
    data = build_bundle(bundle_id="b", bundle_version="1.0.0", tenant_id=T, selections=[
        ToolSelection("csv.count", "0.2.0", requires=(Requirement("tool", "helper.tool", "9.9.9"),))]).data
    _remove_originals(layers=())
    assert [o.status for o in _import(data).outcomes] == ["quarantined"]


def test_A9_C10_unreadable_inventory_is_a_recorded_refusal(tool_bundle, tmp_corvin_home, chain_events):
    reg = tmp_corvin_home / "skills_installed" / "skills_registry.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text("[1, 2, 3]")
    with pytest.raises(BundleImportError) as exc:
        _import(tool_bundle)
    assert exc.value.stage == "inventory"
    rejected = [e for e in chain_events() if e["event_type"] == "forge_bundle.import_rejected"]
    assert rejected and rejected[-1]["details"]["rejected_stage"] == "inventory"


# ── host-wide skill store (S7 / A8 / C12) ────────────────────────────────────


def test_S7_skill_import_needs_the_install_owner(full_bundle, tmp_corvin_home, chain_events):
    result = _import(full_bundle, skills=False)
    skill = next(o for o in result.outcomes if o.kind == "skill")
    assert skill.status == "failed" and "install owner" in skill.detail
    assert not (tmp_corvin_home / "skills_installed" / "summarize").exists()
    assert {o.kind: o.status for o in result.outcomes}["tool"] == "quarantined"  # the rest still lands
    failed = [e for e in chain_events() if e["event_type"] == "forge_bundle.artifact_failed"]
    assert failed[-1]["details"]["artifact_kind"] == "skill"


# ── audit outage mid-import (A1) ─────────────────────────────────────────────


def test_A1_an_outage_mid_import_reports_what_landed(full_bundle, monkeypatch, tmp_corvin_home):
    from core.forge_bundle import import_module

    real = import_module.emit
    calls = {"n": 0}

    def flaky(event, **kw):
        if event == "forge_bundle.artifact_intake_started":
            calls["n"] += 1
            if calls["n"] == 2:
                raise ForgeBundleAuditError("down")
        return real(event, **kw)

    monkeypatch.setattr(import_module, "emit", flaky)
    with pytest.raises(BundleImportAborted) as exc:
        _import(full_bundle)
    statuses = [o.status for o in exc.value.result.outcomes]
    assert len(statuses) == 4
    assert statuses[0] != "not_attempted" and statuses[1:] == ["not_attempted"] * 3


def test_audit_failure_before_any_intake_changes_nothing(full_bundle, monkeypatch):
    from core.forge_bundle import import_module

    def boom(event, **_k):
        raise ForgeBundleAuditError(f"{event}: down")

    monkeypatch.setattr(import_module, "emit", boom)
    with pytest.raises(ForgeBundleAuditError):
        _import(full_bundle)
    assert ToolQuarantine(T).list() == [] and _registry_tool("csv.count") is None


# ── dependency order (C4) ────────────────────────────────────────────────────


def test_C4_a_layer_lands_after_the_layer_it_depends_on(make_layer):
    from core.orchestration.layer_forge.orchestrator import layer_forge_home
    from core.orchestration.layer_forge.registry import LayerRegistry

    make_layer("acme.base", "1.0.0")
    reg = LayerRegistry(layer_forge_home(T) / "registry")
    reg.create({"id": "acme.child", "version": "1.0.0",
                "targets": [{"layer_id": "L34", "layer_name": "Data Flow Guard"}],
                "dependencies": [{"id": "acme.base", "type": "layer_definition"}],
                "quality_gates": [], "enforcement_rules": []})
    data = build_bundle(bundle_id="b", bundle_version="1.0.0", tenant_id=T, selections=[
        LayerSelection("acme.child", "1.0.0"), LayerSelection("acme.base", "1.0.0")]).data
    _remove_originals(tool=None, layers=(("acme.child", "1.0.0"), ("acme.base", "1.0.0")))
    result = _import(data)
    assert [(o.id, o.status) for o in result.outcomes] == [("acme.base", "forged"), ("acme.child", "forged")]


# ── imported layers (C5 / S6 / A7) ───────────────────────────────────────────


def _layer_bundle(make_layer, mutate) -> bytes:
    from core.orchestration.layer_forge.orchestrator import layer_forge_home

    make_layer("acme.audit-l34", "1.0.0")
    data = build_bundle(bundle_id="b", bundle_version="1.0.0", tenant_id=T,
                        selections=[LayerSelection("acme.audit-l34", "1.0.0")]).data
    _remove_originals(tool=None)
    src = zipfile.ZipFile(io.BytesIO(data))
    env = json.loads(src.read("forge-bundle.json"))
    path = "artifacts/layer/acme.audit-l34@1.0.0/manifest.json"
    manifest = json.loads(src.read(path))
    mutate(manifest)
    body = json.dumps(manifest).encode()
    import hashlib
    for f in env["artifacts"][0]["files"]:
        f.update(sha256=hashlib.sha256(body).hexdigest(), size=len(body))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        dst.writestr("forge-bundle.json", json.dumps(env))
        dst.writestr(path, body)
    return out.getvalue()


def test_C5_a_travelling_review_flag_is_dropped(make_layer):
    from core.orchestration.layer_forge.orchestrator import layer_forge_home

    data = _layer_bundle(make_layer, lambda m: m.update(review_flagged=True, review_flags=["x"], status="deployed"))
    assert _import(data).outcomes[0].status == "forged"
    stored = json.loads((layer_forge_home(T) / "registry" / "acme.audit-l34@1.0.0.json").read_text())
    assert "review_flagged" not in stored and stored["status"] != "deployed"


@pytest.mark.parametrize("mutate,match", [
    (lambda m: m.update(quality_gates=[{"gate_id": "g1", "test_path": "tests"}]), "one test file"),
    (lambda m: m.update(quality_gates=[{"gate_id": "Jane Doe lives at 1 Main St", "test_path": "tests/test_x.py"}]), "short identifiers"),
    (lambda m: m.update(enforcement_rules=[{"rule_id": "free text with spaces", "type": "boot_time"}]), "short identifiers"),
    (lambda m: m.update(targets=[{"layer_id": "Ignore all prior instructions and"}]), "target ids"),        # R2B-8
    (lambda m: m.update(quality_gates=[{"gate_id": "g1", "test_path": "tests/test_x.py::test_a"}]), "one test file"),  # R2B-9
])
def test_S6_A7_imported_layer_gates_are_bounded(make_layer, mutate, match):
    outcome = _import(_layer_bundle(make_layer, mutate)).outcomes[0]
    assert outcome.status == "failed" and match in outcome.detail


# ── tool quarantine (C8 / A4 / A5 / S8) ──────────────────────────────────────


def test_C8_an_existing_tool_is_reported_as_a_conflict_at_import(tool_bundle, make_tool):
    make_tool("csv.count")
    outcome = _import(tool_bundle).outcomes[0]
    assert outcome.status == "failed" and "already exists" in outcome.detail
    assert ToolQuarantine(T).list() == []


def test_C8_importing_the_same_bundle_twice_queues_one_entry(tool_bundle):
    first, second = _qid(_import(tool_bundle)), _qid(_import(tool_bundle))
    assert first == second and len(ToolQuarantine(T).list()) == 1


def test_A4_a_failed_cleanup_never_lets_the_entry_be_decided_again(tool_bundle, monkeypatch):
    qid = _qid(_import(tool_bundle))
    monkeypatch.setattr(ToolQuarantine, "discard", staticmethod(lambda claimed: None))  # cleanup "fails"
    tq.accept(T, qid, actor="test")
    assert _registry_tool("csv.count") is not None
    assert ToolQuarantine(T).list() == []
    with pytest.raises(QuarantineNotFound):
        tq.reject(T, qid, actor="test")


def test_A5_two_concurrent_accepts_decide_once(tool_bundle, chain_events):
    qid = _qid(_import(tool_bundle))
    barrier, results = threading.Barrier(2), []

    def go():
        barrier.wait()
        try:
            tq.accept(T, qid, actor="test")
            results.append("ok")
        except QuarantineError as exc:
            results.append(type(exc).__name__)

    threads = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == ["QuarantineNotFound", "ok"]
    assert _types(chain_events).count("forge_bundle.quarantine_accepted") == 1


def test_a_failed_create_puts_the_entry_back(tool_bundle, monkeypatch):
    qid = _qid(_import(tool_bundle))
    from forge.multi_registry import MultiRegistry

    def denied(self, **_k):
        raise PermissionError("forge.create denied: free tier")

    monkeypatch.setattr(MultiRegistry, "create", denied)
    with pytest.raises(tq.QuarantineForbidden):
        tq.accept(T, qid, actor="test")
    assert [e.quarantine_id for e in ToolQuarantine(T).list()] == [qid]


@pytest.mark.parametrize("qid", ["*", "../etc", "a" * 31, "A" * 32, ""])
def test_quarantine_id_must_be_32_hex(qid, tool_bundle):
    _import(tool_bundle)
    with pytest.raises(QuarantineNotFound):
        tq.reject(T, qid, actor="test")
    assert len(ToolQuarantine(T).list()) == 1


def test_S8_a_tampered_meta_file_cannot_redirect_the_impl_read(tool_bundle):
    qid = _qid(_import(tool_bundle))
    q = ToolQuarantine(T)
    meta_path = q.root / qid / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["impl_filename"] = "../../../../etc/passwd"
    meta_path.write_text(json.dumps(meta))
    with pytest.raises(QuarantineError, match="inconsistent"):
        tq.accept(T, qid, actor="test")


def test_implementation_changed_on_disk_is_refused_at_accept(tool_bundle):
    qid = _qid(_import(tool_bundle))
    q = ToolQuarantine(T)
    (q.root / qid / q.get(qid).impl_filename).write_text("import os\nos.system('id')\n")
    with pytest.raises(QuarantineError, match="changed on disk"):
        tq.accept(T, qid, actor="test")
    assert _registry_tool("csv.count") is None
    assert len(q.list()) == 1  # back in the queue


def test_accept_refuses_to_overwrite_a_tool_created_meanwhile(tool_bundle, make_tool):
    qid = _qid(_import(tool_bundle))
    make_tool("csv.count", impl_body="def run(req):\n    return 'local'\n")
    with pytest.raises(QuarantineConflict):
        tq.accept(T, qid, actor="test")
    assert len(ToolQuarantine(T).list()) == 1


def test_staging_refuses_a_credential_in_the_implementation():
    with pytest.raises(QuarantineError, match="credential"):
        ToolQuarantine(T).stage(
            tool_id="x.tool", version="1.0.0", bundle_id="b", bundle_version="1.0.0",
            spec={"name": "x.tool", "runtime": "python", "input_schema": {}},
            impl_bytes=b'KEY = "AKIAABCDEFGHIJKLMNOP"\n')


@pytest.mark.parametrize("meta,match", [
    ({"requirements": ["git+https://evil.example/pkg"]}, "plain package"),
    ({"requirements": ["--index-url=https://evil.example"]}, "plain package"),
    ({"secrets": ["../x"]}, "secret references"),
    ({"budget": {"cpu_seconds": -1}}, "budget"),
])
def test_C3_travelling_tool_meta_is_validated(meta, match):
    with pytest.raises(QuarantineError, match=match):
        ToolQuarantine(T).stage(
            tool_id="x.tool", version="1.0.0", bundle_id="b", bundle_version="1.0.0",
            spec={"name": "x.tool", "runtime": "python", "input_schema": {}, "meta": meta},
            impl_bytes=b"def run(r):\n    return 1\n")


def test_C3_tool_meta_survives_the_round_trip(tmp_corvin_home):
    from forge.multi_registry import MultiRegistry

    MultiRegistry(tenant_id=T).create(
        scope="user", name="stats.mean", description="d", input_schema={"type": "object"},
        impl="def run(r):\n    return 0\n", meta={"requirements": ["numpy>=1.26"], "deterministic": True})
    data = build_bundle(bundle_id="b", bundle_version="1.0.0", tenant_id=T,
                        selections=[ToolSelection("stats.mean", "1.0.0")]).data
    MultiRegistry(tenant_id=T).delete("stats.mean")
    qid = _qid(_import(data))
    assert ToolQuarantine(T).get(qid).to_public()["requirements"] == ["numpy>=1.26"]
    tq.accept(T, qid, actor="test")
    meta = _registry_tool("stats.mean").meta
    assert meta["requirements"] == ["numpy>=1.26"] and meta["deterministic"] is True


def test_quarantine_files_are_owner_only(tool_bundle):
    qid = _qid(_import(tool_bundle))
    entry_dir = ToolQuarantine(T).root / qid
    assert entry_dir.stat().st_mode & 0o077 == 0
    for f in entry_dir.iterdir():
        assert f.stat().st_mode & 0o077 == 0


def test_C17_reimporting_a_staged_plugin_does_not_restage_it(make_plugin_package):
    pkg = make_plugin_package("acme-audit-sink", "0.5.0")
    data = build_bundle(bundle_id="b", bundle_version="1.0.0", tenant_id=T,
                        selections=[PluginSelection("acme-audit-sink", "0.5.0", package_path=pkg)]).data
    from core.plugins.staging import StagingManager

    first = _import(data).outcomes[0]
    staged_at = StagingManager(T).get_staged_upload(first.detail)["timestamp"]
    second = _import(data).outcomes[0]
    assert second.status == "pending_approval" and second.detail == first.detail
    assert StagingManager(T).get_staged_upload(first.detail)["timestamp"] == staged_at


# ── adversarial round 2 ──────────────────────────────────────────────────────


def test_R2B_10_gates_are_bounded_per_bundle_not_just_per_layer(make_layer):
    gates = [{"gate_id": f"g{i}", "test_path": "tests/test_x.py"} for i in range(17)]
    with pytest.raises(BundleImportError) as exc:
        _import(_layer_bundle(make_layer, lambda m: m.update(quality_gates=gates)))
    assert exc.value.stage == "limits"


def test_R2B_2_a_dependency_without_type_still_orders(make_layer):
    from core.orchestration.layer_forge.orchestrator import layer_forge_home
    from core.orchestration.layer_forge.registry import LayerRegistry

    make_layer("acme.base", "1.0.0")
    LayerRegistry(layer_forge_home(T) / "registry").create({
        "id": "acme.child", "version": "1.0.0", "targets": [{"layer_id": "L34"}],
        "dependencies": [{"id": "acme.base"}], "quality_gates": [], "enforcement_rules": []})
    data = build_bundle(bundle_id="b", bundle_version="1.0.0", tenant_id=T, selections=[
        LayerSelection("acme.child", "1.0.0"), LayerSelection("acme.base", "1.0.0")]).data
    _remove_originals(tool=None, layers=(("acme.child", "1.0.0"), ("acme.base", "1.0.0")))
    assert [o.id for o in _import(data).outcomes] == ["acme.base", "acme.child"]


def test_R2B_3_a_dependent_fails_when_its_bundled_dependency_did_not_land(make_layer, monkeypatch):
    from core.orchestration.layer_forge.orchestrator import layer_forge_home
    from core.orchestration.layer_forge.registry import LayerRegistry
    from core.forge_bundle import import_module

    make_layer("acme.base", "1.0.0")
    LayerRegistry(layer_forge_home(T) / "registry").create({
        "id": "acme.child", "version": "1.0.0", "targets": [{"layer_id": "L34"}],
        "dependencies": [{"id": "acme.base", "type": "layer_definition"}],
        "quality_gates": [], "enforcement_rules": []})
    data = build_bundle(bundle_id="b", bundle_version="1.0.0", tenant_id=T, selections=[
        LayerSelection("acme.base", "1.0.0"), LayerSelection("acme.child", "1.0.0")]).data
    _remove_originals(tool=None, layers=(("acme.child", "1.0.0"),))  # an OLD base stays installed
    result = _import(data)  # base fails: it already exists here
    by_id = {o.id: o for o in result.outcomes}
    assert by_id["acme.base"].status == "failed"
    assert by_id["acme.child"].status == "failed" and "did not import" in by_id["acme.child"].detail


def test_R2B_1_a_layer_written_before_layer_forges_audit_failed_counts_as_landed(make_layer, monkeypatch):
    from core.orchestration.layer_forge import orchestrator as orch_mod

    data = _layer_bundle(make_layer, lambda m: None)
    real = orch_mod.LayerForgeOrchestrator._transition_locked

    def audit_dies_after_write(self, *a, **k):
        raise orch_mod.audit.LayerForgeAuditError("chain down")

    monkeypatch.setattr(orch_mod.LayerForgeOrchestrator, "_transition_locked", audit_dies_after_write)
    outcome = _import(data).outcomes[0]
    assert outcome.status == "forged" and "could not record" in outcome.detail


def test_R2B_4_an_unreadable_forged_skill_is_an_inventory_refusal(tool_bundle, tmp_corvin_home):
    broken = tmp_corvin_home / "skills_gen" / "broken"
    broken.mkdir(parents=True)
    (broken / "skill.json").write_text("{not json")
    with pytest.raises(BundleImportError) as exc:
        _import(tool_bundle)
    assert exc.value.stage == "inventory"


def test_R2A_1_a_tool_created_before_the_registry_audit_failed_is_reported_created(tool_bundle, monkeypatch, chain_events):
    qid = _qid(_import(tool_bundle))
    from forge.multi_registry import MultiRegistry
    real_create = MultiRegistry.create

    def create_then_audit_fails(self, **kw):
        real_create(self, **kw)
        raise OSError("disk full while writing the registry audit record")

    monkeypatch.setattr(MultiRegistry, "create", create_then_audit_fails)
    tq.accept(T, qid, actor="test")
    assert _registry_tool("csv.count") is not None
    assert ToolQuarantine(T).list() == []
    assert _types(chain_events)[-1] == "forge_bundle.artifact_created"


def test_R2A_7_a_changed_version_is_a_new_review_entry(make_tool):
    make_tool("csv.count")
    one = build_bundle(bundle_id="b", bundle_version="1.0.0", tenant_id=T,
                       selections=[ToolSelection("csv.count", "0.2.0")]).data
    two = build_bundle(bundle_id="b", bundle_version="1.0.0", tenant_id=T,
                       selections=[ToolSelection("csv.count", "0.3.0")]).data
    _remove_originals(layers=())
    assert _qid(_import(one)) != _qid(_import(two))
    assert {e.version for e in ToolQuarantine(T).list()} == {"0.2.0", "0.3.0"}


@pytest.mark.parametrize("req", ["evil-1.0-py3-none-any.whl", "pkg.tar.gz", "x.zip", "a-"])
def test_R2A_9_requirements_that_pip_would_treat_as_files_are_refused(req):
    with pytest.raises(QuarantineError, match="plain package"):
        tq.clean_tool_meta({"requirements": [req]})


def test_R2A_10_a_non_finite_budget_is_refused():
    with pytest.raises(QuarantineError, match="budget"):
        tq.clean_tool_meta({"budget": {"cpu_seconds": float("inf")}})


def test_R2A_11_the_conflict_check_ignores_case(tool_bundle, make_tool):
    make_tool("CSV.count")
    outcome = _import(tool_bundle).outcomes[0]
    assert outcome.status == "failed" and "already exists" in outcome.detail


def test_R2A_12_stale_claim_leftovers_are_swept(tool_bundle):
    import os
    import time as _t
    qid = _qid(_import(tool_bundle))
    q = ToolQuarantine(T)
    stale = q.root / f".claimed-{'f' * 32}"
    stale.mkdir()
    old = _t.time() - 7200
    os.utime(stale, (old, old))
    assert [e.quarantine_id for e in q.list()] == [qid]
    assert not stale.exists()
