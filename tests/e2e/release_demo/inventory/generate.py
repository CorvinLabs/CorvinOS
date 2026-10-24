"""Derive the console release-readiness inventory from the web-next source (W-0b).

The inventory lists every page a user can reach and every tab a page declares. It is generated
from code, not typed by hand:

  page          static sidebar entries (NAV_GROUPS, src/components/layout.tsx) and registered
                panels (PANELS rc("<route>", ...), src/panels/registry.tsx), merged by route
  route         every <Route path=...> in src/App.tsx (redirects carry their target)
  deeplink      <Navigate to="/app..."/> targets in App.tsx and static "/app/...?tab=..." strings in src/
  button-tab    declared tab arrays (TAB_IDS / TABS / FILTER_TABS / VIEWS / *_TABS) under src/
  tabs-trigger  static <TabsTrigger value="..."> under src/ that no declared array already covers
  manifest      default plugin panels from core/console/corvin_console/default_plugins.yaml
  ai-panels     the runtime "Your panels" sidebar group (layout.tsx, useAiPanels)

Completeness is enforced, not hoped for: a tab-array declaration under src/ that is neither parsed
(TAB_SOURCES) nor listed with a reason (TAB_DECLS_IGNORED) is an error; a <TabsTrigger> opening or a
<Route> opening the parser cannot read is an error; a declaration it must find and does not is an
error. A parser that silently finds nothing would make the inventory look complete.

Usage: python3 tests/e2e/release_demo/inventory/generate.py   (writes inventory.json next to this file)
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

WEB_REL = "core/console/corvin_console/web-next"
DEFAULT_PLUGINS_REL = "core/console/corvin_console/default_plugins.yaml"
SCHEMA = 2
HERE = Path(__file__).resolve().parent

# Panels registered in PANELS but deliberately not inventoried. Each reason cites the source.
# Every id here must still be registered in PANELS (checked): a removed panel must not leave a
# stale exclusion behind.
EXCLUDED_PANELS: dict[str, str] = {
    "voice-summaries": "hidden from the sidebar (registry.tsx:116; NAV_EXEMPT, panel-nav-wiring.test.ts:60); route stays mounted for deep links",
    "flows": "hidden from the sidebar (registry.tsx:152; NAV_EXEMPT, panel-nav-wiring.test.ts:62)",
    "orgs": "hidden from the sidebar (registry.tsx:145; NAV_EXEMPT, panel-nav-wiring.test.ts:56)",
    "people": "hidden from the sidebar (registry.tsx:146; NAV_EXEMPT, panel-nav-wiring.test.ts:63)",
    "control-intent-router": "ADR-2029 control-plane panel with no backend action (registry.tsx:202; panel-nav-wiring.test.ts:79)",
    "control-plane-plugins": "ADR-2029 control-plane panel with no backend action (registry.tsx:203; panel-nav-wiring.test.ts:80)",
    "control-plane-subsystems": "ADR-2029 control-plane panel with no backend action (registry.tsx:204; panel-nav-wiring.test.ts:81)",
    "control-plane-overrides": "ADR-2029 control-plane panel with no backend action (registry.tsx:205; panel-nav-wiring.test.ts:82)",
    "control-plane-snapshots": "ADR-2029 control-plane panel with no backend action (registry.tsx:206; panel-nav-wiring.test.ts:83)",
}

# Plan exclusions that are NOT registered in PANELS today, so they cannot appear as pages.
# Recorded for the reader only; not stale-checked.
EXCLUDED_NOT_REGISTERED: dict[str, str] = {
    "video-quality-metrics": "NAV_EXEMPT: folded into the Video Producer studio Quality tab (panel-nav-wiring.test.ts:35)",
    "audit": "NAV_EXEMPT: backend routes 404 on the live host (panel-nav-wiring.test.ts:43)",
    "releases": "NAV_EXEMPT: backend routes 404 on the live host (panel-nav-wiring.test.ts:44)",
    "webhooks": "NAV_EXEMPT: removed; the Register Webhook button never called the GitHub API (panel-nav-wiring.test.ts:53)",
    "space": "NAV_EXEMPT: removed 2026-09-15 (registry.tsx comment)",
    "agents": "NAV_EXEMPT (panel-nav-wiring.test.ts:61)",
}

QUOTED = r"['\"]([A-Za-z0-9_-]+)['\"]"

# Declared tab arrays: (file, declared name, declaration regex, item regex, owner, value prefix).
# Owner is the route the array renders on, or "manifest:<registry_id>" for a plugin panel whose
# route is not in web-next source (the studio is loaded from the plugin manifest).
TAB_SOURCES: list[tuple[str, str, str, str, str, str]] = [
    ("src/pages/vibe-engineering/VibeDashboard.tsx", "TAB_IDS",
     r"const TAB_IDS: readonly TabType\[\] = \[([^\]]*)\]", QUOTED, "/app/vibe-engineering", ""),
    ("src/pages/tasks/index.tsx", "VIEWS",
     r"(?s)const VIEWS: \{[^=]*= \[(.*?)\n\];", r"id: \"([A-Za-z0-9_-]+)\"", "/app/initiatives", ""),
    ("src/pages/compute.tsx", "TABS",
     r"(?s)const TABS: [^=]*=\s*\[(.*?)\n  \];", r"key: \"([A-Za-z0-9_-]+)\"", "/app/compute", ""),
    ("src/pages/forge.tsx", "FORGE_TABS",
     r"const FORGE_TABS = \[([^\]]*)\]", QUOTED, "/app/forge", ""),
    ("src/pages/forge.tsx", "GENERATOR_SUBTABS",
     r"const GENERATOR_SUBTABS = \[([^\]]*)\]", QUOTED, "/app/forge", "generator:"),
    ("src/pages/video-producer.tsx", "STUDIO_TABS",
     r"const STUDIO_TABS = \[([^\]]*)\]", QUOTED, "manifest:video_producer", ""),
    ("src/pages/marketplace/tabs.ts", "TAB_IDS",
     r"export const TAB_IDS = \[([^\]]*)\]", QUOTED, "/app/marketplace", ""),
    ("src/pages/models/tabs.ts", "TAB_IDS",
     r"export const TAB_IDS = \[([^\]]*)\]", QUOTED, "/app/models", ""),
    ("src/pages/skills/SkillManagerDashboard.tsx", "TAB_IDS",
     r"const TAB_IDS: readonly TabType\[\] = \[([^\]]*)\]", QUOTED, "/app/skill-manager", ""),
    ("src/pages/compliance.tsx", "FILTER_TABS",
     r"(?s)const FILTER_TABS = \[(.*?)\n\] as const;", r"key: \"([A-Za-z0-9_-]*)\"", "/app/compliance", "filter:"),
]

TAB_SOURCES += [
    ("src/pages/rag-hub.tsx", "tabs",
     r"(?s)const tabs: RAGHubTab\[\] = \[(.*?)\n  \];", r"id: \"([A-Za-z0-9_-]+)\"", "/app/rag-hub", ""),
    ("src/components/chat/ChatContextSidebar.tsx", "MODES",
     r"const MODES: SidebarMode\[\] = \[([^\]]*)\]", QUOTED, "/app/chat", "sidebar:"),
]

# Tab-array declarations under src/ that are deliberately not parsed (file, name) -> reason.
TAB_DECLS_IGNORED: dict[tuple[str, str], str] = {}

# String-literal unions that define a tab set: a `type *Tab*/*View* = 'a' | 'b'` alias or a
# tab/view-named `useState<'a' | 'b'>`. Every one found under src/ is either parsed here
# (file, shape, name) -> (owner, value prefix) or ignored in UNIONS_IGNORED with a reason.
UNION_SOURCES: dict[tuple[str, str, str], tuple[str, str]] = {
    ("src/pages/vibe-engineering/components/MaturityDashboard.tsx", "type", "DashboardTab"): ("/app/vibe-engineering", "maturity:"),
    ("src/components/WdatAuditPanel.tsx", "type", "AuditView"): ("/app/chat", "audit-view:"),
    ("src/components/WdatAuditPanel.tsx", "state", "osView"): ("/app/chat", "audit-os-view:"),
    ("src/components/GenericPluginInspector.tsx", "state", "activeTab"): ("src:src/components/GenericPluginInspector.tsx", ""),
    ("src/components/SkillInspector.tsx", "state", "activeTab"): ("src:src/components/SkillInspector.tsx", ""),
    ("src/pages/chat.tsx", "state", "auditTab"): ("/app/chat", "audit:"),
    ("src/pages/workflows.tsx", "state", "inspectorTab"): ("/app/workflows", "inspector:"),
    ("src/pages/workflows.tsx", "state", "runTab"): ("/app/workflows", "run:"),
}
UNIONS_IGNORED: dict[tuple[str, str, str], str] = {
    ("src/pages/compute.tsx", "type", "Tab"): "same values as the compute TABS array (parsed)",
    ("src/pages/skills/SkillManagerDashboard.tsx", "type", "TabType"): "same values as SkillManagerDashboard TAB_IDS (parsed)",
    ("src/pages/tasks/index.tsx", "type", "View"): "same values as the tasks VIEWS array (parsed)",
    ("src/pages/vibe-engineering/VibeDashboard.tsx", "type", "TabType"): "same values as VibeDashboard TAB_IDS (parsed)",
    ("src/lib/api/video-assets.ts", "type", "CaptureViewport"): "API parameter (capture viewport), not a UI tab",
    ("src/app/console/flows/page.tsx", "type", "DetailView"): "flows panel is excluded (EXCLUDED_PANELS)",
    ("src/components/FlowCreatorPanel.tsx", "state", "tab"): "used only by app/console/flows/page.tsx; flows panel is excluded",
}

# Files with role="tab": the parsed declaration that renders those tabs, or a reason.
ROLE_TAB_FILES: dict[str, str] = {
    "src/pages/video-producer.tsx": "parsed: STUDIO_TABS",
    "src/pages/tasks/index.tsx": "parsed: VIEWS",
    "src/components/chat/ChatContextSidebar.tsx": "parsed: MODES",
    "src/pages/tasks/graph-view.tsx": "ignored: the tabs are the tenant's initiatives at runtime (data), not a declared set",
}

_STR = r"['\"][A-Za-z0-9_ -]+['\"]"
_UNION = rf"{_STR}(?:\s*\|\s*{_STR})+"
TYPE_UNION = re.compile(rf"\btype\s+(\w*(?:Tab|View)\w*)\s*=\s*({_UNION})")
STATE_UNION = re.compile(rf"const\s+\[\s*(\w*(?:[Tt]ab|[Vv]iew)\w*)\s*,\s*\w+\s*\]\s*=\s*(?:React\.)?useState<\s*({_UNION})\s*>")
LOWER_TAB_ARRAY = re.compile(r"\b(?:const|let)\s+((?:[a-z]\w*)?(?:tabs|Tabs|views|Views))\b\s*(?::[^=]*)?=\s*\[")
ROLE_TAB = re.compile(r"role=[\"']tab[\"']")

# Files whose <TabsTrigger value={...}> is dynamic, mapped to the declaration that feeds it.
DYNAMIC_TRIGGER_SOURCES: dict[str, tuple[str, str]] = {
    "src/pages/marketplace/index.tsx": ("src/pages/marketplace/tabs.ts", "TAB_IDS"),
    "src/pages/models/index.tsx": ("src/pages/models/tabs.ts", "TAB_IDS"),
}

# Files whose static <TabsTrigger> values are not inventoried, with the reason.
TRIGGER_FILES_IGNORED: dict[str, str] = {
    "src/pages/orgs.tsx": "orgs panel is excluded (hidden from the sidebar; EXCLUDED_PANELS)",
}

NAV_ITEM = re.compile(r'\{\s*to:\s*"(/app/[^"]+)",\s*label:\s*"([^"]+)"')
PANEL_RC = re.compile(r'\brc\(\s*"([^"]+)",\s*"([^"]*)"')
APP_REDIRECT = re.compile(r'<Navigate\s+to="(/app[^"]*)"')
STATIC_TAB_LINK = re.compile(r'"(/app/[A-Za-z0-9/_-]+\?tab=[A-Za-z0-9_&=-]+)"')
TAB_DECL = re.compile(r"\b(?:const|let) ([A-Z][A-Z_]*(?:TABS|TAB_IDS|VIEWS))\b\s*(?::[^=]*)?=\s*\[")
TRIGGER_OPEN = re.compile(r"<TabsTrigger\b")
# Attribute text of an opening tag: up to the first '>' that is not the tail of '=>'.
TRIGGER_TAG = re.compile(r"<TabsTrigger\b((?:[^>]|(?<==)>)*)>", re.S)
ROUTE_OPEN = re.compile(r"<Route\b(?!s)")
ROUTE_HEAD = re.compile(r"<Route\b(?!s)\s+(?:key=\{[^}]*\}\s+)?(?:(index)\b|path=(?:\"([^\"]+)\"|\{([^}]+)\}))", re.S)
ROUTE_DYNAMIC_OK = {"p.id": "PANELS routes (panelRoutes); inventoried as pages"}


class InventoryError(RuntimeError):
    """A declaration the parser must find is missing, empty or unreadable."""


def strip_comments(text: str) -> str:
    """Drop /* */ (incl. JSX {/* */}) blocks and whole-line // comments; strings are left alone."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


