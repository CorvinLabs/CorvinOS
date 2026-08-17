"""Tenant Sync (G5, ADR-0369) — the type-specific merge engine for cross-device learning.

CorvinOS runs on the operator's own machine. Cross-device learning means a tenant's
*learnable state* — CEL stage grades, learning-event JSONL, skills, memory — is shared
across the operator's instances (laptop, server) through a Git remote. Git is the
**transport + history** ONLY; the merge is done HERE, structurally, per data type — never
by `git merge` on working files (which would hand an end-user a text conflict they will
never resolve).

Merge rules (see ADR-0369, and the plan's LDD review F1/F4):
  * Grade store (``ce_stage_grades.json``): **union of the per-stage ``grades[]`` arrays**,
    then recompute ``n_grades``/``mean_score`` from the union. NOT "sum n_grades" — that
    double-counts a grade already present on both sides.
  * Learning events (``*.jsonl``): union of lines, de-duplicated, sorted. Append-only logs
    merge losslessly.
  * Free-form files (skills, memory): last-write-wins by mtime, with a collision report so
    the loser is never silently dropped without the operator seeing it.

Security (fail-closed intent, honestly bounded):
  * ``assert_no_raw_pii`` scans a payload for PII/secret shapes and raises before a push.
    It is **best-effort over free text**, NOT the structural fail-closed guarantee that
    telemetry's ``_assert_safe`` gives over a closed enum allowlist — learning state is
    free-form (memory prose, skill bodies, grade notes). The load-bearing protections are
    the mandatory GPG encryption + explicit consent + default-off flag; the scanner is a
    second line, not the guarantee. Do not describe it as fail-closed-equivalent.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class PiiLeak(Exception):
    """Raised by ``assert_no_raw_pii`` when a payload carries a PII/secret shape."""


# ── PII backstop (best-effort; GPG + consent carry the real guarantee) ─────────
# Shapes that must never leave the machine in cleartext. Deliberately conservative
# (false positives are cheap: they just refuse a sync until the operator looks).
_PII_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer", re.compile(r"\b(?:sk-|ghp_|xoxb-)[A-Za-z0-9]{16,}")),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?\d{4}){3,}")),
)


def scan_pii(text: str) -> list[str]:
    """Return the names of every PII shape found in ``text`` (empty = clean)."""
    return [name for name, pat in _PII_PATTERNS if pat.search(text or "")]


def assert_no_raw_pii(payload: str | bytes) -> None:
    """Raise ``PiiLeak`` if the payload carries a PII/secret shape. Best-effort — see
    the module docstring: this is a second line, not a fail-closed guarantee."""
    text = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else payload
    hits = scan_pii(text)
    if hits:
        raise PiiLeak(f"payload carries PII/secret shape(s): {', '.join(sorted(set(hits)))}")


# ── merge primitives ───────────────────────────────────────────────────────────
def merge_jsonl(local: list[str], remote: list[str]) -> list[str]:
    """Union of two append-only JSONL logs: de-duplicated, stable-sorted. Blank lines
    dropped. A line present on both sides appears once (lossless, no double-count)."""
    seen: dict[str, None] = {}
    for line in [*local, *remote]:
        s = (line or "").strip()
        if s:
            seen.setdefault(s, None)
    return sorted(seen.keys())


def merge_grade_store(local: dict[str, Any], remote: dict[str, Any]) -> dict[str, Any]:
    """Merge two ``ce_stage_grades.json`` dicts. For each stage, UNION the ``grades[]``
    arrays (a grade is identified by its full record; identical records collapse), then
    recompute ``n_grades``/``mean_score`` from the union. Never sums n_grades."""
    out: dict[str, Any] = {}
    for stage in set(local) | set(remote):
        l_entry = local.get(stage) or {}
        r_entry = remote.get(stage) or {}
        l_grades = l_entry.get("grades") or []
        r_grades = r_entry.get("grades") or []
        # union by canonical JSON of each grade record (order-insensitive, lossless)
        merged: dict[str, dict] = {}
        for g in [*l_grades, *r_grades]:
            merged.setdefault(json.dumps(g, sort_keys=True, default=str), g)
        grades = list(merged.values())
        scores = [g.get("score") for g in grades if isinstance(g.get("score"), (int, float))]
        out[stage] = {
            **{k: v for k, v in {**l_entry, **r_entry}.items() if k != "grades"},
            "grades": grades,
            "n_grades": len(grades),
            "mean_score": round(sum(scores) / len(scores), 6) if scores else 0.0,
        }
    return out


@dataclass
class Collision:
    path: str
    reason: str
    kept: str  # "local" | "remote"


@dataclass
class SyncReport:
    merged_files: list[str] = field(default_factory=list)
    jsonl_lines_added: int = 0
    grade_stages_merged: int = 0
    collisions: list[Collision] = field(default_factory=list)
    pii_blocked: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "merged_files": self.merged_files,
            "jsonl_lines_added": self.jsonl_lines_added,
            "grade_stages_merged": self.grade_stages_merged,
            "collisions": [c.__dict__ for c in self.collisions],
            "pii_blocked": self.pii_blocked,
            "ok": not self.pii_blocked,
        }


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""


def merge_tenant_dirs(local_dir: Path, remote_dir: Path) -> SyncReport:
    """Merge ``remote_dir`` INTO ``local_dir`` in place, type-specifically. Returns a
    report. Raises ``PiiLeak`` (via the caller's push-time assert) is NOT done here —
    this only merges local state; the PII assert runs on the OUTBOUND payload before push.

    File-type routing:
      * ``ce_stage_grades.json``  → grade-store union
      * ``*.jsonl``               → line union
      * everything else           → last-write-wins by mtime, collision recorded
    """
    local_dir = Path(local_dir)
    remote_dir = Path(remote_dir)
    report = SyncReport()

    for rpath in sorted(remote_dir.rglob("*")):
        if not rpath.is_file():
            continue
        rel = rpath.relative_to(remote_dir)
        lpath = local_dir / rel
        lpath.parent.mkdir(parents=True, exist_ok=True)

        if rpath.name == "ce_stage_grades.json":
            l = json.loads(_read(lpath) or "{}") if lpath.exists() else {}
            r = json.loads(_read(rpath) or "{}")
            merged = merge_grade_store(l, r)
            lpath.write_text(json.dumps(merged, indent=2, default=str), encoding="utf-8")
            report.grade_stages_merged += len(merged)
            report.merged_files.append(str(rel))
        elif rpath.suffix == ".jsonl":
            before = [ln for ln in _read(lpath).splitlines() if ln.strip()]
            merged = merge_jsonl(before, _read(rpath).splitlines())
            lpath.write_text("\n".join(merged) + ("\n" if merged else ""), encoding="utf-8")
            report.jsonl_lines_added += max(0, len(merged) - len(before))
            report.merged_files.append(str(rel))
        else:
            if not lpath.exists():
                lpath.write_bytes(rpath.read_bytes())
                report.merged_files.append(str(rel))
            else:
                l_mtime = lpath.stat().st_mtime
                r_mtime = rpath.stat().st_mtime
                if r_mtime > l_mtime and _read(rpath) != _read(lpath):
                    lpath.write_bytes(rpath.read_bytes())
                    report.collisions.append(Collision(str(rel), "mtime LWW", "remote"))
                    report.merged_files.append(str(rel))
                elif _read(rpath) != _read(lpath):
                    report.collisions.append(Collision(str(rel), "mtime LWW", "local"))
    return report
