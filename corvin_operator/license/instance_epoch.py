"""Instance epoch persistence — ADR-0138 M3 D3 fix.

Stores the highest seen nonce_epoch from manifest fetches in
instance_epoch.json, independent of the manifest cache. This prevents
a manifest-cache-delete replay attack where deleting
license_manifest_cache.json would reset the epoch counter to 0,
re-enabling SOBs from previous billing periods.

The epoch is NEVER decreased — only increased on successful manifest fetch.
A missing instance_epoch.json on a fresh install is the only legitimate
source of epoch=0.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path

log = logging.getLogger("corvin.license.instance_epoch")

_EPOCH_FILENAME = "instance_epoch.json"


def _epoch_path(corvin_home: Path) -> Path:
    return corvin_home / "global" / _EPOCH_FILENAME


def read_instance_epoch(corvin_home: Path) -> int:
    """Return the persisted nonce_epoch, or 0 if the file doesn't exist yet.

    Mode check: rejects world/group-readable files (tamper indicator) and
    returns 0 — the calling code will treat the manifest as authoritative
    or fail-closed.
    """
    path = _epoch_path(corvin_home)
    try:
        mode = path.stat().st_mode & 0o077
        if mode:
            # Permissive mode is a tamper indicator — return a very large epoch
            # so the SOB epoch check fails for all SOBs (fail-closed, not fail-open).
            log.warning(
                "instance_epoch: %s has permissive mode 0o%o — rejecting all SOBs (fail-closed)",
                path.name, mode,
            )
            return 2**31 - 1
        data = json.loads(path.read_text(encoding="utf-8"))
        return int(data.get("nonce_epoch", 0))
    except FileNotFoundError:
        return 0
    except Exception as exc:  # noqa: BLE001
        log.debug("instance_epoch: read failed (%s)", exc)
        return 0


def write_instance_epoch(corvin_home: Path, epoch: int) -> None:
    """Persist nonce_epoch atomically via rename.  Only increases.

    Called after every successful manifest fetch.  Mode 0o600.
    No-op when the new epoch is not strictly greater than the stored one.
    """
    if epoch <= 0:
        return

    current = read_instance_epoch(corvin_home)
    if epoch <= current:
        return  # never decrease

    path = _epoch_path(corvin_home)
    data = json.dumps({"nonce_epoch": epoch, "updated_at": int(time.time())}).encode()

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".epoch.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
            log.debug("instance_epoch: updated to %d", epoch)
        except Exception:  # noqa: BLE001
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception as exc:  # noqa: BLE001
        log.warning("instance_epoch: write failed (%s) — epoch not persisted", exc)