def parse_nav_groups(text: str) -> list[dict]:
    m = re.search(r"const NAV_GROUPS: NavGroup\[\] = \[(.*?)\n\];", text, re.S)
    if not m:
        raise InventoryError("NAV_GROUPS declaration not found in layout.tsx")
    items = [{"to": to, "label": label} for to, label in NAV_ITEM.findall(m.group(1))]
    if not items:
        raise InventoryError("NAV_GROUPS has no static entries")
    return items


def parse_panels(text: str) -> list[dict]:
    panels = [{"id": pid, "label": label} for pid, label in PANEL_RC.findall(text)]
    if not panels:
        raise InventoryError("PANELS has no rc(...) entries in registry.tsx")
    return panels


def parse_tab_array(text: str, decl: str, item: str, tag: str) -> list[str]:
    m = re.search(decl, text, re.S)
    if not m:
        raise InventoryError(f"tab array {tag} not found")
    values = re.findall(item, m.group(1))
    if not values:
        raise InventoryError(f"tab array {tag} has no items")
    return values


def parse_app_redirects(text: str) -> list[str]:
    return list(dict.fromkeys(APP_REDIRECT.findall(text)))


def parse_static_tab_links(texts: list[str]) -> list[str]:
    found: set[str] = set()
    for t in texts:
        found.update(STATIC_TAB_LINK.findall(t))
    return sorted(found)


