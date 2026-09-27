"""Model lineage — which Claude model is CURRENT, and what replaces a retired one.

Two operator rules (2026-09-27) live here and nowhere else:

1. **A retired model jumps to its successor automatically.** When Anthropic
   retires ``claude-opus-5`` in favour of ``claude-opus-5-5``, every place that
   still names the old id — a pin in the tenant YAML, a saved per-tier choice,
   a worker model, a hardcoded default — must launch the successor instead of
   failing every turn. The successor is the newest AVAILABLE model of the same
   family whose version is not lower (``opus 5`` → ``opus 5.5``, never
   ``opus 4.8``).
2. **Automatic routing always uses the newest version of a family.** The
   complexity tiers name a family (haiku / sonnet / opus); :func:`latest` turns
   that into the newest available id. An operator's explicit pin is NOT
   upgraded while its model still exists — only rule 1 touches it.

"Retired" is deliberately narrow, because a false positive silently swaps an
operator's chosen model. An id counts as retired only when the Claude Code CLI
itself answered ``There's an issue with the selected model (<id>)`` for it
(recorded by :func:`mark_retired` from ``agents/claude_code.py``). Such a record
expires after :data:`RUNTIME_RETIRE_TTL_S`, so a transient access problem heals
itself.

Two weaker signals are deliberately NOT evidence:

* absence from the curated registry — a valid older model (``claude-opus-4-8``)
  or a brand-new one the registry does not list yet must pass through;
* absence from the live ``/v1/models`` catalogue — that list is what the
  configured API KEY can see, while the CLI may run on a subscription that sees
  a different set (a partial catalogue retired Opus 5.5 in the first draft).

No network here; stdlib only; never raises on the hot path.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Iterable

#: ``claude-<family>-<major>[-<minor>][-<YYYYMMDD>]`` — the naming scheme of
#: every current Claude model. The 8-digit date is a snapshot, not a version.
_ID_RE = re.compile(
    r"^claude-(haiku|sonnet|opus|fable|mythos)-(\d{1,2})(?:-(\d{1,2}))?(?:-(\d{8}))?$"
)

#: Capability order across families. Used for ranking only — never pricing.
FAMILY_RANK: dict[str, int] = {"haiku": 1, "sonnet": 2, "opus": 3, "fable": 4, "mythos": 4}

#: How long a runtime "model does not exist" observation stands before the id
#: is tried again. Long enough that a retired model does not fail one turn per
#: hour; short enough that a mistaken mark (a transient access error) heals.
RUNTIME_RETIRE_TTL_S: float = 7 * 24 * 3600

_CLI_UNAVAILABLE_RE = re.compile(r"issue with the selected model \(([^)\s]+)\)", re.I)


# ── parsing ───────────────────────────────────────────────────────────


def _bare(model_id: str) -> str:
    return (model_id or "").rsplit("/", 1)[-1].strip()


def parse(model_id: str | None) -> tuple[str, tuple[int, int]] | None:
    """``(family, (major, minor))`` for a Claude id, else ``None``.

    ``claude-opus-5`` → ``("opus", (5, 0))``; ``claude-opus-5-5`` →
    ``("opus", (5, 5))``; ``claude-haiku-4-5-20251001`` → ``("haiku", (4, 5))``.
    A ``provider/`` prefix is ignored.
    """
    if not isinstance(model_id, str):
        return None
    m = _ID_RE.match(_bare(model_id))
    if not m:
        return None
    return m.group(1), (int(m.group(2)), int(m.group(3) or 0))


def family_of(model_id: str | None) -> str | None:
    p = parse(model_id)
    return p[0] if p else None


def version_label(model_id: str | None) -> str | None:
    """Human label with the version number: ``Opus 5.5``, ``Haiku 4.5``."""
    p = parse(model_id)
    if not p:
        return None
    fam, (major, minor) = p
    return f"{fam.capitalize()} {major}" + (f".{minor}" if minor else "")


def rank(model_id: str | None) -> int:
    """Capability rank by family (0 for anything unparseable)."""
    fam = family_of(model_id)
    return FAMILY_RANK.get(fam, 0) if fam else 0


# ── retirement evidence ───────────────────────────────────────────────


def _retired_path() -> Path:
    home_env = os.environ.get("CORVIN_HOME", "")
    home = Path(home_env).expanduser() if home_env else Path.home() / ".corvin"
    return home / "global" / "retired_models.json"


def _load_runtime_marks() -> dict[str, float]:
    try:
        raw = json.loads(_retired_path().read_text("utf-8"))
    except Exception:  # noqa: BLE001 — absent/corrupt file = no evidence
        return {}
    marks = raw.get("models") if isinstance(raw, dict) else None
    if not isinstance(marks, dict):
        return {}
    now = time.time()
    out: dict[str, float] = {}
    for mid, ts in marks.items():
        try:
            ts = float(ts)
        except (TypeError, ValueError):
            continue
        if 0 <= now - ts < RUNTIME_RETIRE_TTL_S:
            out[str(mid)] = ts
    return out


def mark_retired(model_id: str) -> bool:
    """Record runtime evidence that *model_id* no longer exists.

    Only a parseable Claude id is recorded — a malformed id (``anthropic/…``,
    a typo) is a configuration error, not a retirement, and must not trigger
    a model swap. Atomic write at mode 0600. Returns True when recorded."""
    bare = _bare(model_id)
    if parse(bare) is None:
        return False
    path = _retired_path()
    marks = _load_runtime_marks()
    marks[bare] = time.time()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"models": marks}, sort_keys=True, indent=2), "utf-8")
        tmp.chmod(0o600)
        tmp.replace(path)
    except OSError:
        return False
    return True


def unavailable_model_in(error_text: str | None) -> str | None:
    """The model id named by the CLI's "selected model" error, if any."""
    if not error_text:
        return None
    m = _CLI_UNAVAILABLE_RE.search(str(error_text))
    return m.group(1) if m else None


