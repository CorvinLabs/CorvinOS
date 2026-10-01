#!/usr/bin/env python3
"""ADR <-> memory link lint (ADR-2101 §5).

Memory notes cite ADRs ("ADR-0407 = the solution", "ADR-0405 ✅ done"). Nothing
checked those citations, so a note could point at an id that does not exist, at
an id two files share, or claim a status the ADR's own frontmatter contradicts.
Every later session that loaded the note inherited the error. This lint reports
those citations.

Checks (compiler-style output ``file:line: level: CODE message``):
  DANGLING             error    cited id has no file in decisions/
  STATUS_CONFLICT      error    line claims done/open, ADR frontmatter says otherwise
  AMBIGUOUS_ID         warning  id is carried by more than one ADR file
  DESCRIPTION_MISMATCH warning  "ADR-NNNN (description)" shares no word with the title
  ID_FILENAME_MISMATCH warning  cited number exists only as a file name, not as a frontmatter id

The status check only runs on lines that cite exactly ONE ADR, and only reads a
done/open marker within 40 characters of the citation, so a claim is never
attributed to the wrong id or to a nearby subject ("ADR-X violation → resolved").

Usage:
    python3 scripts/adr_memory_link_lint.py
    python3 scripts/adr_memory_link_lint.py --memory-dir DIR --adr-dir DIR [--strict] [--json]
Exit: 0 clean (warnings allowed), 1 errors (or warnings with --strict), 2 bad input.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from adr_graph import _parse_frontmatter, _title_from_body  # noqa: E402

_REPO = Path(__file__).resolve().parent.parent
DEFAULT_ADR_DIR = _REPO.parent / "Corvin-ADR" / "decisions"
DEFAULT_MEMORY_DIR = Path.home() / ".claude" / "projects" / "-home-shumway-projects-CorvinOS" / "memory"

ADR_REF_RE = re.compile(r"\bADR-(\d{4})(?!\d)")
_ID_KEY_RE = re.compile(r"^(?:ADR-)?(\d{4})(?!\d)")
_DESC_RE = re.compile(r"\bADR-(\d{4})\s*\(([^)]{3,80})\)")

_DONE_STATUSES = {"ACCEPTED", "IMPLEMENTED"}
_OPEN_STATUSES = {"PROPOSED", "DRAFT", "UNKNOWN"}
_CLAIM_DONE_RE = re.compile(
    r"✅|\b(DONE|COMPLETE|COMPLETED|IMPLEMENTED|ACCEPTED|SHIPPED|LIVE)\b|\bfertig\b|\berledigt\b",
    re.IGNORECASE)
_CLAIM_OPEN_RE = re.compile(
    r"❌|\b(PROPOSED|NOT (?:BUILT|WIRED|DONE|IMPLEMENTED)|TODO|TBD|offen)\b|(?<![-\w])OPEN\b",
    re.IGNORECASE)
_CLAIM_WINDOW = 40
# A marker after one of these words is about the violation/gap/bug, not the ADR
# ("ADR-0516 violation → RESOLVED ✅" says the violation is resolved).
_SUBJECT_SHIFT_RE = re.compile(
    r"\b(violation|verstoß|gap|lücke|blocker|bug|issue|finding|fehler|problem|incident)\b",
    re.IGNORECASE)
_STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "of", "to", "a", "an", "on", "in", "by",
    "der", "die", "das", "und", "mit", "für", "von", "adr", "phase", "fix", "fixes",
    "solution", "lösung", "status", "done", "proposed", "accepted",
}


@dataclass
class AdrFile:
    key: str
    file: str
    status: str
    title: str
    frontmatter_key: str = ""


@dataclass
class Finding:
    file: str
    line: int
    level: str
    code: str
    message: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    memory_files: int = 0
    citations: int = 0
    status_checked: int = 0

    @property
    def errors(self) -> int:
        return sum(1 for f in self.findings if f.level == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for f in self.findings if f.level == "warning")


def load_adr_index(adr_dir: Path) -> dict[str, list[AdrFile]]:
    """Every ADR FILE per 4-digit id. Unlike adr_graph.load_graph (keyed by id,
    last file wins) this keeps all of them, because a shared id is itself a finding."""
    index: dict[str, list[AdrFile]] = {}
    for f in sorted(adr_dir.glob("*.md")):
        text = f.read_text(encoding="utf-8", errors="replace")
        fm = _parse_frontmatter(text) or {}
        fm_m = _ID_KEY_RE.match(str(fm.get("id", "")))
        fn_m = _ID_KEY_RE.match(f.name)
        if not (fm_m or fn_m):
            continue
        fm_key = fm_m.group(1) if fm_m else ""
        status = str(fm.get("status") or "UNKNOWN").strip().split()[0].upper()
        title = _title_from_body(text)
        # A file named ADR-0829-0660-... whose frontmatter says ADR-0660 is reachable
        # under BOTH numbers; citing the filename number is reported, not "dangling".
        for key in dict.fromkeys(k for k in (fm_key, fn_m.group(1) if fn_m else "") if k):
            index.setdefault(key, []).append(
                AdrFile(key=key, file=f.name, status=status, title=title, frontmatter_key=fm_key))
    return index


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zäöüß0-9]+", text.lower())
            if len(w) > 2 and w not in _STOPWORDS}


def _related(desc_words: set[str], title_words: set[str]) -> bool:
    """Same word, or a shared prefix of >=5 chars (drift/drifting, persist/persistence)."""
    for d in desc_words:
        for t in title_words:
            if d == t or (min(len(d), len(t)) >= 5 and d[:5] == t[:5]):
                return True
    return False


def _strip_inline_code(line: str) -> str:
    return re.sub(r"`[^`]*`", " ", line)


def lint(memory_dir: Path, adr_dir: Path) -> Report:
    index = load_adr_index(adr_dir)
    report = Report()
    for mf in sorted(memory_dir.rglob("*.md")):
        rel = mf.relative_to(memory_dir)
        if rel.parts[0] == "archive" or mf.name == "MEMORY.md":
            continue
        report.memory_files += 1
        in_frontmatter = False
        reported_ambiguous: set[str] = set()
        for lineno, raw in enumerate(mf.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if lineno == 1 and raw.strip() == "---":
                in_frontmatter = True
                continue
            if in_frontmatter:
                if raw.strip() == "---":
                    in_frontmatter = False
                continue
            ids = list(dict.fromkeys(ADR_REF_RE.findall(raw)))
            if not ids:
                continue
            report.citations += len(ids)
            loc = str(rel)
            for key in ids:
                files = index.get(key)
                if not files:
                    report.findings.append(Finding(loc, lineno, "error", "DANGLING",
                        f"ADR-{key} has no file in {adr_dir.name}/"))
                    continue
                titles = {a.title for a in files}
                if len(files) > 1 and len(titles) > 1 and key not in reported_ambiguous:
                    reported_ambiguous.add(key)
                    report.findings.append(Finding(loc, lineno, "warning", "AMBIGUOUS_ID",
                        f"ADR-{key} is carried by {len(files)} files: "
                        + ", ".join(a.file for a in files)))
                if all(a.frontmatter_key and a.frontmatter_key != key for a in files):
                    report.findings.append(Finding(loc, lineno, "warning", "ID_FILENAME_MISMATCH",
                        f"ADR-{key} resolves only through a file name; its frontmatter id is "
                        f"ADR-{files[0].frontmatter_key} ({files[0].file})"))
            for key, desc in _DESC_RE.findall(raw):
                files = index.get(key) or []
                if not files:
                    continue
                if re.search(r"\d", desc):
                    continue  # dates, commits, scores: metadata, not a description
                dw = _words(desc)
                if dw and not any(_related(dw, _words(a.title + " " + a.file)) for a in files):
                    report.findings.append(Finding(loc, lineno, "warning", "DESCRIPTION_MISMATCH",
                        f"ADR-{key} described as '{desc.strip()}' but is titled "
                        f"'{files[0].title or files[0].file}'"))
            if len(ids) == 1 and ids[0] in index:
                files = index[ids[0]]
                statuses = {a.status for a in files}
                if len(statuses) != 1:
                    continue
                status = statuses.pop()
                text = _strip_inline_code(raw)
                m = ADR_REF_RE.search(text)
                if m is None:
                    continue
                window = text[max(0, m.start() - _CLAIM_WINDOW):m.end() + _CLAIM_WINDOW]
                after = text[m.end():m.end() + _CLAIM_WINDOW]
                if _SUBJECT_SHIFT_RE.search(after):
                    continue
                claims_done = bool(_CLAIM_DONE_RE.search(window))
                claims_open = bool(_CLAIM_OPEN_RE.search(window))
                if claims_done == claims_open:
                    continue
                report.status_checked += 1
                if claims_done and status in _OPEN_STATUSES:
                    report.findings.append(Finding(loc, lineno, "error", "STATUS_CONFLICT",
                        f"line claims ADR-{ids[0]} done, its frontmatter says {status}"))
                elif claims_open and status in _DONE_STATUSES:
                    report.findings.append(Finding(loc, lineno, "error", "STATUS_CONFLICT",
                        f"line claims ADR-{ids[0]} open, its frontmatter says {status}"))
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check ADR citations in memory notes (ADR-2101).")
    ap.add_argument("--memory-dir", type=Path, default=DEFAULT_MEMORY_DIR)
    ap.add_argument("--adr-dir", type=Path, default=DEFAULT_ADR_DIR)
    ap.add_argument("--strict", action="store_true", help="warnings also fail")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    args = ap.parse_args(argv)
    for name, d in (("memory", args.memory_dir), ("ADR", args.adr_dir)):
        if not d.is_dir():
            print(f"error: {name} directory not found: {d}", file=sys.stderr)
            return 2
    report = lint(args.memory_dir, args.adr_dir)
    if args.json:
        print(json.dumps({
            "memory_files": report.memory_files, "citations": report.citations,
            "status_checked": report.status_checked, "errors": report.errors,
            "warnings": report.warnings, "findings": [asdict(f) for f in report.findings],
        }, ensure_ascii=False, indent=2))
    else:
        for f in report.findings:
            print(f"{f.file}:{f.line}: {f.level}: {f.code} {f.message}")
        print(f"{report.memory_files} memory files, {report.citations} ADR citations, "
              f"{report.status_checked} status claims checked: "
              f"{report.errors} errors, {report.warnings} warnings")
    failed = report.errors > 0 or (args.strict and report.warnings > 0)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