def parse_routes(text: str) -> list[dict]:
    """Every <Route> in App.tsx. A relative path is nested under /app when its <Route> is indented
    deeper than the <Route path="/app"> opening (the only parent route); otherwise it is top level."""
    opens = [m.start() for m in ROUTE_OPEN.finditer(text)]
    heads = {m.start(): m for m in ROUTE_HEAD.finditer(text)}

    def col(pos: int) -> int:
        return pos - (text.rfind("\n", 0, pos) + 1)

    app_cols = [col(pos) for pos, m in heads.items() if m.group(2) == "/app"]
    if len(app_cols) != 1:
        raise InventoryError(f'expected exactly one <Route path="/app">, found {len(app_cols)}')
    app_col = app_cols[0]
    routes: list[dict] = []
    for i, pos in enumerate(opens):
        m = heads.get(pos)
        if m is None:
            raise InventoryError(f"unreadable <Route> at App.tsx offset {pos}: {text[pos:pos + 80]!r}")
        index, static, dynamic = m.group(1), m.group(2), m.group(3)
        end = opens[i + 1] if i + 1 < len(opens) else len(text)
        nav = re.search(r'<Navigate\s+to="([^"]+)"', text[m.end():end])
        redirect = nav.group(1) if nav else None
        if dynamic is not None:
            if dynamic.strip() not in ROUTE_DYNAMIC_OK:
                raise InventoryError(f"dynamic <Route path={{{dynamic}}}> is not on ROUTE_DYNAMIC_OK")
            continue
        if index:
            routes.append({"path": "/app", "index": True, "redirect": redirect})
            continue
        if static.startswith("/"):
            full = static
        elif col(pos) > app_col:
            full = f"/app/{static}"
        else:
            full = f"/{static}"
        routes.append({"path": full, "index": False, "redirect": redirect})
    if not routes:
        raise InventoryError("App.tsx has no <Route> entries")
    return routes