def is_retired(model_id: str | None) -> bool:
    """True only on evidence (see module docstring)."""
    bare = _bare(model_id or "")
    if parse(bare) is None:
        return False
    return bare in _load_runtime_marks()


# ── current models ────────────────────────────────────────────────────


def _registry_ids(engine_id: str) -> list[str]:
    try:
        try:
            from engine_models import load_registry  # type: ignore  # noqa: PLC0415
        except ImportError:
            from .engine_models import load_registry  # type: ignore  # noqa: PLC0415
        spec = load_registry().get(engine_id)
    except Exception:  # noqa: BLE001
        return []
    if spec is None:
        return []
    ids = [m.id for m in spec.os_models] + [m.id for m in spec.worker_models]
    return list(dict.fromkeys(i for i in ids if i))


def newest(ids: Iterable[str], family: str, *, min_version: tuple[int, int] = (0, 0)) -> str | None:
    """Newest id of *family* in *ids* (not retired, version ≥ *min_version*).

    A tie between a dated snapshot and its bare alias keeps the first one seen,
    so the registry's own spelling wins."""
    best: tuple[tuple[int, int], str] | None = None
    for mid in ids:
        p = parse(mid)
        if not p or p[0] != family or p[1] < min_version or is_retired(mid):
            continue
        if best is None or p[1] > best[0]:
            best = (p[1], mid)
    return best[1] if best else None


def latest(family: str, engine_id: str = "claude_code") -> str | None:
    """Newest available model of *family* for *engine_id*, or ``None``."""
    return newest(_registry_ids(engine_id), family)


def successor(model_id: str, engine_id: str = "claude_code") -> str | None:
    """The newest available same-family model not older than *model_id*."""
    p = parse(model_id)
    if not p:
        return None
    return newest(_registry_ids(engine_id), p[0], min_version=p[1])


def current(model_id: str | None, engine_id: str = "claude_code") -> str | None:
    """*model_id*, or its successor when it is retired.

    Unparseable ids, available ids and retired ids without a successor are
    returned unchanged — this never invents a model and never drops one."""
    if not isinstance(model_id, str) or not model_id.strip():
        return model_id
    if not is_retired(model_id):
        return model_id
    succ = successor(model_id, engine_id)
    if not succ:
        return model_id
    prefix = model_id[: len(model_id) - len(_bare(model_id))]
    return prefix + _bare(succ) if prefix else succ
