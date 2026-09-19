"""Real artifacts for the quality gates (ADR-0688) — read from the Corvin-ADR
repository, the single source of truth for decisions, concepts, ideas and
implementation plans (CLAUDE.md § ADR-0516).

The validators (``validators.py``) take structured dicts. Until 2026-09-20
nothing produced those dicts from the real files: the console's "Run All
Gates" wrote a hard-coded ``pass`` per gate and the git hooks were never
installed, so ``gate_events`` stayed empty and the Quality Gates page showed
zeros. This module projects each markdown file onto the shape its gate checks:

* ``ADRGate``               ← ``decisions/*.md``            frontmatter fields
* ``ConceptGate``           ← ``concepts/*.md``             narrative, boundaries, commits
* ``ImplementationPlanGate``← ``implementation-plans/*.md`` phases, success criteria, estimate
* ``IdeaGate``              ← ``ideas/*.md``                evidence, recurrence

Nothing is inferred beyond what the file says: a missing section is a missing
section and the gate's own finding names it. The projection is deterministic
so a run over the same tree yields the same verdicts.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - PyYAML is a console dependency
    yaml = None  # type: ignore

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$", re.M)
_PHASE_RE = re.compile(r"\bphase\b", re.I)
_SUCCESS_RE = re.compile(r"success criteria|acceptance criteria|definition of done|exit criteria|verification", re.I)
_BOUNDARY_RE = re.compile(r"when\s+not\s+to\s+use|boundar|non-goals?|out of scope|limits?\b|scope", re.I)
_RESOURCE_RE = re.compile(r"\b(effort|resource|estimate|person[- ]days?|hours?|LoC|sessions?)\b", re.I)
_WEEKS_RE = re.compile(r"(\d+)\s*(?:–|-|to)?\s*(\d+)?\s*weeks?", re.I)

#: Directory (relative to the ADR root) and gate per artifact kind.
KINDS: Dict[str, Dict[str, str]] = {
    "ADRGate": {"dir": "decisions", "node_type": "ADR"},
    "ConceptGate": {"dir": "concepts", "node_type": "Concept"},
    "ImplementationPlanGate": {"dir": "implementation-plans", "node_type": "ImplementationPlan"},
    "IdeaGate": {"dir": "ideas", "node_type": "Idea"},
}


def resolve_adr_root() -> Optional[Path]:
    """The Corvin-ADR checkout: ``CORVIN_ADR_ROOT`` → the sibling checkout beside
    the CorvinOS repo → the ``corvin_decisions`` submodule. ``None`` when none
    holds a ``decisions/`` directory (the run then reports it, never fakes it)."""
    candidates: List[Path] = []
    env = os.environ.get("CORVIN_ADR_ROOT", "").strip()
    if env:
        candidates.append(Path(env).expanduser())
    repo = Path(__file__).resolve().parents[2]
    candidates.append(repo.parent / "Corvin-ADR")
    candidates.append(repo / "corvin_decisions")
    for c in candidates:
        if (c / "decisions").is_dir():
            return c
    return None


def parse_frontmatter(text: str) -> tuple[Dict[str, Any], str]:
    """``(frontmatter, body)``; an absent or unparsable block is ``({}, text)``."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    body = text[m.end():]
    if yaml is None:
        return {}, body
    try:
        data = yaml.safe_load(m.group(1))
    except Exception:  # noqa: BLE001 — a broken block is "no frontmatter", the gate says so
        return {}, body
    return (data if isinstance(data, dict) else {}), body


def _sections(body: str) -> List[tuple[str, str]]:
    """``[(heading, text-under-it)]`` in document order."""
    out: List[tuple[str, str]] = []
    matches = list(_HEADING_RE.finditer(body))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        out.append((m.group(2), body[m.end():end]))
    return out


def _listify(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v if x is not None and str(x).strip()]
    return [str(v)] if str(v).strip() else []


def _artifact_id(fm: Dict[str, Any], path: Path) -> str:
    """The FILE stem, never the declared ``id``: in the live checkout 77
    decisions declare the placeholder ``ADR-0000`` and seven declare
    ``ADR-0469``, so a verdict keyed on the declared id could not be traced
    back to a file. The declared id travels as ``declared_id``."""
    return path.stem


def adr_artifact(path: Path) -> Dict[str, Any]:
    fm, _body = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    art: Dict[str, Any] = dict(fm)
    art["declared_id"] = str(fm.get("id") or "")
    art["id"] = _artifact_id(fm, path)
    art["path"] = path.name
    return art


def concept_artifact(path: Path) -> Dict[str, Any]:
    fm, body = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    boundaries = str(fm.get("boundaries") or "")
    if not boundaries:
        for heading, text in _sections(body):
            if _BOUNDARY_RE.search(heading):
                boundaries = text.strip()
                break
    return {
        "id": _artifact_id(fm, path),
        "declared_id": str(fm.get("id") or ""),
        "path": path.name,
        "narrative": body,
        "boundaries": boundaries,
        "evidence_commits": _listify(fm.get("commits")) or _listify(fm.get("evidence_commits")),
        "skills": _listify(fm.get("skills")),
    }


def plan_artifact(path: Path) -> Dict[str, Any]:
    fm, body = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    sections = _sections(body)
    phases = [{"title": h} for h, _t in sections if _PHASE_RE.search(h)]
    success = "\n".join(t.strip() for h, t in sections if _SUCCESS_RE.search(h))
    if not success:
        # a bold inline label ("**Acceptance Criteria:**") inside a section
        success = "\n".join(line for line in body.splitlines() if _SUCCESS_RE.search(line))
    resource = "\n".join(line for line in body.splitlines() if _RESOURCE_RE.search(line))
    weeks = 0
    for m in _WEEKS_RE.finditer(body):
        weeks = max(weeks, int(m.group(2) or m.group(1)))
    return {
        "id": _artifact_id(fm, path),
        "declared_id": str(fm.get("id") or ""),
        "path": path.name,
        "subsystem": str(fm.get("subsystem") or ""),
        "phases": phases,
        "success_criteria": success,
        "resource_estimate": resource,
        "timeline_weeks": weeks,
    }


def idea_artifact(path: Path) -> Dict[str, Any]:
    fm, body = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    return {
        "id": _artifact_id(fm, path),
        "declared_id": str(fm.get("id") or ""),
        "path": path.name,
        "description": body[:2000],
        "evidence_tasks": _listify(fm.get("evidence_tasks")),
        "evidence_commits": _listify(fm.get("commits")) or _listify(fm.get("evidence_commits")),
        "recurrence_count": int(fm.get("recurrence_count") or 0),
    }


_LOADERS = {
    "ADRGate": adr_artifact,
    "ConceptGate": concept_artifact,
    "ImplementationPlanGate": plan_artifact,
    "IdeaGate": idea_artifact,
}


def load_artifacts(root: Path, gate_name: str) -> List[Dict[str, Any]]:
    """Every ``*.md`` of the gate's directory, projected; sorted by name."""
    kind = KINDS.get(gate_name)
    loader = _LOADERS.get(gate_name)
    if kind is None or loader is None:
        return []
    directory = root / kind["dir"]
    if not directory.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for path in sorted(directory.glob("*.md")):
        if path.name.startswith(("README", "_")):
            continue
        try:
            out.append(loader(path))
        except OSError:
            continue
    return out


def load_all(root: Path) -> Dict[str, List[Dict[str, Any]]]:
    return {gate: load_artifacts(root, gate) for gate in KINDS}
