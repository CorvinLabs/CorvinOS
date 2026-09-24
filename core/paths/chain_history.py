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

import json
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


__all__ = ["chain_history_files", "iter_chain_records"]
