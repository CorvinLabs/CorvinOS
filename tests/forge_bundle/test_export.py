"""Forge Bundle export — library level (ADR-2229 Phase 2).

Every test here builds real fixtures on disk (a skill folder, a tool via
MultiRegistry, a layer via LayerRegistry) and calls ``build_bundle`` for
real — no mocking of the collectors, since the entire point of Phase 2 is
"does export read what each forge actually wrote". The round-trip test closes
the loop with Phase 1: an exported bundle must pass ``validate_bundle`` with
the exporting tenant as its own inventory.
"""
from __future__ import annotations

import json
import zipfile
from io import BytesIO

import pytest

from core.forge_bundle import (
    BundleResult,
    ExportError,
    LayerSelection,
    PluginSelection,
    SkillSelection,
    ToolSelection,
    validate_bundle,
)
from core.forge_bundle.export import build_bundle


def test_export_all_four_kinds_round_trips_through_validate(
    make_skill, make_tool, make_layer, make_plugin_package,
):
    make_skill("summarize", "1.0.0")
    make_tool("csv.count")
    make_layer("acme.audit-l34", "1.0.0")
    wheel = make_plugin_package("acme-audit-sink", "0.5.0")

    result = build_bundle(
        bundle_id="acme-automation",
        bundle_version="2.0.0",
        tenant_id="_default",
        description="round trip",
        selections=[
            SkillSelection(skill_id="summarize", version="1.0.0"),
            ToolSelection(name="csv.count", version="0.2.0"),
            LayerSelection(entry_id="acme.audit-l34", version="1.0.0"),
            PluginSelection(plugin_id="acme-audit-sink", version="0.5.0", package_path=wheel),
        ],
    )
    assert isinstance(result, BundleResult)
    assert result.artifact_count == 4
    assert result.audit_hash

    report = validate_bundle(result.data)
    assert {a.kind for a in report.envelope.artifacts} == {"skill", "tool", "layer", "plugin"}


def test_skill_zip_payload_matches_skill_packager_output(make_skill):
    make_skill("summarize", "1.0.0")
    result = build_bundle(
        bundle_id="b", bundle_version="1.0.0", tenant_id="_default",
        selections=[SkillSelection(skill_id="summarize", version="1.0.0")],
    )
    with zipfile.ZipFile(BytesIO(result.data)) as zf:
        inner_names = [n for n in zf.namelist() if n.startswith("artifacts/skill/")]
        assert len(inner_names) == 1
        assert inner_names[0].endswith(".zip")
        inner_zip_bytes = zf.read(inner_names[0])
    with zipfile.ZipFile(BytesIO(inner_zip_bytes)) as inner:
        assert "summarize/skill.json" in inner.namelist()
        assert "summarize/src/skill.py" in inner.namelist()


def test_tool_export_excludes_registry_only_state(make_tool):
    make_tool("csv.count")
    result = build_bundle(
        bundle_id="b", bundle_version="1.0.0", tenant_id="_default",
        selections=[ToolSelection(name="csv.count", version="0.2.0")],
    )
    with zipfile.ZipFile(BytesIO(result.data)) as zf:
        spec = json.loads(zf.read("artifacts/tool/csv.count@0.2.0/spec.json"))
    assert set(spec) == {"name", "description", "input_schema", "runtime", "version", "impl_filename", "meta"}
    assert spec["version"] == "0.2.0"  # caller-supplied, not derived (ToolSpec has no version)
    assert spec["meta"] == {}


def test_layer_export_strips_registry_status(make_layer):
    make_layer("acme.audit-l34", "1.0.0")
    result = build_bundle(
        bundle_id="b", bundle_version="1.0.0", tenant_id="_default",
        selections=[LayerSelection(entry_id="acme.audit-l34", version="1.0.0")],
    )
    with zipfile.ZipFile(BytesIO(result.data)) as zf:
        manifest = json.loads(zf.read("artifacts/layer/acme.audit-l34@1.0.0/manifest.json"))
    assert "status" not in manifest
    assert "_created_at" not in manifest
    assert manifest["id"] == "acme.audit-l34"


def test_plugin_export_never_triggers_a_build(make_plugin_package, monkeypatch):
    wheel = make_plugin_package("acme-audit-sink", "0.5.0")
    # If export imported the builder at all this would fail the test session
    # (module not installed in this fixture's sys.path); its absence proves
    # collect() for plugin never reaches for it.
    import sys
    assert "core.plugins.plugin_builder.build_system.builder" not in sys.modules
    build_bundle(
        bundle_id="b", bundle_version="1.0.0", tenant_id="_default",
        selections=[PluginSelection(plugin_id="acme-audit-sink", version="0.5.0", package_path=wheel)],
    )
    assert "core.plugins.plugin_builder.build_system.builder" not in sys.modules


