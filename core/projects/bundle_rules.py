"""The ONE protected-entry list of the project bundle (ADR-2323 Decision 8 "One protected list for both sides", REVIEW-0062 N3).

:func:`protected_entry` answers, for a project-relative path, the ``left_out`` reason under which the export leaves it out
- and the import (V1 of ``core/projects/bundle.py``) refuses every bundle entry it answers for. Both sides call this one
function, so an exported project passes its own V1 by construction. The protected components are imported from the
registry (``registry.PROTECTED_COMPONENTS``), never copied.

Also here, because both sides need them and ``bundle.py`` must not carry a protected-name literal of its own: the name rule
for components that cannot exist on Windows (:func:`unportable_component`), the two git payload paths below
``.corvin-export`` that are the only entries allowed there (:func:`export_payload_problem`), and the history rule of
Decision 12 (:func:`history_secret_path`).
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Iterable, Optional, Union

from .manifest import WINDOWS_DEVICE_NAMES
from .registry import PROTECTED_COMPONENTS

Rel = Union[PurePosixPath, str, Iterable[str]]

#: the reasons this module answers (the export's ``left_out`` vocabulary for protected entries)
REASONS = ("machine_local", "git_internal", "protected_name", "engine_copy", "graph_cache", "secret_name")

#: the platform's machine-local directory and the git directory (members of PROTECTED_COMPONENTS)
_MACHINE_LOCAL_COMPONENT = ".corvin"
_GIT_COMPONENT = ".git"
assert {_MACHINE_LOCAL_COMPONENT, _GIT_COMPONENT} <= PROTECTED_COMPONENTS
_MCP_FILE = ".mcp.json"
_ENGINE_FILE = "engine.json"                       # casefolded
_LOCAL_SUBDIR = "local"                            # .corvin/local - machine-local generated files

#: secret-named files (casefolded basename)
_SECRET_NAME_RE = re.compile(r"^(?:\.env|\.env\..*|.*\.pem|.*\.key|.*\.p12|.*\.pfx|id_rsa.*|id_ed25519.*|secrets\..*|credentials\..*)$")
_KB_ENGINE_SCRIPT_RE = re.compile(r"^kb.*\.py$")

#: the export directory of a bundle and the only two entries allowed below it (Decision 7)
EXPORT_DIR = ".corvin-export"
HISTORY_BUNDLE = f"{EXPORT_DIR}/git/history.bundle"
DOCS_HISTORY_BUNDLE = f"{EXPORT_DIR}/git/docs-history.bundle"
GIT_PAYLOADS = (HISTORY_BUNDLE, DOCS_HISTORY_BUNDLE)

#: characters NTFS refuses in a name (the colon opens an alternate data stream)
_NTFS_BAD = re.compile(r'[<>:"|?*\\\x00-\x1f\x7f]')


def _parts(rel: Rel) -> tuple[str, ...]:
    if isinstance(rel, PurePosixPath):
        return tuple(p for p in rel.parts if p not in ("", "."))
    if isinstance(rel, str):
        return tuple(p for p in rel.split("/") if p not in ("", "."))
    return tuple(rel)


def protected_entry(rel: Rel) -> Optional[str]:
    """The ``left_out`` reason for a project-relative path, or None. Case-insensitive (on NTFS ``.GIT`` is the git directory).

    A component in ``registry.PROTECTED_COMPONENTS`` at any depth: the machine-local directory -> ``machine_local``, the git
    directory or a ``.git`` file -> ``git_internal``, any other -> ``protected_name``; ``.mcp.json`` -> ``machine_local``;
    ``ENGINE.json`` (an engine copy, ADR-2316 Decision 8) and ``docs/scripts/kb*.py`` -> ``engine_copy``; ``kb/graph/**`` ->
    ``graph_cache``; the secret-name patterns -> ``secret_name``."""
    parts = _parts(rel)
    if not parts:
        return None
    folded = [p.casefold() for p in parts]
    for f in folded:
        if f in PROTECTED_COMPONENTS:
            if f == _MACHINE_LOCAL_COMPONENT:
                return "machine_local"
            if f == _GIT_COMPONENT:
                return "git_internal"
            return "protected_name"
    name = folded[-1]
    if name == _MCP_FILE:
        return "machine_local"
    if name == _ENGINE_FILE:
        return "engine_copy"
    if len(folded) >= 3 and folded[0] == "docs" and folded[1] == "scripts" and _KB_ENGINE_SCRIPT_RE.match(name):
        return "engine_copy"
    for i in range(len(folded) - 1):
        if folded[i] == "kb" and folded[i + 1] == "graph":
            return "graph_cache"
    if _SECRET_NAME_RE.match(name):
        return "secret_name"
    return None


def unportable_component(name: str) -> bool:
    """A name Windows cannot create or open normally: a device name (with any extension), an NTFS-forbidden character or a
    control character, or a trailing dot or space (REVIEW-0061 m3)."""
    if not name or _NTFS_BAD.search(name) or name[-1] in ". ":
        return True
    return name.split(".", 1)[0].casefold().rstrip(" ") in WINDOWS_DEVICE_NAMES


def export_payload_problem(rel: Rel) -> bool:
    """True when a path below a project has an ``.corvin-export`` component and is not one of the two git payload paths."""
    parts = _parts(rel)
    if not any(p.casefold() == EXPORT_DIR for p in parts):
        return False
    return "/".join(parts) not in GIT_PAYLOADS


def history_secret_path(rel: Rel) -> bool:
    """Decision 12: a path in the git history that refuses an export with history - a secret-named file, ``.mcp.json`` or a
    file below ``.corvin/local``. (Other never-list entries - a fragment's tracked ``.corvin/forge/README.md``, a
    ``tenants`` folder - travel in the history; only the tree leaves them out.)"""
    parts = _parts(rel)
    if not parts:
        return False
    folded = [p.casefold() for p in parts]
    if folded[-1] == _MCP_FILE or _SECRET_NAME_RE.match(folded[-1]):
        return True
    return any(folded[i] == _MACHINE_LOCAL_COMPONENT and folded[i + 1] == _LOCAL_SUBDIR for i in range(len(folded) - 1))
