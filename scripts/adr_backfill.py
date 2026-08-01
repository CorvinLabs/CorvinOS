#!/usr/bin/env python3
"""ADR-0264 frontmatter backfill for the pre-convention ADR corpus.

ADR-0264 itself explicitly rejected a blind, one-shot "add frontmatter to all
260+ existing ADRs" migration (see its "Alternatives considered"): guessed or
stale metadata would be a NEW drift source, worse than having none. This
script is the safer alternative the user asked for afterwards: a
deterministic, re-runnable EXTRACTOR that only ever writes a field when it
can point to the concrete source it came from. Never invents, never infers
semantic relationships an LLM "feels" are probably true.

What gets filled, and from where (four confidence tiers):

  Tier 1 -- deterministic, always safe:
    id       <- the filename itself ("0233-title.md" -> "ADR-0233")
    status   <- the FIRST WORD after a literal "**Status:**" line in the
                body, lowercased. If no such line exists or it doesn't look
                like a real word, status is "unknown" -- an explicit,
                visible "not derivable" marker, never a guessed default.

  Tier 2 -- soft signal, informational only (never drives graph traversal):
    commits  <- CorvinOS repo commit SHAs whose message contains this ADR's
                id as a literal substring (best-effort; a mention is not
                proof of implementation, hence Tier 2 not Tier 1).

  Tier 3 -- ONLY on an explicit phrase, never a bare mention:
    supersedes  <- "supersede(s|d) (by )?ADR-NNNN" in the body
    depends_on  <- "depends on|builds on|requires ADR-NNNN" in the body
    These edges change how the graph reports an ADR's current authority
    (superseded_by is DERIVED from supersedes at read time -- see
    scripts/adr_graph.py) or reading order, so a wrong entry here is
    actively misleading. Bare co-occurrence is not enough evidence.

  Tier 4 -- generous, because low-stakes:
    related  <- every other "ADR-NNNN" mention in the body not already
                captured by Tier 3, deduped, self-reference excluded.
                `related` is explicitly non-blocking / associative
                (ADR-0264), so over-inclusion here costs nothing.

  Never auto-filled, always empty for a backfilled ADR:
    paths    <- CLAUDE.md's existing "-> ADR:" pointers are NOT
                machine-uniform enough to parse reliably in bulk (mixed
                table/prose/inline-code formats) -- guessing wrong globs
                here would be worse than no globs. Left [] deliberately;
                ADR-0264's own "lazy backfill" already covers this: paths
                get added the moment a NEW adr's depends_on references an
                old one.
    docs     <- same reasoning as paths, one layer over: an old ADR's
                documentation surface (which docs/claude-ref/*.md page, if
                any, describes it) is not mechanically derivable from the
                ADR's own body text without real risk of pointing at the
                wrong file. Left [] for the same lazy-backfill reason.

Every backfilled node also carries `backfilled: true` and `backfill_date:`
so it is visibly distinguishable from hand-authored frontmatter (a new ADR
written after ADR-0264) -- a reader or the traversal tool can tell "this
metadata is best-effort-extracted" from "this metadata was deliberately
authored", which is the actual anti-drift property: guessed data never
impersonates verified data.

Safety:
  - Dry-run by default; nothing is written without --write.
  - Skips any decisions/*.md with a dirty git working tree (uncommitted
    changes from a parallel session) -- never build on top of someone
    else's in-progress edit.
  - Skips any file that already carries frontmatter (idempotent: re-running
    this script is always safe, it only touches files it hasn't touched).
  - After writing, re-parses every touched file through
    scripts.adr_graph.load_graph() and reports any that fail to parse or
    reference a dangling id -- a validation gate, not a hope.

Usage:
    python3 scripts/adr_backfill.py --limit 20                # dry-run, first 20
    python3 scripts/adr_backfill.py --limit 20 --write         # actually write
    python3 scripts/adr_backfill.py --write                    # full corpus
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_ADR_REPO = _REPO.parent / "Corvin-ADR"
_DECISIONS_DIR = _ADR_REPO / "decisions"

sys.path.insert(0, str(_REPO))
from scripts.adr_graph import load_graph  # noqa: E402

_ID_RE = re.compile(r"^(\d{4})-")
_STATUS_RE = re.compile(r"\*\*Status:\*\*\s*([A-Za-z][A-Za-z-]*)")
_ADR_MENTION_RE = re.compile(r"\bADR-(\d{4})\b")
_SUPERSEDES_RE = re.compile(
    r"supersed(?:e|es|ed)\s+(?:by\s+)?ADR-(\d{4})", re.IGNORECASE)
_SUPERSEDED_BY_RE = re.compile(
    r"supersed(?:e|es|ed)\s+by\s+ADR-(\d{4})", re.IGNORECASE)
_DEPENDS_RE = re.compile(
    r"(?:depends on|builds on|requires)\s+ADR-(\d{4})", re.IGNORECASE)

_STATUS_NORMALIZE = {
    "accepted": "accepted", "proposed": "proposed", "draft": "proposed",
    "superseded": "superseded", "frozen": "frozen", "deprecated": "deprecated",
    "rejected": "rejected", "implemented": "accepted",
}


@dataclass
class BackfillResult:
    file: Path
    id_: str
    status: str
    supersedes: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    commits: list[str] = field(default_factory=list)
    skipped_reason: str = ""


def _has_frontmatter(text: str) -> bool:
    return text.startswith("---\n") and "\n---" in text[4:]


def _dirty_paths(repo: Path) -> set[str]:
    proc = subprocess.run(
        ["git", "status", "--short", "decisions/"],
        cwd=repo, capture_output=True, text=True, timeout=10,
    )
    dirty = set()
    for line in proc.stdout.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2:
            dirty.add(parts[1])
    return dirty


def _group_by_id(files: list[Path]) -> dict[str, list[Path]]:
    groups: dict[str, list[Path]] = {}
    for f in files:
        m = _ID_RE.match(f.name)
        if m:
            groups.setdefault(m.group(1), []).append(f)
    return groups


def find_id_collisions(files: list[Path]) -> tuple[set[str], set[Path]]:
    """Detect 4-digit numbers claimed by more than one filename.

    Assigning the SAME `id: ADR-NNNN` to more than one file would corrupt
    scripts/adr_graph.py's uniqueness invariant (load_graph keys nodes by
    id -- later files silently shadow earlier ones). This is a pre-existing
    corpus issue (e.g. eight separate "0007-*.md" files, legacy planning
    docs from before the numbering convention was strict), not something
    this tool resolves -- picking a "correct" one among several is a human
    judgment call.
    """
    groups = _group_by_id(files)
    colliding_ids = {num for num, group in groups.items() if len(group) > 1}
    collision_files = {f for num in colliding_ids for f in groups[num]}
    return colliding_ids, collision_files


def _extract_status(text: str) -> str:
    m = _STATUS_RE.search(text)
    if not m:
        return "unknown"
    word = m.group(1).strip().lower()
    return _STATUS_NORMALIZE.get(word, "unknown")


def _extract_commits(adr_id: str, corvin_repo: Path) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "log", f"--grep={adr_id}", "--fixed-strings",
             "--format=%H", "--max-count=20"],
            cwd=corvin_repo, capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    return [h for h in proc.stdout.splitlines() if h.strip()]


def analyze(path: Path, corvin_repo: Path) -> BackfillResult | None:
    m = _ID_RE.match(path.name)
    if not m:
        return None
    adr_id = f"ADR-{m.group(1)}"
    text = path.read_text(encoding="utf-8")
    if _has_frontmatter(text):
        return BackfillResult(path, adr_id, "", skipped_reason="already has frontmatter")

    status = _extract_status(text)

    superseded_by_explicit = set(_SUPERSEDED_BY_RE.findall(text))
    supersedes = sorted({
        f"ADR-{n}" for n in _SUPERSEDES_RE.findall(text)
        if n not in superseded_by_explicit  # "superseded BY X" is not "supersedes X"
    } - {adr_id})

    depends_on = sorted({f"ADR-{n}" for n in _DEPENDS_RE.findall(text)} - {adr_id})

    all_mentions = {f"ADR-{n}" for n in _ADR_MENTION_RE.findall(text)}
    related = sorted(all_mentions - set(supersedes) - set(depends_on) - {adr_id})

    commits = _extract_commits(adr_id, corvin_repo)

    return BackfillResult(
        file=path, id_=adr_id, status=status, supersedes=supersedes,
        depends_on=depends_on, related=related, commits=commits,
    )


def render_frontmatter(r: BackfillResult, backfill_date: str) -> str:
    def _list(items: list[str]) -> str:
        return "[" + ", ".join(items) + "]"

    lines = [
        "---",
        f"id: {r.id_}",
        f"status: {r.status}",
        f"supersedes: {_list(r.supersedes)}",
        f"depends_on: {_list(r.depends_on)}",
        f"related: {_list(r.related)}",
        f"commits: {_list(r.commits)}",
        "paths: []",
        "docs: []",
        "backfilled: true",
        f"backfill_date: {backfill_date}",
        "---",
        "",
    ]
    return "\n".join(lines)


def apply_backfill(r: BackfillResult, backfill_date: str) -> None:
    text = r.file.read_text(encoding="utf-8")
    r.file.write_text(render_frontmatter(r, backfill_date) + text, encoding="utf-8")


def validate(decisions_dir: Path, touched_ids: set[str]) -> list[str]:
    """Re-parse the whole corpus and report problems among touched nodes."""
    problems = []
    nodes = load_graph(decisions_dir)
    for adr_id in touched_ids:
        if adr_id not in nodes:
            problems.append(f"{adr_id}: failed to re-parse after write")
            continue
        node = nodes[adr_id]
        for dep in node.depends_on + node.supersedes:
            if dep not in nodes and dep not in touched_ids:
                problems.append(f"{adr_id}: references {dep}, which has no frontmatter (dangling for now)")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--limit", type=int, default=None,
                         help="only process the first N eligible files (by ADR number)")
    parser.add_argument("--write", action="store_true", help="actually write files (default: dry-run)")
    parser.add_argument("--decisions-dir", default=str(_DECISIONS_DIR))
    parser.add_argument("--adr-repo", default=str(_ADR_REPO))
    parser.add_argument("--corvin-repo", default=str(_REPO))
    parser.add_argument("--backfill-date", default="2026-08-01")
    args = parser.parse_args(argv)

    decisions_dir = Path(args.decisions_dir)
    adr_repo = Path(args.adr_repo)
    corvin_repo = Path(args.corvin_repo)

    dirty = _dirty_paths(adr_repo)
    all_files = sorted(decisions_dir.glob("*.md"))

    eligible: list[Path] = []
    skipped: list[tuple[Path, str]] = []
    for f in all_files:
        rel = f"decisions/{f.name}"
        if rel in dirty:
            skipped.append((f, "dirty working tree (parallel edit in progress)"))
            continue
        if not _ID_RE.match(f.name):
            skipped.append((f, "filename doesn't look like an ADR (NNNN-title.md)"))
            continue
        eligible.append(f)

    colliding_ids, collision_files = find_id_collisions(eligible)
    if colliding_ids:
        for f in sorted(collision_files):
            num = _ID_RE.match(f.name).group(1)
            skipped.append((f, f"id collision — {len(_group_by_id(eligible)[num])} "
                                f"files share number {num}, needs human resolution"))
        eligible = [f for f in eligible if f not in collision_files]

    if args.limit:
        eligible = eligible[:args.limit]

    results = [analyze(f, corvin_repo) for f in eligible]
    results = [r for r in results if r is not None]
    to_write = [r for r in results if not r.skipped_reason]
    already = [r for r in results if r.skipped_reason]

    n_collision = sum(1 for _, reason in skipped if "collision" in reason)
    n_dirty = sum(1 for _, reason in skipped if "dirty" in reason)
    print(f"Corpus: {len(all_files)} files. Skipped: {len(skipped)} "
          f"({n_collision} id-collisions, {n_dirty} dirty). Eligible this run: {len(eligible)}.")
    if colliding_ids:
        print(f"  Collisions found (needs human resolution, NOT written): "
              f"numbers {sorted(colliding_ids)}")
    print(f"  -> {len(already)} already have frontmatter (untouched).")
    print(f"  -> {len(to_write)} will be backfilled.")
    n_status_known = sum(1 for r in to_write if r.status != "unknown")
    n_supersedes = sum(1 for r in to_write if r.supersedes)
    n_depends = sum(1 for r in to_write if r.depends_on)
    n_related = sum(1 for r in to_write if r.related)
    n_commits = sum(1 for r in to_write if r.commits)
    print(f"     status known: {n_status_known}/{len(to_write)}  "
          f"supersedes: {n_supersedes}  depends_on: {n_depends}  "
          f"related>0: {n_related}  commits>0: {n_commits}")

    if not args.write:
        print("\n(dry-run -- pass --write to actually modify files)")
        for r in to_write[:10]:
            print(f"\n--- {r.file.name} ---")
            print(render_frontmatter(r, args.backfill_date), end="")
        if len(to_write) > 10:
            print(f"\n... and {len(to_write) - 10} more (showing first 10)")
        return 0

    for r in to_write:
        apply_backfill(r, args.backfill_date)
    print(f"\nWrote frontmatter to {len(to_write)} file(s).")

    problems = validate(decisions_dir, {r.id_ for r in to_write})
    if problems:
        print(f"\n{len(problems)} validation note(s):")
        for p in problems:
            print(f"  - {p}")
    else:
        print("Validation: all touched files re-parse cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