def test_audit_event_recorded(make_skill, tmp_corvin_home):
    make_skill("summarize", "1.0.0")
    result = build_bundle(
        bundle_id="acme-automation", bundle_version="2.0.0", tenant_id="_default",
        selections=[SkillSelection(skill_id="summarize", version="1.0.0")],
    )
    chain = tmp_corvin_home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    events = [json.loads(line) for line in chain.read_text().splitlines() if line.strip()]
    exported = [e for e in events if e.get("event_type") == "forge_bundle.exported"]
    assert exported, events
    details = exported[-1].get("details", exported[-1])
    assert details["bundle_id"] == "acme-automation"
    assert details["artifact_count"] == 1
    assert details["tenant_id"] == "_default"
    assert exported[-1].get("hash") == result.audit_hash


# ── failure paths ────────────────────────────────────────────────────────────


def test_missing_skill_raises_export_error():
    with pytest.raises(ExportError, match="skill not found"):
        build_bundle(
            bundle_id="b", bundle_version="1.0.0", tenant_id="_default",
            selections=[SkillSelection(skill_id="nope", version="1.0.0")],
        )


def test_skill_version_mismatch_raises(make_skill):
    make_skill("summarize", "1.0.0")
    with pytest.raises(ExportError, match="on-disk version"):
        build_bundle(
            bundle_id="b", bundle_version="1.0.0", tenant_id="_default",
            selections=[SkillSelection(skill_id="summarize", version="9.9.9")],
        )


def test_missing_tool_raises():
    with pytest.raises(ExportError, match="tool not found"):
        build_bundle(
            bundle_id="b", bundle_version="1.0.0", tenant_id="_default",
            selections=[ToolSelection(name="nope.tool", version="1.0.0")],
        )


def test_missing_layer_raises():
    with pytest.raises(ExportError, match="layer not found"):
        build_bundle(
            bundle_id="b", bundle_version="1.0.0", tenant_id="_default",
            selections=[LayerSelection(entry_id="nope.layer", version="1.0.0")],
        )


def test_missing_package_raises_at_selection_construction(tmp_path):
    with pytest.raises(ExportError, match="package file not found"):
        PluginSelection(plugin_id="x", version="1.0.0", package_path=tmp_path / "missing.whl")


def test_no_selections_raises():
    with pytest.raises(ExportError, match="at least one artifact"):
        build_bundle(bundle_id="b", bundle_version="1.0.0", tenant_id="_default", selections=[])


def test_duplicate_selection_raises(make_skill):
    make_skill("summarize", "1.0.0")
    with pytest.raises(ExportError, match="duplicate selection"):
        build_bundle(
            bundle_id="b", bundle_version="1.0.0", tenant_id="_default",
            selections=[
                SkillSelection(skill_id="summarize", version="1.0.0"),
                SkillSelection(skill_id="summarize", version="1.0.0"),
            ],
        )


@pytest.mark.parametrize("bad_id", ["", "../escape", "a/b"])
def test_unsafe_bundle_id_rejected(bad_id, make_skill):
    make_skill("summarize", "1.0.0")
    with pytest.raises(ExportError):
        build_bundle(
            bundle_id=bad_id, bundle_version="1.0.0", tenant_id="_default",
            selections=[SkillSelection(skill_id="summarize", version="1.0.0")],
        )


def test_non_semver_bundle_version_rejected(make_skill):
    make_skill("summarize", "1.0.0")
    with pytest.raises(ExportError, match="semver"):
        build_bundle(
            bundle_id="b", bundle_version="v2", tenant_id="_default",
            selections=[SkillSelection(skill_id="summarize", version="1.0.0")],
        )


def test_declared_requires_travel_into_the_envelope(make_skill, make_tool):
    from core.forge_bundle.envelope import Requirement

    make_skill("summarize", "1.0.0")
    make_tool("csv.count")
    result = build_bundle(
        bundle_id="b", bundle_version="1.0.0", tenant_id="_default",
        selections=[
            SkillSelection(skill_id="summarize", version="1.0.0"),
            ToolSelection(
                name="csv.count", version="0.2.0",
                requires=(Requirement(kind="skill", id="summarize", version="1.0.0"),),
            ),
        ],
    )
    report = validate_bundle(result.data, known={})
    tool_art = next(a for a in report.envelope.artifacts if a.kind == "tool")
    assert tool_art.requires == (Requirement(kind="skill", id="summarize", version="1.0.0"),)


def test_export_packages_the_skill_folder_as_it_is_now(make_skill, tmp_corvin_home):
    # A cached package ZIP must never ship code older than the folder.
    folder = make_skill("summarize", "1.0.0")
    build_bundle(bundle_id="b", bundle_version="1.0.0", tenant_id="_default",
                 selections=[SkillSelection(skill_id="summarize", version="1.0.0")])
    (folder / "src" / "skill.py").write_text("def run(text):\n    return 'NEW CODE'\n")
    second = build_bundle(bundle_id="b", bundle_version="1.0.1", tenant_id="_default",
                          selections=[SkillSelection(skill_id="summarize", version="1.0.0")])
    with zipfile.ZipFile(BytesIO(second.data)) as zf:
        inner = zf.read(next(n for n in zf.namelist() if n.startswith("artifacts/skill/")))
    with zipfile.ZipFile(BytesIO(inner)) as zi:
        assert b"NEW CODE" in zi.read("summarize/src/skill.py")
    assert not (tmp_corvin_home / "skills_packages" / "summarize_1.0.0.zip").exists()