def _union_values(union: str) -> list[str]:
    return re.findall(r"['\"]([A-Za-z0-9_ -]+)['\"]", union)


def find_union_tabs(src_texts: dict[str, str]) -> list[tuple[str, str, str, list[str]]]:
    """(file, "type"|"state", name, values) for every tab/view string-literal union under src/."""
    out = []
    for rel, text in sorted(src_texts.items()):
        for m in TYPE_UNION.finditer(text):
            out.append((rel, "type", m.group(1), _union_values(m.group(2))))
        for m in STATE_UNION.finditer(text):
            out.append((rel, "state", m.group(1), _union_values(m.group(2))))
    return out


def check_tab_declarations(src_texts: dict[str, str], *, check_stale: bool = True) -> None:
    """Every tab declaration under src/ is parsed or ignored with a reason; no listed one is stale.

    Shapes: UPPER_CASE *_TABS / *TAB_IDS / *VIEWS arrays (const or let), lowercase *tabs / *views
    arrays, role="tab" elements, `type *Tab*|*View* = 'a' | 'b'` aliases and tab/view-named
    `useState<'a' | 'b'>` states."""
    arrays = {(f, name) for f, name, *_ in TAB_SOURCES}
    known_arrays = arrays | set(TAB_DECLS_IGNORED)
    for rel, text in sorted(src_texts.items()):
        for pat in (TAB_DECL, LOWER_TAB_ARRAY):
            for m in pat.finditer(text):
                if (rel, m.group(1)) not in known_arrays:
                    raise InventoryError(f"tab-array declaration {m.group(1)} in {rel} is not in TAB_SOURCES or TAB_DECLS_IGNORED")
        if ROLE_TAB.search(text):
            res = ROLE_TAB_FILES.get(rel)
            if res is None:
                raise InventoryError(f'role="tab" in {rel} is not resolved in ROLE_TAB_FILES (parse its declaration or give a reason)')
            if res.startswith("parsed: ") and (rel, res[len("parsed: "):]) not in arrays:
                raise InventoryError(f"ROLE_TAB_FILES[{rel}] names {res!r}, which is not in TAB_SOURCES")
    found = set()
    for rel, shape, name, _values in find_union_tabs(src_texts):
        found.add((rel, shape, name))
        if (rel, shape, name) not in UNION_SOURCES and (rel, shape, name) not in UNIONS_IGNORED:
            raise InventoryError(f"tab {shape} union {name} in {rel} is not in UNION_SOURCES or UNIONS_IGNORED")
    if not check_stale:
        return
    for key in list(UNION_SOURCES) + list(UNIONS_IGNORED):
        if key not in found:
            raise InventoryError(f"stale tab-union entry {key}: no longer found in src/")
    for rel in ROLE_TAB_FILES:
        if rel not in src_texts or not ROLE_TAB.search(src_texts[rel]):
            raise InventoryError(f'stale ROLE_TAB_FILES entry {rel}: no role="tab" there any more')


