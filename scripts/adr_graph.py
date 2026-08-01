#!/usr/bin/env python3
"""ADR Decision Graph traversal (ADR-0264).

The ADR corpus (../Corvin-ADR/decisions/) is a knowledge base built for a
coding agent to traverse, not a document set meant to be read cover to
cover. This tool implements ADR-0264's traversal protocol: given a
repo-relative path (or an ADR id directly), find the minimal relevant
subgraph -- the seed ADR(s) plus everything they transitively depend_on --
in topological (dependency-first) reading order, with each node's current
status (accepted / superseded / frozen) resolved via `superseded_by`.

Three layers, not two: `paths:` is an ADR's CODE surface, `docs:` is its
DOCUMENTATION surface (README sections, docs/claude-ref/*.md, CLAUDE.md
mechanisms -- the artifacts `docs-as-definition-of-done` keeps in sync with
that code). Kept as separate fields deliberately: a code file and a doc
file answer different questions ("what implements this decision" vs. "what
explains it to a reader") and a caller querying one should not silently
also match the other's namespace. adrs_for_path() / adrs_for_doc() are the
two independent entry points into the same underlying graph; both feed the
identical `depends_on`/`supersedes` traversal once a seed is found, so
Code, Docs, and ADRs are three views into one graph, not three graphs.

ADRs without ADR-0264 frontmatter (the pre-0264 corpus) are silently
excluded from the graph -- by design (ADR-0264 "no retrofit"). Querying a
path with no frontmatter match returns an empty, non-error result: absence
from the graph is the expected default, not a failure.

Usage:
    python3 scripts/adr_graph.py <repo-relative-path>       # code path -> subgraph
    python3 scripts/adr_graph.py --doc <repo-relative-path> # doc path -> subgraph
    python3 scripts/adr_graph.py --adr 0264                 # one ADR -> subgraph
    python3 scripts/adr_graph.py --adr 0264 --format json   # machine-readable

Library use (for an agent or another tool to call directly instead of
shelling out):
    from scripts.adr_graph import load_graph, adrs_for_path, adrs_for_doc, subgraph
    nodes = load_graph()
    seeds = adrs_for_path("operator/bundle/skills/ldd/adr_gate/SKILL.md", nodes)
    doc_seeds = adrs_for_doc("docs/claude-ref/adr-gate.md", nodes)
    ordered = subgraph([n.id for n in seeds], nodes)
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parent.parent
_DEFAULT_DECISIONS_DIR = _REPO.parent / "Corvin-ADR" / "decisions"


@dataclass
class AdrNode:
    id: str
    file: Path
    status: str = "accepted"
    supersedes: list[str] = field(default_factory=list)
    superseded_by: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    commits: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    docs: list[str] = field(default_factory=list)
    title: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "file": str(self.file),
            "title": self.title,
            "status": self.status,
            "supersedes": self.supersedes,
            "superseded_by": self.superseded_by,
            "depends_on": self.depends_on,
            "related": self.related,
            "commits": self.commits,
            "paths": self.paths,
            "docs": self.docs,
        }


def _parse_frontmatter(text: str) -> dict | None:
    """Extract the leading '---\\n...\\n---' YAML block, if any."""
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        return None
    block = text[4:end]
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


_TITLE_PREFIX_RE = re.compile(r"^ADR-\d{4}\s*[—:-]\s*", re.IGNORECASE)


def _title_from_body(text: str) -> str:
    """First H1 heading, with a leading 'ADR-NNNN — ' repeated automatically
    by callers stripped so title stays the descriptive part only."""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            heading = line[2:].strip()
            return _TITLE_PREFIX_RE.sub("", heading)
    return ""


def load_graph(decisions_dir: Path = _DEFAULT_DECISIONS_DIR) -> dict[str, AdrNode]:
    """Load every ADR carrying ADR-0264 frontmatter into a graph, keyed by id.

    `superseded_by` is DERIVED here, not trusted from the file -- it is the
    reverse of every other node's `supersedes` list. This is deliberate: an
    ADR's own author cannot know, at write time, which future ADR will
    supersede it, and a hand-maintained back-reference is exactly the kind
    of fact that silently rots (the failure mode ADR-0264 exists to fix).
    Whatever a node's own file happens to say in `superseded_by` is
    overwritten by this computation.
    """
    nodes: dict[str, AdrNode] = {}
    if not decisions_dir.is_dir():
        return nodes
    for f in sorted(decisions_dir.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        fm = _parse_frontmatter(text)
        if fm is None or "id" not in fm:
            continue
        node = AdrNode(
            id=str(fm["id"]),
            file=f,
            status=str(fm.get("status", "accepted")),
            supersedes=[str(x) for x in (fm.get("supersedes") or [])],
            depends_on=[str(x) for x in (fm.get("depends_on") or [])],
            related=[str(x) for x in (fm.get("related") or [])],
            commits=[str(x) for x in (fm.get("commits") or [])],
            paths=[str(x) for x in (fm.get("paths") or [])],
            docs=[str(x) for x in (fm.get("docs") or [])],
            title=_title_from_body(text),
        )
        nodes[node.id] = node
    for node in nodes.values():
        for older_id in node.supersedes:
            older = nodes.get(older_id)
            if older is not None and node.id not in older.superseded_by:
                older.superseded_by.append(node.id)
    return nodes


def _glob_match_any(rel_path: str, patterns: list[str]) -> bool:
    """fnmatch's `*` already matches `/`, so a trailing `/**` glob (e.g.
    "core/plugins/plugin_builder/**") matches every file under that
    directory without special-casing -- verified: fnmatch does not apply
    shell/pathlib's directory-boundary semantics to `*`."""
    return any(fnmatch.fnmatch(rel_path, pattern) for pattern in patterns)