def test_a_wheel_is_refused_at_export_not_at_import(make_plugin_wheel):
    wheel = make_plugin_wheel("acme-audit-sink", "0.5.0")
    with pytest.raises(ExportError, match="not a plugin package"):
        build_bundle(bundle_id="b", bundle_version="1.0.0", tenant_id="_default",
                     selections=[PluginSelection("acme-audit-sink", "0.5.0", package_path=wheel)])


def test_tool_meta_travels_as_its_behavioural_subset(tmp_corvin_home):
    from forge.multi_registry import MultiRegistry

    MultiRegistry(tenant_id="_default").create(
        scope="user", name="stats.mean", description="d", input_schema={"type": "object"},
        impl="def run(r):\n    return 0\n",
        meta={"requirements": ["numpy>=1.26"], "budget": {"cpu_seconds": 5}, "deterministic": True,
              "internal_note": "never exported"},
    )
    result = build_bundle(bundle_id="b", bundle_version="1.0.0", tenant_id="_default",
                          selections=[ToolSelection("stats.mean", "1.0.0")])
    with zipfile.ZipFile(BytesIO(result.data)) as zf:
        spec = json.loads(zf.read("artifacts/tool/stats.mean@1.0.0/spec.json"))
    assert spec["meta"] == {"requirements": ["numpy>=1.26"], "budget": {"cpu_seconds": 5}, "deterministic": True}


def test_layer_review_flags_never_travel(make_layer):
    from core.orchestration.layer_forge.orchestrator import layer_forge_home

    make_layer("acme.audit-l34", "1.0.0")
    path = layer_forge_home("_default") / "registry" / "acme.audit-l34@1.0.0.json"
    stored = json.loads(path.read_text())
    stored.update(review_flagged=True, review_flags=["x"], _promoted_at="t")
    path.write_text(json.dumps(stored))
    result = build_bundle(bundle_id="b", bundle_version="1.0.0", tenant_id="_default",
                          selections=[LayerSelection("acme.audit-l34", "1.0.0")])
    with zipfile.ZipFile(BytesIO(result.data)) as zf:
        manifest = json.loads(zf.read("artifacts/layer/acme.audit-l34@1.0.0/manifest.json"))
    assert not {"review_flagged", "review_flags", "_promoted_at", "status"} & set(manifest)


def test_a_forge_internal_error_becomes_an_export_error(make_skill):
    folder = make_skill("summarize", "1.0.0")
    import shutil
    shutil.rmtree(folder / "hooks")  # SkillPackager raises ValueError
    with pytest.raises(ExportError, match="cannot be packaged") as exc:
        build_bundle(bundle_id="b", bundle_version="1.0.0", tenant_id="_default",
                     selections=[SkillSelection(skill_id="summarize", version="1.0.0")])
    assert "/" not in str(exc.value).split("(")[-1]  # no host path in the message

def test_requirement_without_version_accepts_any(make_skill, make_tool):
    from core.forge_bundle.envelope import Requirement

    make_skill("summarize", "1.0.0")
    make_tool("csv.count")
    result = build_bundle(
        bundle_id="b", bundle_version="1.0.0", tenant_id="_default",
        selections=[
            SkillSelection(skill_id="summarize", version="1.0.0"),
            ToolSelection(name="csv.count", version="0.2.0",
                         requires=(Requirement(kind="skill", id="summarize", version=None),)),
        ],
    )
    with zipfile.ZipFile(BytesIO(result.data)) as zf:
        envelope = json.loads(zf.read("forge-bundle.json"))
    tool_requires = next(a for a in envelope["artifacts"] if a["kind"] == "tool")["requires"]
    assert tool_requires == [{"kind": "skill", "id": "summarize"}]  # no "version" key at all


def test_self_validation_rejects_a_secret_leaking_tool(make_tool):
    make_tool("csv.count", impl_body='TOKEN = "AKIAABCDEFGHIJKLMNOP"\n')
    with pytest.raises(ExportError, match="failed self-validation"):
        build_bundle(
            bundle_id="b", bundle_version="1.0.0", tenant_id="_default",
            selections=[ToolSelection(name="csv.count", version="0.2.0")],
        )


def test_bad_requirement_kind_rejected_at_construction():
    from core.forge_bundle.envelope import Requirement

    with pytest.raises(ExportError, match="unknown kind"):
        ToolSelection(
            name="csv.count", version="0.2.0",
            requires=(Requirement(kind="theme", id="x", version=None),),
        )