def parse_tabs_triggers(rel: str, text: str) -> tuple[list[str], int]:
    """Static values of every <TabsTrigger> in a file, and the count of dynamic ones."""
    opens = len(TRIGGER_OPEN.findall(text))
    statics: list[str] = []
    dynamic = 0
    tags = TRIGGER_TAG.findall(text)
    if len(tags) != opens:
        raise InventoryError(f"{rel}: {opens} <TabsTrigger> openings but {len(tags)} readable tags")
    for attrs in tags:
        s = re.search(r'\bvalue="([^"]+)"', attrs)
        if s:
            statics.append(s.group(1))
        elif re.search(r"\bvalue=\{", attrs):
            dynamic += 1
        else:
            raise InventoryError(f"{rel}: <TabsTrigger> without a readable value: {attrs.strip()[:80]!r}")
    if dynamic and rel not in DYNAMIC_TRIGGER_SOURCES:
        raise InventoryError(f"{rel}: dynamic <TabsTrigger value={{...}}> not mapped in DYNAMIC_TRIGGER_SOURCES")
    return statics, dynamic


def parse_default_plugins(text: str) -> list[dict]:
    """registry_id + name of every plugin in default_plugins.yaml (line parser; no PyYAML in CI)."""
    plugins: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("- index_id:"):
            cur = {"index_id": s.split(":", 1)[1].strip()}
            plugins.append(cur)
        elif cur is not None and s.startswith("registry_id:"):
            cur["registry_id"] = s.split(":", 1)[1].split("#", 1)[0].strip()
        elif cur is not None and s.startswith("name:"):
            cur["name"] = s.split(":", 1)[1].strip()
    plugins = [p for p in plugins if p.get("registry_id") and p.get("name")]
    if not plugins:
        raise InventoryError("default_plugins.yaml lists no plugins with registry_id and name")
    return plugins


SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SLUG_MAX = 96


def naive_slug(entry_id: str) -> str:
    """The plain capture-slug rule (lowercase, every non-alphanumeric run -> '-'); NOT unique:
    'route:/' and 'route:/*' both give 'route'."""
    return re.sub(r"[^a-z0-9]+", "-", entry_id.lower()).strip("-")


def assign_slugs(entries: list[dict]) -> None:
    """Give every entry an explicit, unique, filesystem-safe `slug` (<kind>-<path>, plus a short hash
    of the id when the plain slug collides, is only the kind, or is longer than SLUG_MAX).
    Consumers (capture names, gate rows) read this field; they never re-derive it."""
    base = {e["id"]: naive_slug(e["id"]) for e in entries}
    count: dict[str, int] = {}
    for b in base.values():
        count[b] = count.get(b, 0) + 1
    for e in entries:
        b = base[e["id"]]
        needs_hash = count[b] > 1 or b == naive_slug(e["kind"]) or len(b) > SLUG_MAX
        if needs_hash:
            h = hashlib.sha1(e["id"].encode("utf-8")).hexdigest()[:8]
            b = f"{b[:SLUG_MAX - 9]}-{h}".strip("-")
        e["slug"] = b
    slugs = [e["slug"] for e in entries]
    if len(slugs) != len(set(slugs)):
        raise InventoryError("slug collision after hashing")
    bad = [s for s in slugs if not SLUG_RE.match(s)]
    if bad:
        raise InventoryError(f"malformed slugs: {bad[:5]}")


def _src_files(web: Path) -> list[Path]:
    out = []
    for p in sorted((web / "src").rglob("*")):
        if p.suffix not in (".ts", ".tsx"):
            continue
        rel = p.relative_to(web).as_posix()
        if "/tests/" in "/" + rel or "node_modules" in rel or ".test." in p.name:
            continue
        out.append(p)
    return out


