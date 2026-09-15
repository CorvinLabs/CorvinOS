"""Fail-closed import surface for the shared `claude -p` prompt neutraliser.

ADR-0648 put the ONE neutraliser in ``agents/claude_code.py``
(:func:`~agents.claude_code.guard_prompt_head`): a fixed non-slash sentinel
line at byte 0 plus a zero-width U+2060 WORD JOINER before every ``@`` that
could start a client-side file reference. Both expansions happen *inside the
CLI, before the model runs*, so no tool policy, permission mode,
``--disallowedTools`` or sandbox restricts them.

Round 3 of the adversarial review guarded six spawn sites and ledgered 28
more (``core/console/tests/test_claude_spawn_site_ledger.py``). Round 4 closes
the rest. Rather than copy the same defensive ``try: import … except: None``
block into thirty modules — where every copy is one more place to forget the
``is None`` branch and spawn unguarded — every site imports the guard from
HERE.

The fail-closed contract lives in this module, not in its callers:

* the import of the real helper is attempted once, at module import;
* if it fails for ANY reason, :func:`guard_prompt_head` RAISES
  :class:`PromptGuardUnavailable` when called. There is no code path that
  returns the caller's text unchanged, so "the helper was missing" can never
  silently become "we spawned the CLI on raw chat text".

Callers therefore need no ``is None`` check: calling the guard either returns
a neutralised payload or raises before the subprocess is built. Sites that
must degrade rather than crash (compliance gates with their own fail-closed
verdict, best-effort narrators) catch the exception AND return their existing
refusal/skip value — never the unguarded spawn.
"""
from __future__ import annotations

import os as _os
import sys as _sys

__all__ = [
    "PromptGuardUnavailable",
    "guard_prompt_head",
    "neutralise_at_references",
    "prompt_guard_available",
]


class PromptGuardUnavailable(RuntimeError):
    """The shared neutraliser could not be imported — refuse to spawn."""


def _load():  # -> tuple[callable | None, callable | None, BaseException | None]
    _here = _os.path.dirname(_os.path.abspath(__file__))
    if _here not in _sys.path:
        _sys.path.insert(0, _here)
    try:
        from agents.claude_code import (  # type: ignore  # noqa: PLC0415
            guard_prompt_head as _guard,
            neutralise_at_references as _neutralise,
        )
    except Exception as exc:  # noqa: BLE001 - any failure means "unavailable"
        return None, None, exc
    return _guard, _neutralise, None


_GUARD, _NEUTRALISE, _IMPORT_ERROR = _load()


def prompt_guard_available() -> bool:
    """True when the real neutraliser was importable."""
    return _GUARD is not None


def guard_prompt_head(text: str | None) -> str:
    """Neutralise *text* for a ``claude -p`` spawn, or raise.

    Delegates to :func:`agents.claude_code.guard_prompt_head`. Raises
    :class:`PromptGuardUnavailable` instead of returning anything when that
    helper is not importable — an unguarded payload is never produced.
    """
    if _GUARD is None:
        raise PromptGuardUnavailable(
            "agents.claude_code.guard_prompt_head is not importable "
            f"({type(_IMPORT_ERROR).__name__}: {_IMPORT_ERROR}) — refusing to "
            "build an unguarded `claude -p` payload"
        )
    return _GUARD(text)


def neutralise_at_references(text: str) -> str:
    """``@<path>`` half of the guard only, for payloads that already carry a
    safe byte 0 (e.g. a fixed operator template). Raises like the above."""
    if _NEUTRALISE is None:
        raise PromptGuardUnavailable(
            "agents.claude_code.neutralise_at_references is not importable "
            f"({type(_IMPORT_ERROR).__name__}: {_IMPORT_ERROR}) — refusing to "
            "build an unguarded `claude -p` payload"
        )
    return _NEUTRALISE(text)
