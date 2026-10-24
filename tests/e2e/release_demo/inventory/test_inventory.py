"""W-0b: the release-readiness inventory is derived from code and every entry is mapped.

Run: python3 -m pytest tests/e2e/release_demo/inventory -q
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
sys.path.insert(0, str(HERE))

import check  # noqa: E402
import generate  # noqa: E402


def test_nav_groups_static_entries_are_read_from_layout():
    text = (REPO / generate.WEB_REL / "src/components/layout.tsx").read_text(encoding="utf-8")
    nav = generate.parse_nav_groups(text)
    # The plan quotes 28 static entries (27 after M-15); the parser must find exactly the block's entries.
    assert len(nav) == 28, [n["to"] for n in nav]
    assert any(n["to"] == "/app/chat" and n["label"] == "Chat" for n in nav)


def test_panels_are_read_from_registry_and_exclusions_are_present():
    text = (REPO / generate.WEB_REL / "src/panels/registry.tsx").read_text(encoding="utf-8")
    panels = generate.parse_panels(text)
    ids = {p["id"] for p in panels}
    assert "settings/github" in ids and "layer-forge-analytics" in ids
    for pid in generate.EXCLUDED_PANELS:
        assert pid in ids, f"stale exclusion {pid}: not registered in PANELS"


def test_every_tab_array_is_found_and_non_empty():
    inv = generate.build_inventory(REPO)
    owners = {e["owner"] for e in inv["entries"] if e["kind"] == "button-tab"}
    assert {"/app/vibe-engineering", "/app/initiatives", "/app/compute", "/app/forge", "manifest:video_producer"} <= owners
    counts = {}
    for e in inv["entries"]:
        if e["kind"] == "button-tab":
            counts[e["owner"]] = counts.get(e["owner"], 0) + 1
    assert counts["/app/vibe-engineering"] == 3 + 3  # TAB_IDS + MaturityDashboard DashboardTab
    assert counts["/app/initiatives"] == 6
    assert counts["/app/compute"] == 7
    assert counts["/app/forge"] == 11 + 4  # FORGE_TABS (11) and GENERATOR_SUBTABS (4)
    assert counts["/app/marketplace"] == 6  # pages/marketplace/tabs.ts TAB_IDS
    assert counts["/app/models"] == 4  # pages/models/tabs.ts TAB_IDS
    assert counts["/app/skill-manager"] == 3  # SkillManagerDashboard TAB_IDS
    assert counts["/app/compliance"] == 9  # compliance FILTER_TABS (incl. the "" = all key)
    assert counts["manifest:video_producer"] == 5  # STUDIO_TABS


def test_every_app_route_is_inventoried_with_its_nesting():
    inv = generate.build_inventory(REPO)
    routes = {e["id"] for e in inv["entries"] if e["kind"] == "route"}
    for rid in ("route:/app/chat/group/:groupId", "route:/app/chat/peer/:peerId",
                "route:/app/workflows/:wid/runs/:rid", "route:/login", "route:/*", "route:/app#index"):
        assert rid in routes, rid
    redirects = {e["id"]: e["redirect"] for e in inv["entries"] if e["kind"] == "route"}
    assert redirects["route:/app/layer-forge"] == "/app/forge?tab=layers"


def test_a_route_inside_a_comment_is_not_a_route():
    text = '/* docs mention <Route> here */\n<Routes>\n  <Route path="/app" element={<X />}>\n    <Route path="a" element={<A />} />\n  </Route>\n</Routes>\n'
    paths = [r["path"] for r in generate.parse_routes(generate.strip_comments(text))]
    assert paths == ["/app", "/app/a"]


def test_an_unreadable_route_fails_loudly():
    text = '<Route path="/app" element={<X />}>\n  <Route element={<Y />} />\n</Route>\n'
    with pytest.raises(generate.InventoryError):
        generate.parse_routes(text)


def test_an_unknown_tab_declaration_fails_loudly():
    with pytest.raises(generate.InventoryError, match="NEW_PANEL_TABS"):
        generate.check_tab_declarations({"src/pages/new.tsx": "const NEW_PANEL_TABS = ['a', 'b'];"})


def test_an_unreadable_tabs_trigger_fails_loudly():
    with pytest.raises(generate.InventoryError):
        generate.parse_tabs_triggers("src/x.tsx", '<TabsTrigger className="x">A</TabsTrigger>')
    with pytest.raises(generate.InventoryError, match="DYNAMIC_TRIGGER_SOURCES"):
        generate.parse_tabs_triggers("src/x.tsx", "<TabsTrigger value={id}>A</TabsTrigger>")
    statics, dyn = generate.parse_tabs_triggers("src/x.tsx", '<TabsTrigger\n  onClick={() => go()}\n  value="one">A</TabsTrigger>')
    assert statics == ["one"] and dyn == 0


def test_every_entry_has_a_unique_explicit_slug():
    inv = json.loads((HERE / "inventory.json").read_text(encoding="utf-8"))
    slugs = {e["id"]: e.get("slug") for e in inv["entries"]}
    assert all(s and generate.SLUG_RE.match(s) for s in slugs.values())
    assert len(set(slugs.values())) == len(slugs)
    # The plain capture-slug rule collides on these two; the explicit slug does not.
    assert generate.naive_slug("route:/") == generate.naive_slug("route:/*") == "route"
    assert slugs["route:/"] != slugs["route:/*"]
    assert slugs["page:/app/chat"] == "page-app-chat"  # no hash when the plain slug is already unique


def test_a_duplicate_slug_fails_the_check(tmp_path: Path):
    inv, amap = _copy_fixture(tmp_path)
    data = json.loads(inv.read_text(encoding="utf-8"))
    data["entries"][1]["slug"] = data["entries"][0]["slug"]
    inv.write_text(json.dumps(data), encoding="utf-8")
    errors, _ = check.check(REPO, inventory_path=inv, map_path=amap)
    assert any(e.startswith("slug:") and "shared by" in e for e in errors), errors[:5]


def test_extra_tab_shapes_are_inventoried():
    inv = generate.build_inventory(REPO)
    ids = {e["id"] for e in inv["entries"]}
    for must in (
        "button-tab:/app/rag-hub#discover", "button-tab:/app/rag-hub#trending", "button-tab:/app/rag-hub#top-rated",
        "button-tab:/app/vibe-engineering#maturity:radar", "button-tab:/app/vibe-engineering#maturity:summary",
        "button-tab:/app/vibe-engineering#maturity:patterns",
        "button-tab:/app/chat#sidebar:peers",
        "button-tab:src:src/components/GenericPluginInspector.tsx#audit",
        "button-tab:src:src/components/SkillInspector.tsx#learning",
        "button-tab:/app/workflows#run:media",
    ):
        assert must in ids, must


def test_unresolved_tab_shapes_fail_loudly():
    cases = {
        'role="tab"': '<button role="tab">A</button>',
        "lowercase tabs array": "const panelTabs = ['a', 'b'];",
        "tab type union": "type FooTab = 'a' | 'b';",
        "tab-named state union": "const [tab, setTab] = React.useState<'a' | 'b'>('a');",
    }
    for what, text in cases.items():
        with pytest.raises(generate.InventoryError):
            generate.check_tab_declarations({"src/pages/unknown.tsx": text}, check_stale=False)
        # a non-tab union is not a candidate
    generate.check_tab_declarations({"src/x.tsx": "const [status, setStatus] = useState<'idle' | 'done'>('idle');"},
                                    check_stale=False)


def test_manifest_panels_and_your_panels_are_inventoried():
    inv = generate.build_inventory(REPO)
    ids = {e["id"] for e in inv["entries"]}
    assert {"manifest:video_producer", "manifest:corvin_knowledge", "ai-panels:your-panels"} <= ids
    assert any(e["kind"] == "tabs-trigger" for e in inv["entries"])


def test_a_missing_tab_array_fails_loudly(tmp_path: Path):
    text = "export const nothing = 1;\n"
    with pytest.raises(generate.InventoryError):
        generate.parse_tab_array(text, r"const FORGE_TABS = \[([^\]]*)\]", r"'([a-z-]+)'", tag="FORGE_TABS")


def test_redirect_and_static_tab_deeplinks_are_inventoried():
    inv = generate.build_inventory(REPO)
    deeplinks = {e["id"] for e in inv["entries"] if e["kind"] == "deeplink"}
    assert "deeplink:/app/forge?tab=layers" in deeplinks
    assert "deeplink:/app/models?tab=routing" in deeplinks
    assert "deeplink:/app/vibe-engineering?tab=loops" in deeplinks


def test_committed_inventory_matches_the_generator():
    """Drift gate: the committed inventory.json must be exactly what the generator produces now."""
    errors, _ = check.check(REPO)
    assert not [e for e in errors if e.startswith("drift")], errors


def test_every_entry_has_a_mapping_on_the_real_repo():
    errors, summary = check.check(REPO)
    assert not [e for e in errors if e.startswith("unmapped")], errors[:5]
    assert summary["entries"] > 0
    assert summary["entries"] == summary["mapped"] + summary["pending"]


def _copy_fixture(tmp_path: Path) -> tuple[Path, Path]:
    inv = tmp_path / "inventory.json"
    amap = tmp_path / "assertion_map.json"
    shutil.copy(HERE / "inventory.json", inv)
    shutil.copy(HERE / "assertion_map.json", amap)
    return inv, amap


def test_missing_mapping_fails_the_check(tmp_path: Path):
    inv, amap = _copy_fixture(tmp_path)
    data = json.loads(amap.read_text(encoding="utf-8"))
    removed = sorted(data["mappings"])[0]
    del data["mappings"][removed]
    amap.write_text(json.dumps(data), encoding="utf-8")
    errors, _ = check.check(REPO, inventory_path=inv, map_path=amap)
    assert any(removed in e and e.startswith("unmapped") for e in errors), errors[:5]


def test_a_new_inventory_entry_without_mapping_fails(tmp_path: Path):
    inv, amap = _copy_fixture(tmp_path)
    data = json.loads(inv.read_text(encoding="utf-8"))
    data["entries"].append({"id": "page:/app/new-unmapped-panel", "kind": "page", "route": "/app/new-unmapped-panel", "label": "x", "nav": True, "panel": False, "owner": None, "sources": []})
    inv.write_text(json.dumps(data), encoding="utf-8")
    errors, _ = check.check(REPO, inventory_path=inv, map_path=amap)
    assert any("page:/app/new-unmapped-panel" in e and e.startswith("unmapped") for e in errors), errors[:5]


def test_stale_mapping_fails(tmp_path: Path):
    inv, amap = _copy_fixture(tmp_path)
    data = json.loads(amap.read_text(encoding="utf-8"))
    data["mappings"]["page:/app/gone"] = {"status": "pending", "reason": "x"}
    amap.write_text(json.dumps(data), encoding="utf-8")
    errors, _ = check.check(REPO, inventory_path=inv, map_path=amap)
    assert any("stale" in e and "page:/app/gone" in e for e in errors), errors[:5]


def test_pending_without_reason_fails(tmp_path: Path):
    inv, amap = _copy_fixture(tmp_path)
    data = json.loads(amap.read_text(encoding="utf-8"))
    key = sorted(data["mappings"])[0]
    data["mappings"][key] = {"status": "pending"}
    amap.write_text(json.dumps(data), encoding="utf-8")
    errors, _ = check.check(REPO, inventory_path=inv, map_path=amap)
    assert any("no reason" in e and key in e for e in errors), errors[:5]


def test_drift_fails_the_check(tmp_path: Path):
    inv, amap = _copy_fixture(tmp_path)
    data = json.loads(inv.read_text(encoding="utf-8"))
    data["entries"] = [e for e in data["entries"] if e["kind"] != "deeplink"]
    inv.write_text(json.dumps(data), encoding="utf-8")
    errors, _ = check.check(REPO, inventory_path=inv, map_path=amap)
    assert any(e.startswith("drift") for e in errors), errors[:5]


def test_check_cli_exits_zero_on_the_committed_state():
    r = subprocess.run(
        [sys.executable, str(HERE / "check.py")],
        cwd=REPO, capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, r.stdout + r.stderr
