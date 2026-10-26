"""Gate verdicts derived from the knowledge repository's own ``kb check``.

The four gates (ADRGate, ConceptGate, ImplementationPlanGate, IdeaGate) used to carry
their own rule tables in ``validators.py``. Measured 2026-10-26 over the live checkout,
those tables disagreed with ``kb check`` on whether an artifact fails for 848 of 1277
artifacts (66 %): a proposed ADR with ``commits: []`` — the shape ``kb new decision``
writes — failed here, while 24 artifacts with real ``kb check`` errors (duplicate id,
dangling link, invalid status) passed. The KB is the single source of truth for its own
artifacts (ADR-0516, ADR-2205), so its rules decide; this module only projects.

Mapping, per artifact file:

* any finding of severity ``error``  -> ``fail``
* only ``warning`` findings          -> ``warn``  (e.g. the G1-G4 readiness gates)
* no finding                         -> ``pass``

Where the severity comes from: ``kb check --json`` names each finding's rule, and the
rule's severity lives in the ``RULES`` table of the SAME checkout's ``scripts/kb.py``.
That table is read with ``ast`` — parsed, never imported or executed — so the console
keeps no copy of it and never runs repository code in-process. ``kb.py`` itself is not
changed for this: the path gate pins its hash, and a self-edited pinned tool would block
every session until an operator re-pinned it. A rule missing from the table, or a table
that is not a literal, refuses the run instead of guessing a severity.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

from core.quality_gates.models import GateResult, VerdictType

#: The rule source named in every verdict, so a reader sees which system judged.
RULE_SOURCE = "kb check"

_SEVERITIES = ("error", "warning")


class KbCheckOutputError(ValueError):
    """A ``kb check`` result or rule table this module cannot judge from."""


def rule_severities(kb_py: Path) -> Dict[str, str]:
    """``{rule: severity}`` from the module-level ``RULES = {...}`` literal of ``kb_py``."""
    try:
        tree = ast.parse(Path(kb_py).read_text(encoding="utf-8"), filename=str(kb_py))
    except (OSError, SyntaxError) as exc:
        raise KbCheckOutputError(f"cannot read the rule table of {kb_py}: {type(exc).__name__}") from exc
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id == "RULES" for t in targets) and node.value is not None:
            try:
                table = ast.literal_eval(node.value)
            except ValueError as exc:
                raise KbCheckOutputError(f"RULES in {kb_py} is not a literal table") from exc
            out = {str(rule): spec[0] for rule, spec in table.items()
                   if isinstance(spec, tuple) and spec and spec[0] in _SEVERITIES}
            if len(out) != len(table):
                raise KbCheckOutputError(f"RULES in {kb_py} has an entry without error/warning severity")
            return out
    raise KbCheckOutputError(f"no RULES table in {kb_py}")


def findings_by_file(check: Dict[str, Any], severities: Dict[str, str]) -> Dict[str, List[Dict[str, Any]]]:
    """Every finding of a ``kb check --json`` result, keyed by the resolved file path,
    each carrying the severity its rule has in the checkout's own table."""
    issues = check.get("issues")
    if not isinstance(issues, dict):
        raise KbCheckOutputError("kb check output has no 'issues' map")
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for rule, items in issues.items():
        severity = severities.get(rule)
        if severity is None:
            raise KbCheckOutputError(f"kb check reported {rule}, which its own RULES table does not define")
        for item in items or []:
            out[str(Path(item["file"]).resolve())].append(dict(item, rule=rule, severity=severity))
    return dict(out)


def verdict_for(gate_name: str, artifact_id: str, file: Path,
                by_file: Dict[str, List[Dict[str, Any]]], tenant_id: str) -> GateResult:
    """The verdict of one artifact file, from the findings ``kb check`` recorded for it."""
    found = by_file.get(str(Path(file).resolve()), [])
    errors = [f for f in found if f["severity"] == "error"]
    warnings = [f for f in found if f["severity"] == "warning"]
    if errors:
        verdict = VerdictType.FAIL
    elif warnings:
        verdict = VerdictType.WARN
    else:
        verdict = VerdictType.PASS
    lines = [f"{f['rule']}: {f.get('detail', '')}" + (" (baselined)" if f.get("baselined") else "")
             for f in errors + warnings]
    if verdict is VerdictType.PASS:
        reason = f"{RULE_SOURCE}: no finding"
    else:
        reason = f"{RULE_SOURCE}: {len(errors)} error(s), {len(warnings)} warning(s)"
    return GateResult(gate_name=gate_name, artifact_id=artifact_id, verdict=verdict,
                      confidence=1.0, reason=reason, tenant_id=tenant_id, findings=lines)