def adrs_for_path(rel_path: str, nodes: dict[str, AdrNode]) -> list[AdrNode]:
    """ADRs whose `paths:` (CODE surface) globs match rel_path.

    Deliberately checks `paths` only, never `docs` -- a code file and a doc
    file are different questions ("what implements this decision" vs. "what
    explains it"). Use adrs_for_doc() for the documentation surface.
    """
    rel_path = rel_path.replace("\\", "/")
    return [n for n in nodes.values() if _glob_match_any(rel_path, n.paths)]


def adrs_for_doc(rel_path: str, nodes: dict[str, AdrNode]) -> list[AdrNode]:
    """ADRs whose `docs:` (DOCUMENTATION surface) globs match rel_path.

    This is the reverse lookup `docs-as-definition-of-done` needs: given a
    doc file you are about to edit (or just edited), which ADRs describe
    the same decision and might need an amendment too. Checks `docs` only,
    never `paths` -- see adrs_for_path()'s docstring for why the two stay
    separate.
    """
    rel_path = rel_path.replace("\\", "/")
    return [n for n in nodes.values() if _glob_match_any(rel_path, n.docs)]


def subgraph(seed_ids: list[str], nodes: dict[str, AdrNode]) -> list[AdrNode]:
    """Transitive closure over depends_on from the seeds, topologically
    sorted (dependencies first) -- the reading order ADR-0264 prescribes.
    An unresolvable/cyclic dependency is skipped rather than raised: a
    context tool must degrade, never crash, when the graph itself is
    imperfect."""
    visited: dict[str, AdrNode] = {}
    order: list[str] = []

    def visit(node_id: str, stack: tuple[str, ...]) -> None:
        if node_id in visited or node_id in stack:
            return
        node = nodes.get(node_id)
        if node is None:
            return
        for dep in node.depends_on:
            visit(dep, stack + (node_id,))
        if node_id not in visited:
            visited[node_id] = node
            order.append(node_id)

    for seed in seed_ids:
        visit(seed, ())
    return [visited[i] for i in order]


def format_report(seed_ids: set[str], sub: list[AdrNode], matched_via: str = "") -> str:
    header = f"{len(sub)} ADR(s) in reading order (dependencies first)"
    if matched_via:
        header += f" — seed matched via {matched_via}"
    lines = [header + ":"]
    for node in sub:
        marker = "→" if node.id in seed_ids else " "
        flag = ""
        if node.superseded_by:
            flag = f"  [SUPERSEDED BY {', '.join(node.superseded_by)}]"
        elif node.status not in ("accepted", "proposed"):
            flag = f"  [{node.status.upper()}]"
        lines.append(f"  {marker} {node.id} — {node.title}{flag}")
        lines.append(f"      {node.file}")
        if node.paths:
            lines.append(f"      code: {', '.join(node.paths)}")
        if node.docs:
            lines.append(f"      docs: {', '.join(node.docs)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("target", nargs="?", help="repo-relative CODE path to query (matches `paths:`)")
    parser.add_argument("--doc", help="repo-relative DOC path to query (matches `docs:`)")
    parser.add_argument("--adr", help="query the subgraph for one ADR id, e.g. 0264 or ADR-0264")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--decisions-dir", default=str(_DEFAULT_DECISIONS_DIR))
    args = parser.parse_args(argv)

    decisions_dir = Path(args.decisions_dir)
    nodes = load_graph(decisions_dir)
    matched_via = ""

    if args.adr:
        adr_id = args.adr.upper()
        if not adr_id.startswith("ADR-"):
            adr_id = f"ADR-{adr_id}"
        if adr_id not in nodes:
            msg = f"{adr_id} not found under {decisions_dir} (or has no ADR-0264 frontmatter yet)"
            if args.format == "json":
                print(json.dumps({"error": msg, "nodes": []}))
            else:
                print(msg, file=sys.stderr)
            return 1
        seeds = [nodes[adr_id]]
    elif args.doc:
        matched_via = "docs"
        seeds = adrs_for_doc(args.doc, nodes)
        if not seeds:
            if args.format == "json":
                print(json.dumps({"seeds": [], "nodes": [],
                                   "note": "no ADR-0264 docs: frontmatter matches this path"}))
            else:
                print(f"No ADR `docs:` frontmatter matches path {args.doc!r}.")
                print("(Expected for most doc files -- most docs aren't tied to a "
                      "single ADR. This is the query docs-as-definition-of-done "
                      "should run before editing a doc: if it DOES match, the "
                      "matched ADR may need an amendment alongside the doc edit.)")
            return 0
    elif args.target:
        matched_via = "code paths"
        seeds = adrs_for_path(args.target, nodes)
        if not seeds:
            if args.format == "json":
                print(json.dumps({"seeds": [], "nodes": [],
                                   "note": "no ADR-0264 frontmatter matches this path"}))
            else:
                print(f"No ADR frontmatter matches path {args.target!r}.")
                print("(Expected for most files. Falling back to CLAUDE.md's "
                      "prose table for the pre-ADR-0264 corpus is the right "
                      "next step, not an error.)")
            return 0
    else:
        parser.print_help()
        return 2

    seed_ids = {n.id for n in seeds}
    sub = subgraph(sorted(seed_ids), nodes)

    if args.format == "json":
        print(json.dumps({
            "seeds": sorted(seed_ids),
            "matched_via": matched_via or "adr_id",
            "nodes": [n.to_dict() for n in sub],
        }, indent=2))
    else:
        print(format_report(seed_ids, sub, matched_via))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
