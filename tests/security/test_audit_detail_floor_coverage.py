"""The ADR-0640 default-deny detail floor must not silently empty a shipped emitter.

R4 finding (2026-09-07): ``_assert_shipped_allowlists_clean()`` validates the
allowlists that EXIST. Nothing detected an emitter that has **none** — so
``erasure.requested`` reached the GDPR Art. 30 chain as
``{"_dropped_fields": ["requester", "subject_id"]}`` and
``audit.segment_sealed`` reached it with a details body holding nothing but the
list of what was thrown away. The floor was doing exactly what it was told; no
test asked whether what it kept was still an audit record.

This module closes that. It AST-scans every ``write_event`` / ``audit_event`` /
``_emit`` call site in ``core/`` and ``operator/`` that has BOTH a literal event
type and a literal ``details`` dict, runs those literal keys through the REAL
:func:`forge.security_events.filter_audit_details`, and fails when a site loses
its whole body (``test_no_emitter_lands_empty``) or loses a key that carries the
event's meaning (``test_registered_vocabularies_survive``).

It is a lower bound by construction — it only sees literal dicts — but it is a
structural check rather than a list a reviewer happened to walk, and it fails on
the NEXT emitter that ships without a vocabulary.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "operator" / "forge"))

from forge.security_events import filter_audit_details  # noqa: E402

_EMIT_FUNCS = {"write_event", "audit_event", "_emit", "_emit_audit", "_audit_emit"}
_SCAN_ROOTS = ("core", "operator")
_SKIP_PARTS = {
    ".venv", "venv", "node_modules", "site-packages", "dist-packages", ".git",
    ".corvin", "tests", "test", "__pycache__", "worktrees", ".claude",
}


def _literal(node: ast.AST):
    try:
        return ast.literal_eval(node)
    except Exception:  # noqa: BLE001
        return None


def _emit_sites() -> list[tuple[str, int, str, list[str]]]:
    """``(relpath, lineno, event_type, literal detail keys)`` for every scannable site."""
    out: list[tuple[str, int, str, list[str]]] = []
    for root in _SCAN_ROOTS:
        for py in sorted((REPO / root).rglob("*.py")):
            rel = py.relative_to(REPO)
            if any(p in _SKIP_PARTS for p in rel.parts) or rel.name.startswith("test_"):
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                if name not in _EMIT_FUNCS:
                    continue
                et = None
                for arg in node.args:
                    v = _literal(arg)
                    if isinstance(v, str) and ("." in v or "_" in v):
                        et = v
                        break
                details = None
                for kw in node.keywords:
                    if kw.arg == "details":
                        details = kw.value
                if details is None:
                    # positional `_emit(event, severity, {...})` shape
                    for arg in node.args:
                        if isinstance(arg, ast.Dict):
                            details = arg
                if et is None or not isinstance(details, ast.Dict):
                    continue
                keys = [k for k in (_literal(k) for k in details.keys) if isinstance(k, str)]
                if keys:
                    out.append((str(rel), node.lineno, et, keys))
    return out


SITES = _emit_sites()


def test_scan_found_emit_sites():
    """Guard the guard: a scanner that matches nothing passes vacuously."""
    assert len(SITES) > 100, f"AST scan found only {len(SITES)} emit sites — scanner broken"


def test_no_emitter_lands_empty():
    """No shipped emitter may write a details body that is entirely dropped.

    A record whose details are ``{"_dropped_fields": [...]}`` says an event
    happened and nothing else — for ``erasure.completed`` that means the
    immutable trail cannot say whether the erasure succeeded.
    """
    empty: list[str] = []
    for rel, line, et, keys in SITES:
        cleaned, _ = filter_audit_details({k: "x" for k in keys}, event_type=et)
        kept = [k for k in cleaned if not k.startswith("_")]
        if not kept:
            empty.append(f"{rel}:{line} {et} loses ALL of {sorted(keys)}")
    assert not empty, (
        "these emitters write a details body the ADR-0640 floor empties completely — "
        "register a content-free vocabulary via register_event_allowlist() or add the "
        "keys to _AUDIT_KNOWN_KEYS:\n  " + "\n  ".join(sorted(empty))
    )


#: Event types whose MEANING lives in a specific field. Losing the field leaves a
#: record that is present but useless — the R4 asymmetry (``failed_count``
#: survived, ``applied_count`` did not) is exactly this shape.
_LOAD_BEARING: dict[str, set[str]] = {
    "erasure.requested": {"subject_id", "requester"},
    "erasure.applied":   {"subject_id", "code"},
    "erasure.skipped":   {"subject_id", "code"},
    "erasure.failed":    {"subject_id", "code"},
    "erasure.completed": {"subject_id", "overall_status", "applied_count"},
    "audit.segment_sealed":      {"sealed_segment"},
    "audit.segment_retired":     {"sealed_segment"},
    "audit.unseal_requested":    {"requester", "sealed_segment"},
    "supply_chain.cve_detected": {"cve_id", "package_name"},
}


@pytest.mark.parametrize("event_type,required", sorted(_LOAD_BEARING.items()))
def test_registered_vocabularies_survive(event_type, required):
    cleaned, dropped = filter_audit_details(
        {k: "v" for k in required}, event_type=event_type)
    missing = sorted(required - set(cleaned))
    assert not missing, f"{event_type} loses load-bearing field(s) {missing} (dropped={dropped})"


#: Subsystems that own a curated metadata-only vocabulary in their own module AND
#: are mirrored into ``security_events._EVENT_ALLOWLIST``. Mirroring — rather than
#: an import-time ``register_event_allowlist()`` call from the owning module —
#: is deliberate: registration at import time makes the floor's behaviour depend
#: on whether that module happened to be imported before the write, and a
#: fail-closed compliance mechanism must not be import-order dependent. The price
#: is drift, which this test removes.
def _owned_vocabularies() -> dict[str, dict[str, set[str]]]:
    sys.path.insert(0, str(REPO / "core" / "compute"))
    from corvin_compute.audit import _ALLOWED_FIELDS
    from corvin_compute.fabric.audit_events import FABRIC_AUDIT_EVENTS
    from corvin_compute.fabric.datasources.audit_events import DATASOURCE_AUDIT_EVENTS
    return {
        "corvin_compute.audit::_ALLOWED_FIELDS": _ALLOWED_FIELDS,
        "fabric.audit_events::FABRIC_AUDIT_EVENTS": FABRIC_AUDIT_EVENTS,
        "fabric.datasources.audit_events::DATASOURCE_AUDIT_EVENTS": DATASOURCE_AUDIT_EVENTS,
    }


def test_subsystem_vocabularies_are_mirrored_in_the_floor():
    """A field a subsystem allows IN must survive on the way OUT.

    These dicts were enforced only at the emitter (`_check_allow_list` rejects an
    extra key) and were unknown to the floor, so `compute.run_terminal` reached
    the chain without `best_loss`/`convergence_reason`/`total_iterations`, and
    `datasource.watermark_advanced` without either watermark hash — 22 red tests
    in `core/compute/tests/test_fabric_audit.py` that nothing connected to the
    floor.
    """
    missing: list[str] = []
    for source, vocab in _owned_vocabularies().items():
        for event_type, fields in vocab.items():
            cleaned, _ = filter_audit_details({f: "x" for f in fields}, event_type=event_type)
            lost = sorted(set(fields) - set(cleaned))
            if lost:
                missing.append(f"{source} :: {event_type} loses {lost}")
    assert not missing, (
        "subsystem vocabulary drifted from security_events._EVENT_ALLOWLIST:\n  "
        + "\n  ".join(missing))
