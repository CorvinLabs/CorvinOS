"""Incremental watermark / checkpoint management (ADR-0026 Section D).

Key invariants:
  - write_checkpoint is AUDIT-FIRST: audit_fn called before file write.
  - Watermark is NEVER advanced on job failure.
  - Audit events use sha256[:8] hash — NOT raw watermark values.
  - Checkpoint file itself stores raw watermark (adapters need it to build queries).
  - Atomic write via temp-file + os.replace; mode 0o600.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional


class CheckpointNotFound(FileNotFoundError):
    """Raised when no checkpoint file exists for the datasource."""


# ---------------------------------------------------------------------------
# Hash helper
# ---------------------------------------------------------------------------

def hash_watermark(value: Any) -> str:
    """Return sha256(str(value))[:8] — safe for audit events."""
    raw = str(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:8]


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

_CHECKPOINT_DEFAULTS: dict[str, Any] = {
    "watermark": None,
    "last_successful_run_id": None,
    "last_advanced_at": None,
}


def read_checkpoint(checkpoint_path: Path) -> dict:
    """Read checkpoint JSON. Returns defaults if file does not exist.

    Returns dict with keys: watermark, last_successful_run_id, last_advanced_at.
    """
    if not checkpoint_path.exists():
        return dict(_CHECKPOINT_DEFAULTS)

    try:
        data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(_CHECKPOINT_DEFAULTS)

    return {
        "watermark": data.get("watermark"),
        "last_successful_run_id": data.get("last_successful_run_id"),
        "last_advanced_at": data.get("last_advanced_at"),
    }


# ---------------------------------------------------------------------------
# Write (AUDIT-FIRST)
# ---------------------------------------------------------------------------

def write_checkpoint(
    checkpoint_path: Path,
    watermark: Any,
    run_id: str,
    *,
    audit_fn: Callable[[str, dict], None],
    previous_watermark: Optional[Any] = None,
    rows_read: int = 0,
) -> None:
    """Persist a new watermark checkpoint.

    AUDIT-FIRST: audit_fn is called BEFORE the file is written.
    Atomic write (temp file + os.replace). mode 0o600.

    The AUDIT event uses sha256[:8] hashes — NOT raw watermark values.
    The checkpoint file stores the raw watermark so adapters can use it.

    Args:
        checkpoint_path: Where to write the checkpoint JSON.
        watermark: New watermark value (raw; stored in file for adapter use).
        run_id: Identifier for the run that produced this watermark.
        audit_fn: Callable(event_name, details_dict) — MUST be called first.
        previous_watermark: Previous watermark value (for audit hashing).
        rows_read: Number of rows read in this run (included in audit).
    """
    new_hash = hash_watermark(watermark)
    prev_hash = hash_watermark(previous_watermark) if previous_watermark is not None else "00000000"

    # AUDIT-FIRST — before any file I/O
    audit_fn(
        "datasource.watermark_advanced",
        {
            "name": str(checkpoint_path.stem),
            "previous_watermark_hash": prev_hash,
            "new_watermark_hash": new_hash,
            "rows_read": rows_read,
        },
    )

    import datetime

    payload = {
        "watermark": watermark,  # raw — adapters need this for WHERE col > watermark
        "last_successful_run_id": run_id,
        "last_advanced_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    # Atomic write
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=checkpoint_path.parent,
        prefix=".ckpt_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, checkpoint_path)
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


__all__ = [
    "CheckpointNotFound",
    "hash_watermark",
    "read_checkpoint",
    "write_checkpoint",
]
