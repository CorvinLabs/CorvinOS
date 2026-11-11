"""The path gate's hint test, used by the location rule (ADR-2321 Decision 2, REVIEW-0061 M5).

The assistant's path gate (``corvin_operator/voice/hooks/path_gate.py``) fails a shell command closed on ambiguous constructs
whenever the command names a path that ``_looks_protected`` matches (``forge``, ``compute``, ``packages``, ``.corvin`` ... as
case-insensitive substrings). A managed project whose path matched would have ordinary commands refused, so a NEW managed id that
matches is refused (``hint_word``) and an own folder that matches only warns.

There is ONE list: the gate's own ``_PROTECTED_HINTS``. This module loads the gate module by file path
(``importlib.util.spec_from_file_location``) once and calls its ``_looks_protected``; it never keeps a copy.

Where the gate file is (REVIEW-0064 #1): in a git checkout (editable install) at ``<repo>/corvin_operator/voice/hooks/
path_gate.py``; in a wheel install ``core/projects/`` lands in site-packages while hatch_build.py vendors the hook to
``corvin_core/_vendor/corvin_operator/voice/hooks/`` - the vendor root is asked from ``corvin_core._operator_bootstrap.
vendor_operator_root()``, the one place that knows it. :func:`gate_path` tries the checkout path, then the vendored one.

A load failure raises :class:`HintSourceUnavailable`. Managed creates/moves/imports are then refused with their OWN reason class
``hint_source`` (never ``hint_word``, which would blame the id), the doctor's ``hint_source`` check fails, ``GET /projects``
carries ``hint_source`` (the reason) and the console logs one ERROR. Refusing stays right: the guard exists because a managed
folder whose path the gate matches makes ordinary assistant commands fail closed, and without the list nobody can tell; an own
folder (which only warns) and every other operation keep working.
"""
from __future__ import annotations

import importlib.util
import logging
import threading
from pathlib import Path
from types import ModuleType
from typing import Optional

#: an explicit gate module (a test points this at a sandbox copy); None: :func:`gate_path` resolves it
GATE_PATH: Optional[Path] = None

_GATE_REL = ("voice", "hooks", "path_gate.py")


def _vendored() -> Optional[Path]:
    """The wheel's vendored copy, when ``corvin_core`` ships a vendor root (asked only when the checkout path is missing:
    importing ``corvin_core`` runs its operator bootstrap)."""
    try:
        from corvin_core._operator_bootstrap import vendor_operator_root  # noqa: PLC0415
    except ImportError:
        return None
    root = vendor_operator_root()
    return root / Path(*_GATE_REL) if root is not None else None


def gate_path() -> Path:
    """The gate module this process loads: :data:`GATE_PATH` when set, else the checkout path when it is a file, else the
    vendored copy when it is one, else the checkout path (so the failure names it)."""
    if GATE_PATH is not None:
        return GATE_PATH
    repo = Path(__file__).resolve().parents[2] / "corvin_operator" / Path(*_GATE_REL)
    if repo.is_file():
        return repo
    vend = _vendored()
    return vend if vend is not None and vend.is_file() else repo


class HintSourceUnavailable(RuntimeError):
    """The path gate's hint test could not be loaded."""


_lock = threading.Lock()
_cache: dict[str, ModuleType] = {}
_logged: set[str] = set()


def _log_once(key: str, why: str) -> None:
    """One ERROR per gate path and process: the operator's journal names the cause of the ``hint_source`` refusals."""
    if key in _logged:
        return
    _logged.add(key)
    logging.getLogger("corvin.projects").error(
        "projects: %s (gate file %s) - new folders in the Corvin workspace are refused (hint_source) until it loads", why, key)


def _gate() -> ModuleType:
    key = str(gate_path())
    with _lock:
        mod = _cache.get(key)
        if mod is not None:
            return mod
        try:
            spec = importlib.util.spec_from_file_location("_corvin_projects_path_gate_hints", key)
            if spec is None or spec.loader is None:
                raise HintSourceUnavailable("the path gate module could not be found")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if not callable(getattr(mod, "_looks_protected", None)) or not isinstance(getattr(mod, "_PROTECTED_HINTS", None), tuple):
                raise HintSourceUnavailable("the path gate module has no hint test")
        except HintSourceUnavailable as exc:
            _log_once(key, str(exc))
            raise
        except Exception as exc:  # noqa: BLE001 - a missing or broken file is one answer: unavailable
            why = f"the path gate's hint test could not be loaded ({type(exc).__name__})"
            _log_once(key, why)
            raise HintSourceUnavailable(why) from None
        _cache[key] = mod
        return mod


def reset() -> None:
    with _lock:
        _cache.clear()
        _logged.clear()


def available() -> Optional[str]:
    """None when the hint test loads, else the reason (for the doctor)."""
    try:
        _gate()
    except HintSourceUnavailable as exc:
        return str(exc)
    return None


def looks_protected(s: str) -> bool:
    return bool(_gate()._looks_protected(s))


def hint_words(s: str) -> list[str]:
    """The hints of the gate's own tuple that *s* contains (for the warning's ``<word>``)."""
    low = s.lower()
    return [h for h in _gate()._PROTECTED_HINTS if h.lower() in low]