def _entry(eid: str, kind: str, label: str, sources: list[str], route: str | None = None,
           owner: str | None = None, nav: bool = False, panel: bool = False, **extra) -> dict:
    e = {"id": eid, "kind": kind, "route": route, "label": label, "nav": nav, "panel": panel,
         "owner": owner, "sources": sources}
    e.update(extra)
    return e


def build_inventory(repo_root: Path) -> dict:
    web = repo_root / WEB_REL
    raw_texts = {p.relative_to(web).as_posix(): p.read_text(encoding="utf-8") for p in _src_files(web)}
    # Parse code, not prose: a <Route> or <TabsTrigger> inside a comment is not a declaration.
    src_texts = {rel: strip_comments(t) for rel, t in raw_texts.items()}
    layout = raw_texts["src/components/layout.tsx"]
    registry = src_texts["src/panels/registry.tsx"]
    app = src_texts["src/App.tsx"]

    nav = parse_nav_groups(layout)
    panels_all = parse_panels(registry)
    registered = {p["id"] for p in panels_all}
    for pid in EXCLUDED_PANELS:
        if pid not in registered:
            raise InventoryError(f"stale exclusion: {pid} is no longer registered in PANELS; remove it from EXCLUDED_PANELS")
    panels = [p for p in panels_all if p["id"] not in EXCLUDED_PANELS]

    entries: list[dict] = []

    # pages
    pages: dict[str, dict] = {}
    for n in nav:
        pages[n["to"]] = _entry(f"page:{n['to']}", "page", n["label"], ["src/components/layout.tsx NAV_GROUPS"],
                                route=n["to"], nav=True)
    for p in panels:
        route = f"/app/{p['id']}"
        if route in pages:
            pages[route]["panel"] = True
            pages[route]["sources"].append("src/panels/registry.tsx PANELS")
        else:
            pages[route] = _entry(f"page:{route}", "page", p["label"], ["src/panels/registry.tsx PANELS"],
                                  route=route, panel=True)
    entries.extend(pages.values())

    # routes
    routes = parse_routes(app)
    seen_routes: set[str] = set()
    for r in routes:
        rid = f"route:{r['path']}" + ("#index" if r["index"] else "")
        if rid in seen_routes:
            raise InventoryError(f"duplicate route {rid}")
        seen_routes.add(rid)
        entries.append(_entry(rid, "route", r["redirect"] or r["path"], ["src/App.tsx Route"],
                              route=r["path"], redirect=r["redirect"]))

    # deep links
    deeplinks: dict[str, set] = {}
    for target in parse_app_redirects(app):
        deeplinks.setdefault(target, set()).add("src/App.tsx Navigate")
    for target in parse_static_tab_links(list(src_texts.values())):
        deeplinks.setdefault(target, set()).add("src static ?tab= link")
    for target in sorted(deeplinks):
        entries.append(_entry(f"deeplink:{target}", "deeplink", target, sorted(deeplinks[target]),
                              route=target.split("?", 1)[0]))

    # declared tab arrays
    check_tab_declarations(src_texts)
    declared_values: dict[str, set[str]] = {}
    for rel, name, decl, item, owner, prefix in TAB_SOURCES:
        values = parse_tab_array(src_texts[rel], decl, item, tag=f"{rel}:{name}")
        declared_values.setdefault(rel, set()).update(values)
        for value in values:
            val = prefix + (value or "all")
            entries.append(_entry(f"button-tab:{owner}#{val}", "button-tab", val, [f"{rel} {name}"],
                                  route=owner if owner.startswith("/app") else None, owner=owner))

    # tab sets declared as string-literal unions (type alias or tab-named useState)
    for rel, shape, name, values in find_union_tabs(src_texts):
        if (rel, shape, name) not in UNION_SOURCES:
            continue
        owner, prefix = UNION_SOURCES[(rel, shape, name)]
        declared_values.setdefault(rel, set()).update(values)
        for value in values:
            val = prefix + value
            entries.append(_entry(f"button-tab:{owner}#{val}", "button-tab", val, [f"{rel} {shape} {name}"],
                                  route=owner if owner.startswith("/app") else None, owner=owner))

    # <TabsTrigger value="..."> not already covered by a declared array of the same file
    trigger_count = 0
    for rel, text in sorted(src_texts.items()):
        if "<TabsTrigger" not in text:
            continue
        statics, _dynamic = parse_tabs_triggers(rel, text)
        if rel in TRIGGER_FILES_IGNORED:
            continue
        covered = declared_values.get(rel, set())
        for value in dict.fromkeys(statics):
            if value in covered:
                continue
            trigger_count += 1
            entries.append(_entry(f"tabs-trigger:{rel}#{value}", "tabs-trigger", value, [f"{rel} <TabsTrigger>"],
                                  owner=f"src:{rel}"))

    # manifest panels (default plugins) and the runtime "Your panels" group
    default_plugins = parse_default_plugins((repo_root / DEFAULT_PLUGINS_REL).read_text(encoding="utf-8"))
    for p in default_plugins:
        entries.append(_entry(f"manifest:{p['registry_id']}", "manifest", p["name"], [DEFAULT_PLUGINS_REL],
                              owner="manifest", index_id=p["index_id"]))
    if 'label: "Your panels"' not in layout or "useAiPanels()" not in layout:
        raise InventoryError('the "Your panels" group (useAiPanels) is no longer in layout.tsx')
    entries.append(_entry("ai-panels:your-panels", "ai-panels", "Your panels",
                          ["src/components/layout.tsx useAiPanels (runtime /app/<ai panel id>)"], owner="ai-panels"))

    for e in entries:
        e["sources"] = sorted(set(e["sources"]))
    entries.sort(key=lambda e: e["id"])
    ids = [e["id"] for e in entries]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise InventoryError(f"duplicate inventory ids: {dupes[:5]}")
    assign_slugs(entries)

    excluded = [{"id": f"panel:{pid}", "reason": why, "registered": True} for pid, why in sorted(EXCLUDED_PANELS.items())]
    excluded += [{"id": f"panel:{pid}", "reason": why, "registered": False} for pid, why in sorted(EXCLUDED_NOT_REGISTERED.items())]
    excluded += [{"id": f"tabs-trigger:{rel}", "reason": why, "registered": True} for rel, why in sorted(TRIGGER_FILES_IGNORED.items())]

    key_files = sorted({"src/components/layout.tsx", "src/panels/registry.tsx", "src/App.tsx"} | {f for f, *_ in TAB_SOURCES})
    sources = {rel: hashlib.sha256(raw_texts[rel].encode("utf-8")).hexdigest() for rel in key_files}
    sources[DEFAULT_PLUGINS_REL] = hashlib.sha256((repo_root / DEFAULT_PLUGINS_REL).read_bytes()).hexdigest()
    kinds: dict[str, int] = {}
    for e in entries:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    return {
        "schema": SCHEMA,
        "generator": "tests/e2e/release_demo/inventory/generate.py",
        "sources": sources,
        "counts": {"nav_static": len(nav), "panels_registered": len(panels_all), "panels_inventoried": len(panels),
                   "routes": len(routes), "deeplinks": len(deeplinks), "tabs_triggers": trigger_count,
                   "by_kind": dict(sorted(kinds.items()))},
        "entries": entries,
        "excluded": excluded,
    }


def render(inventory: dict) -> str:
    return json.dumps(inventory, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    repo = HERE.parents[3]
    inventory = build_inventory(repo)
    (HERE / "inventory.json").write_text(render(inventory), encoding="utf-8")
    print(f"wrote {HERE / 'inventory.json'}: {len(inventory['entries'])} entries {inventory['counts']['by_kind']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
