"""Forge Bundle import + tool quarantine — library level (ADR-2229 Phase 3).

Bundles are produced by the real Phase 2 exporter from real forge stores, then
the originals are removed so the import lands in an install that does not
have them — the "another install" case, without a second process.
"""
from __future__ import annotations

import io
import json
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

T = "_default"


def _remove_originals(tool: str | None = "csv.count", layer: tuple[str, str] | None = ("acme.audit-l34", "1.0.0")):
    from forge.multi_registry import MultiRegistry

    from core.orchestration.layer_forge.orchestrator import layer_forge_home

    if tool:
        MultiRegistry(tenant_id=T).delete(tool)
    if layer:
        (layer_forge_home(T) / "registry" / f"{layer[0]}@{layer[1]}.json").unlink()


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
            PluginSelection("acme-audit-sink", "0.5.0", wheel_path=pkg),
        ],
    ).data
    _remove_originals()
    return data


def _registry_tool(name: str):
    from forge.multi_registry import MultiRegistry

    return MultiRegistry(tenant_id=T).get(name)


# ── happy path ───────────────────────────────────────────────────────────────


def test_every_kind_enters_through_its_own_forge(full_bundle, tmp_corvin_home):
    result = import_bundle(full_bundle, tenant_id=T, actor="test")
    status = {o.kind: o.status for o in result.outcomes}
    assert status == {"skill": "installed", "tool": "quarantined",
                      "layer": "forged", "plugin": "pending_approval"}, result.outcomes
    assert result.failed == 0
    assert result.origin_verified is False

    # skill: SkillInstaller's own store
    assert (tmp_corvin_home / "skills_installed" / "summarize" / "1.0.0").is_dir()
    # layer: Layer Forge registry, after its gates
    from core.orchestration.layer_forge.orchestrator import layer_forge_home
    assert (layer_forge_home(T) / "registry" / "acme.audit-l34@1.0.0.json").exists()
    # plugin: the existing plugin-upload staging store, awaiting approval
    from core.plugins.staging import StagingManager
    upload_id = next(o.detail for o in result.outcomes if o.kind == "plugin")
    assert StagingManager(T).get_staged_upload(upload_id)["status"] == "pending_approval"


def test_imported_tool_is_not_callable_until_accepted(full_bundle):
    result = import_bundle(full_bundle, tenant_id=T, actor="test")
    qid = next(o.detail for o in result.outcomes if o.kind == "tool")
    assert _registry_tool("csv.count") is None

    tq.accept(T, qid, actor="test")
    spec = _registry_tool("csv.count")
    assert spec is not None
    assert spec.meta["origin"] == "forge_bundle"
    assert spec.meta["origin_verified"] is False
    assert ToolQuarantine(T).list() == []


def test_reject_drops_the_tool_and_never_creates_it(full_bundle):
    result = import_bundle(full_bundle, tenant_id=T, actor="test")
    qid = next(o.detail for o in result.outcomes if o.kind == "tool")
    tq.reject(T, qid, actor="test")
    assert _registry_tool("csv.count") is None
    assert ToolQuarantine(T).list() == []
    with pytest.raises(QuarantineNotFound):
        tq.accept(T, qid, actor="test")


def test_audit_records_precede_the_state_they_describe(full_bundle, chain_events):
    result = import_bundle(full_bundle, tenant_id=T, actor="test")
    qid = next(o.detail for o in result.outcomes if o.kind == "tool")
    tq.accept(T, qid, actor="test")

    types = [e["event_type"] for e in chain_events() if e["event_type"].startswith("forge_bundle.")]
    validated = types.index("forge_bundle.import_validated")
    staged = [i for i, t in enumerate(types) if t == "forge_bundle.artifact_staged"]
    assert len(staged) == 4 and min(staged) > validated
    assert types[-1] == "forge_bundle.quarantine_accepted"
    for e in chain_events():
        if e["event_type"].startswith("forge_bundle."):
            assert "reason" not in json.dumps(e.get("details", {}))


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
        import_bundle(out.getvalue(), tenant_id=T, actor="test")
    assert exc.value.stage == "integrity"
    assert ToolQuarantine(T).list() == []
    assert [e for e in chain_events() if e["event_type"] == "forge_bundle.import_rejected"]
    assert not [e for e in chain_events() if e["event_type"] == "forge_bundle.artifact_staged"]


def test_requirement_this_install_lacks_is_refused_as_stale(make_tool):
    make_tool("csv.count")
    data = build_bundle(
        bundle_id="b", bundle_version="1.0.0", tenant_id=T,
        selections=[ToolSelection("csv.count", "0.2.0",
                                  requires=(Requirement("layer", "not.here", "1.0.0"),))],
    ).data
    _remove_originals(layer=None)
    with pytest.raises(BundleImportError) as exc:
        import_bundle(data, tenant_id=T, actor="test")
    assert exc.value.stage == "staleness"


