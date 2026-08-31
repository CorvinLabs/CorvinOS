"""Audit-chain walker with filters.

Reads `<tenant_home>/global/forge/audit.jsonl`, returns events
matching the supplied filters. Used by the three baseline report
generators (AI Act, GDPR, Audit-Integrity). Pure-read; never
mutates the chain.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

# Make forge paths importable from this plugin's venv.
_THIS = Path(__file__).resolve().parent
_REPO = _THIS.parents[2]
_FORGE = _REPO / "operator" / "forge"
if str(_FORGE) not in sys.path:
    sys.path.insert(0, str(_FORGE))

from forge import paths as _forge_paths  # noqa: E402
from forge import security_events as _security_events  # noqa: E402


@dataclass(frozen=True)
class ChainStats:
    """Summary statistics over a chain range."""
    total_events: int
    first_event_ts: int | None
    last_event_ts: int | None
    first_event_hash: str | None
    last_event_hash: str | None
    by_event_type: dict[str, int]
    by_severity: dict[str, int]
    chain_intact: bool
    chain_problems: list[str]


def audit_chain_path(tenant_id: str) -> Path:
    """Resolve the on-disk audit-chain path for the given tenant."""
    return _forge_paths.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"


def iter_events(
    *,
    tenant_id: str,
    start_ts: int | None = None,
    end_ts: int | None = None,
    event_type_prefix: str | None = None,
    event_types: tuple[str, ...] | None = None,
    severity: str | None = None,
    chain_path: Path | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield each event matching the filters.

    Filters compose AND-style. ``event_type_prefix`` and
    ``event_types`` are mutually exclusive — pass one or neither.
    ``start_ts`` / ``end_ts`` are unix-epoch inclusive bounds.
    """
    path = chain_path or audit_chain_path(tenant_id)
    if not path.exists():
        return

    if event_type_prefix and event_types:
        raise ValueError(
            "use event_type_prefix OR event_types, not both"
        )

    type_set = set(event_types) if event_types else None
    prefix = event_type_prefix

    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                ts = int(ev.get("ts", 0))
            except (ValueError, TypeError):
                continue
            if start_ts is not None and ts < start_ts:
                continue
            if end_ts is not None and ts > end_ts:
                continue
            et = str(ev.get("event_type", ""))
            if prefix and not et.startswith(prefix):
                continue
            if type_set is not None and et not in type_set:
                continue
            if severity and str(ev.get("severity", "")) != severity:
                continue
            yield ev


def collect_events(**kwargs) -> list[dict[str, Any]]:
    """Materialised version of ``iter_events`` for small ranges."""
    return list(iter_events(**kwargs))


def compute_stats(
    *,
    tenant_id: str,
    start_ts: int | None = None,
    end_ts: int | None = None,
    chain_path: Path | None = None,
) -> ChainStats:
    """Summary statistics over the chain range.

    Includes hash-chain integrity check via
    ``forge.security_events.verify_chain``.
    """
    path = chain_path or audit_chain_path(tenant_id)
    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    first_ts: int | None = None
    last_ts: int | None = None
    first_hash: str | None = None
    last_hash: str | None = None
    total = 0

    for ev in iter_events(
        tenant_id=tenant_id,
        start_ts=start_ts,
        end_ts=end_ts,
        chain_path=chain_path,
    ):
        total += 1
        et = str(ev.get("event_type", ""))
        sev = str(ev.get("severity", "INFO"))
        by_type[et] = by_type.get(et, 0) + 1
        by_severity[sev] = by_severity.get(sev, 0) + 1
        try:
            ts = int(ev.get("ts", 0))
        except (ValueError, TypeError):
            continue
        h = str(ev.get("hash", ""))
        if first_ts is None:
            first_ts = ts
            first_hash = h
        last_ts = ts
        last_hash = h

    intact = True
    problems: list[str] = []
    if path.exists():
        try:
            intact, problems_raw = _security_events.verify_chain(path)
            problems = [str(p) for p in problems_raw]
        except Exception as exc:
            intact = False
            problems = [f"verify-error: {exc}"]

    return ChainStats(
        total_events=total,
        first_event_ts=first_ts,
        last_event_ts=last_ts,
        first_event_hash=first_hash,
        last_event_hash=last_hash,
        by_event_type=by_type,
        by_severity=by_severity,
        chain_intact=intact,
        chain_problems=problems,
    )


__all__ = [
    "ChainStats",
    "audit_chain_path",
    "iter_events",
    "collect_events",
    "compute_stats",
]
