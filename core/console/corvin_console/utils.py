"""Shared console utilities."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_json(
    path: Path,
    data: dict | str,
    *,
    mode: int = 0o600,
) -> None:
    """Write *data* to *path* atomically via mkstemp + os.replace.

    - Creates parent directories if missing.
    - Sets file mode to *mode* (default 0600) before the rename so there is
      no world-readable window.
    - On error the temp file is cleaned up and the exception re-raised.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (data if isinstance(data, str) else json.dumps(data, indent=2, ensure_ascii=False)) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        os.chmod(tmp, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json_or_none(path: Path) -> dict[str, Any] | None:
    """Read a JSON file and return its contents, or None if missing or corrupt."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def read_json_or_empty(path: Path) -> dict[str, Any]:
    """Read a JSON file and return its contents, or {} if missing or corrupt."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def sanitize_grant_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """Return a grant dict safe to send to the browser (no raw signature)."""
    out: dict[str, Any] = {
        "grant_id": doc.get("grant_id"),
        "grantee_actor": doc.get("grantee_actor"),
        "grantor_actor": doc.get("grantor_actor"),
        "capabilities": doc.get("capabilities", []),
        "conditions": doc.get("conditions", {}),
        "issued_at": doc.get("issued_at"),
    }
    if "revoked_at" in doc:
        out["revoked_at"] = doc["revoked_at"]
    return out
