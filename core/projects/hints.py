"""The path gate's hint test, used by the location rule (ADR-2321 Decision 2, REVIEW-0061 M5).

The assistant's path gate (``corvin_operator/voice/hooks/path_gate.py``) fails a shell command closed on ambiguous constructs
whenever the command names a path that ``_looks_protected`` matches (``forge``, ``compute``, ``packages``, ``.corvin`` ... as
case-insensitive substrings). A managed project whose path matched would have ordinary commands refused, so a NEW managed id that
matches is refused (``hint_word``) and an own folder that matches only warns.

There is ONE list: the gate's own ``_PROTECTED_HINTS``. This module loads the gate module by file path
(``importlib.util.spec_from_file_location``) once and calls its ``_looks_protected``; it never keeps a copy. A load failure raises
:class:`HintSourceUnavailable` (the doctor's ``hint_source`` check fails; managed creates are refused until it is fixed).
"""
from __future__ import annotations

import importlib.util
import threading
from pathlib import Path
from types import ModuleType
from typing import Optional

#: the gate module; a test points this at a sandbox copy
GATE_PATH = Path(__file__).resolve().parents[2] / "corvin_operator" / "voice" / "hooks" / "path_gate.py"


class HintSourceUnavailable(RuntimeError):
    """The path gate's hint test could not be loaded."""


_lock = threading.Lock()
_cache: dict[str, ModuleType] = {}


def _gate() -> ModuleType:
    key = str(GATE_PATH)
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
        except HintSourceUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - a missing or broken file is one answer: unavailable
            raise HintSourceUnavailable(f"the path gate's hint test could not be loaded ({type(exc).__name__})") from None
        _cache[key] = mod
        return mod


def reset() -> None:
    with _lock:
        _cache.clear()


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
