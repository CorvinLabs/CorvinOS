"""Sessions-Liste — read-only enumeration of bridge sessions.

Each session lives at
``<corvin_home>/tenants/<tid>/sessions/<bridge>:<chat>/`` and may
contain ``forge/``, ``skill-forge/``, plus voice-state under
``<corvin_home>/tenants/<tid>/voice/sessions/<bridge>/<chat>/``.

Phase B: returns one entry per session-dir with cheap on-disk
metadata. No conversation snapshot yet — that's a Phase C drilldown.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from .. import auth as session_auth
from ..deps import require_session

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths


router = APIRouter()


def _dir_size_and_mtime(path: Path) -> tuple[int, float]:
    """Recursively sum file sizes and find newest mtime under *path*.

    Bounded — we walk only direct children + one level (skill-forge,
    forge subtrees). For deep trees this would need a sampled walk;
    for typical session dirs the depth is 3–4 and file count < 100.
    """
    total = 0
    newest = 0.0
    try:
        for root, _dirs, files in os.walk(path):
            for f in files:
                fp = Path(root) / f
                try:
                    st = fp.stat()
                    total += st.st_size
                    if st.st_mtime > newest:
                        newest = st.st_mtime
                except OSError:
                    continue
    except OSError:
        pass
    return total, newest


def _count_subtree(path: Path) -> int:
    if not path.exists():
        return 0
    n = 0
    try:
        for entry in path.iterdir():
            if entry.is_dir():
                n += 1
    except OSError:
        pass
    return n


def _voice_state_size(tid: str, bridge: str, chat: str) -> int:
    """Voice state lives in a parallel tree — sum its byte size."""
    voice_dir = _forge_paths.tenant_voice_dir(tid) / "sessions" / bridge / chat
    if not voice_dir.exists():
        return 0
    total = 0
    try:
        for root, _dirs, files in os.walk(voice_dir):
            for f in files:
                try:
                    total += (Path(root) / f).stat().st_size
                except OSError:
                    continue
    except OSError:
        pass
    return total


@router.get("/sessions")
def list_sessions(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return all known bridge sessions for the owner's tenant."""
    tid = rec.tenant_id
    sessions_dir = _forge_paths.tenant_sessions_dir(tid)
    items: list[dict[str, Any]] = []
    if sessions_dir.exists():
        for entry in sorted(sessions_dir.iterdir()):
            if not entry.is_dir():
                continue
            session_key = entry.name
            # Naming convention: "<bridge>:<chat>". On Windows the dir name is
            # sanitised to "<bridge>_<chat>" (':' is illegal), so accept EITHER
            # separator. Channel prefixes (web/discord/whatsapp/telegram/slack)
            # contain no ':' or '_', so splitting on the first of either recovers
            # the bridge name on every platform.
            _m = re.match(r"^([^:_]+)[:_](.+)$", session_key)
            if _m:
                bridge, chat = _m.group(1), _m.group(2)
            else:
                bridge, chat = "unknown", session_key
            size_bytes, mtime = _dir_size_and_mtime(entry)
            items.append({
                "session_key":     session_key,
                "bridge":          bridge,
                "chat":            chat,
                "size_bytes":      size_bytes,
                "last_activity":   mtime if mtime > 0 else None,
                "skills":          _count_subtree(entry / "skill-forge" / "skills"),
                "tools":           _count_subtree(entry / "forge" / "tools"),
                "voice_state_b":   _voice_state_size(tid, bridge, chat),
            })
    # Sort by last activity desc; sessions without activity at the end.
    items.sort(key=lambda r: r["last_activity"] or 0.0, reverse=True)
    return {
        "tenant_id":  tid,
        "ts":         time.time(),
        "count":      len(items),
        "sessions":   items,
    }
