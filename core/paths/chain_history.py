"""Read-side access to a tenant chain's seam-linked HISTORY (ADR-2058).

Thin forwarder to ``forge.security_events.chain_history_files`` /
``iter_chain_records`` for code under ``core/`` that cannot assume ``forge`` is
already on ``sys.path``. There is ONE implementation — this module only makes
it importable, and degrades to "canonical file only" when ``forge`` cannot be
imported at all (a reader that loses history must not lose the present too).

For console READERS that aggregate history. Verifiers, the ADR-0232 boot
tripwire and compliance checks read the canonical chain alone.
"""
from __future__ import annotations

import collections
import json
import logging
import sys
from pathlib import Path
from typing import Any, Iterator

_FORGE_DIR = Path(__file__).resolve().parents[2] / "corvin_operator" / "forge"


def _security_events():
    try:
        from forge import security_events  # type: ignore[import-not-found]  # noqa: PLC0415
        return security_events
    except ImportError:
        pass
    try:
        if _FORGE_DIR.is_dir() and str(_FORGE_DIR) not in sys.path:
            sys.path.insert(0, str(_FORGE_DIR))
        from forge import security_events  # type: ignore[import-not-found]  # noqa: PLC0415
        return security_events
    except Exception:  # noqa: BLE001
        return None


def chain_history_files(chain_path: Path) -> list[Path]:
    se = _security_events()
    if se is None or not hasattr(se, "chain_history_files"):
        return []
    return se.chain_history_files(Path(chain_path))


def iter_chain_records(
    chain_path: Path, *, include_history: bool = True,
    needles: tuple[str, ...] | None = None,
) -> Iterator[dict[str, Any]]:
    se = _security_events()
    if se is not None and hasattr(se, "iter_chain_records"):
        yield from se.iter_chain_records(
            Path(chain_path), include_history=include_history, needles=needles,
        )
        return
    # Degraded: forge unavailable — the canonical chain alone, same tolerance.
    try:
        with Path(chain_path).open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if needles is not None and not any(n in line for n in needles):
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(rec, dict):
                    yield rec
    except OSError:
        return


#: Mirrors ``forge.security_events.LAST_N_DEFAULT_MAX_BYTES``.
_LAST_N_DEFAULT_MAX_BYTES = 1 << 20

_log = logging.getLogger(__name__)
_degraded_warned = False


def _warn_degraded_once() -> None:
    global _degraded_warned
    if _degraded_warned:
        return
    _degraded_warned = True
    _log.warning(
        "last_chain_records: forge is not importable; degraded to the "
        "canonical chain file alone (no seam-linked history, file order)")


def last_chain_records(
    chain_path: Path, *, max_records: int,
    max_bytes: int | None = _LAST_N_DEFAULT_MAX_BYTES,
    include_history: bool = True, needles: tuple[str, ...] | None = None,
    max_scan_bytes: int | None = None,
) -> list[dict[str, Any]]:
    """Newest-first last-N view of a chain and its history (see
    ``forge.security_events.last_chain_records``). Last-N views only: a total
    must not be capped. Blocking I/O; call it off the event loop.

    Degraded (forge unimportable, logged once per process): the canonical file
    alone (no history), in file order rather than ts order. The caps behave as
    in the core: ``max_bytes`` counts each raw line plus its newline, cuts a
    prefix of the newest-first order and never empties a non-empty result;
    ``max_scan_bytes`` reads only that many bytes from the end of the file.
    Without ``max_scan_bytes`` this path reads the file forward in full, which
    is acceptable only here: it should not occur in a shipped install.
    """
    se = _security_events()
    if se is not None and hasattr(se, "last_chain_records"):
        return se.last_chain_records(
            Path(chain_path), max_records=max_records, max_bytes=max_bytes,
            include_history=include_history, needles=needles,
            max_scan_bytes=max_scan_bytes,
        )
    _warn_degraded_once()
    if max_records < 0:
        raise ValueError("max_records must be >= 0")
    if max_bytes is not None and max_bytes < 0:
        raise ValueError("max_bytes must be >= 0")
    if max_scan_bytes is not None and max_scan_bytes < 0:
        raise ValueError("max_scan_bytes must be >= 0")
    if max_records == 0:
        return []
    nd = None if needles is None else tuple(n.encode("utf-8") for n in needles)
    keep: collections.deque[tuple[int, dict[str, Any]]] = collections.deque(
        maxlen=max_records)
    try:
        with Path(chain_path).open("rb") as fh:
            size = fh.seek(0, 2)
            start = 0 if max_scan_bytes is None else max(0, size - max_scan_bytes)
            skip_partial = False
            if start > 0:
                fh.seek(start - 1)
                skip_partial = fh.read(1) != b"\n"  # cut mid-line: drop that piece
            fh.seek(start)
            for raw in fh:
                if skip_partial:
                    skip_partial = False
                    continue
                raw = raw.rstrip(b"\n")
                if nd is not None and not any(n in raw for n in nd):
                    continue
                line = raw.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line.decode("utf-8", errors="replace"))
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(rec, dict):
                    keep.append((len(raw) + 1, rec))
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    used = 0
    for nbytes, rec in reversed(keep):
        if max_bytes is not None and out and used + nbytes > max_bytes:
            break
        out.append(rec)
        used += nbytes
    return out


__all__ = ["chain_history_files", "iter_chain_records", "last_chain_records"]