def test_existing_tool_satisfies_a_versioned_requirement(make_tool):
    # Tool Forge has no versions: existence satisfies any version.
    make_tool("csv.count")
    make_tool("helper.tool")
    data = build_bundle(
        bundle_id="b", bundle_version="1.0.0", tenant_id=T,
        selections=[ToolSelection("csv.count", "0.2.0",
                                  requires=(Requirement("tool", "helper.tool", "9.9.9"),))],
    ).data
    _remove_originals(layer=None)
    result = import_bundle(data, tenant_id=T, actor="test")
    assert [o.status for o in result.outcomes] == ["quarantined"]


def test_failed_intake_is_recorded_and_the_rest_still_lands(make_tool, make_plugin_wheel, chain_events):
    make_tool("csv.count")
    wheel = make_plugin_wheel("acme-audit-sink", "0.5.0")  # a wheel, not an ADR-0511 package
    data = build_bundle(
        bundle_id="b", bundle_version="1.0.0", tenant_id=T,
        selections=[ToolSelection("csv.count", "0.2.0"),
                    PluginSelection("acme-audit-sink", "0.5.0", wheel_path=wheel)],
    ).data
    _remove_originals(layer=None)
    result = import_bundle(data, tenant_id=T, actor="test")
    by_kind = {o.kind: o for o in result.outcomes}
    assert by_kind["tool"].status == "quarantined"
    assert by_kind["plugin"].status == "failed"
    assert "manifest.json" in by_kind["plugin"].detail
    failed = [e for e in chain_events() if e["event_type"] == "forge_bundle.artifact_failed"]
    assert failed and failed[-1]["details"]["artifact_kind"] == "plugin"


def test_audit_failure_stops_the_import_before_any_write(full_bundle, monkeypatch):
    from core.forge_bundle import import_module

    def boom(event, **_k):
        raise ForgeBundleAuditError(f"{event}: down")

    monkeypatch.setattr(import_module, "emit", boom)
    with pytest.raises(ForgeBundleAuditError):
        import_bundle(full_bundle, tenant_id=T, actor="test")
    assert ToolQuarantine(T).list() == []
    assert _registry_tool("csv.count") is None


# ── quarantine hardening ─────────────────────────────────────────────────────


@pytest.mark.parametrize("qid", ["*", "../etc", "a" * 31, "A" * 32, ""])
def test_quarantine_id_must_be_32_hex(qid, full_bundle):
    import_bundle(full_bundle, tenant_id=T, actor="test")
    with pytest.raises(QuarantineNotFound):
        tq.reject(T, qid, actor="test")
    assert len(ToolQuarantine(T).list()) == 1  # a glob id deleted nothing


def test_implementation_changed_on_disk_is_refused_at_accept(full_bundle):
    result = import_bundle(full_bundle, tenant_id=T, actor="test")
    qid = next(o.detail for o in result.outcomes if o.kind == "tool")
    q = ToolQuarantine(T)
    impl = q.root / qid / q.get(qid).impl_filename
    impl.write_text("import os\nos.system('id')\n")
    with pytest.raises(QuarantineError, match="changed on disk"):
        tq.accept(T, qid, actor="test")
    assert _registry_tool("csv.count") is None


def test_accept_refuses_to_overwrite_an_existing_tool(full_bundle, make_tool):
    result = import_bundle(full_bundle, tenant_id=T, actor="test")
    qid = next(o.detail for o in result.outcomes if o.kind == "tool")
    make_tool("csv.count", impl_body="def run(req):\n    return 'local'\n")
    with pytest.raises(QuarantineConflict):
        tq.accept(T, qid, actor="test")
    assert len(ToolQuarantine(T).list()) == 1


def test_staging_refuses_a_credential_in_the_implementation():
    with pytest.raises(QuarantineError, match="credential"):
        ToolQuarantine(T).stage(
            tool_id="x.tool", version="1.0.0", bundle_id="b", bundle_version="1.0.0",
            spec={"name": "x.tool", "runtime": "python", "input_schema": {}},
            impl_bytes=b'KEY = "AKIAABCDEFGHIJKLMNOP"\n',
        )


def test_staging_refuses_a_spec_naming_another_tool():
    with pytest.raises(QuarantineError, match="names"):
        ToolQuarantine(T).stage(
            tool_id="x.tool", version="1.0.0", bundle_id="b", bundle_version="1.0.0",
            spec={"name": "other.tool", "runtime": "python", "input_schema": {}},
            impl_bytes=b"def run(r):\n    return 1\n",
        )


def test_quarantine_files_are_owner_only(full_bundle):
    result = import_bundle(full_bundle, tenant_id=T, actor="test")
    qid = next(o.detail for o in result.outcomes if o.kind == "tool")
    entry_dir = ToolQuarantine(T).root / qid
    assert entry_dir.stat().st_mode & 0o077 == 0
    for f in entry_dir.iterdir():
        assert f.stat().st_mode & 0o077 == 0
