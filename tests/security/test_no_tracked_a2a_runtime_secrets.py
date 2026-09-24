"""A2A pairing records must never be tracked by git.

The files under ``corvin_operator/cowork/{remote_endpoints,remote_origins,
remote_pending_friendships,pending_invites}/`` are per-instance RUNTIME state
and carry live channel keys (``hmac_key``/``recv_key``) or raw shared token
keys. They are gitignored, yet commit cce86a13 force-added a friendship's
endpoint+origin (and three pending friendships) into this PUBLIC repo — the
keys stay readable in history, and because git synced the same records onto
every clone, two machines ended up believing they held the same pairing
(found 2026-09-24).

Only the empty ``.a2a_config.lock`` files may stay tracked: deleting a lock
file under a running console would let a second process lock a fresh inode.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RUNTIME_DIRS = (
    "corvin_operator/cowork/remote_endpoints",
    "corvin_operator/cowork/remote_origins",
    "corvin_operator/cowork/remote_pending_friendships",
    "corvin_operator/cowork/pending_invites",
)
ALLOWED_NAMES = {".a2a_config.lock", ".gitkeep"}
_KEY_RE = re.compile(r'"(hmac_key|recv_key|key)"\s*:\s*"[0-9a-fA-F]{64}"')


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=REPO, capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")


def test_runtime_dirs_exist_positive_control():
    # Positive control: the scan below would pass vacuously if these paths
    # moved. The lock files prove git still sees the directories.
    tracked = _git("ls-files", *RUNTIME_DIRS).split()
    assert any(p.endswith(".a2a_config.lock") for p in tracked), tracked


def test_no_pairing_record_is_tracked():
    tracked = _git("ls-files", *RUNTIME_DIRS).split()
    offenders = [p for p in tracked if Path(p).name not in ALLOWED_NAMES]
    assert not offenders, (
        "A2A pairing records carry live channel keys and must never be "
        f"committed: {offenders}"
    )


def test_no_channel_key_literal_in_tracked_json():
    tracked_json = [p for p in _git("ls-files", "*.json").split() if p]
    assert tracked_json, "positive control: the repo tracks JSON files"
    offenders = []
    for rel in tracked_json:
        try:
            text = (REPO / rel).read_text("utf-8", errors="ignore")
        except OSError:
            continue
        if _KEY_RE.search(text):
            offenders.append(rel)
    assert not offenders, f"64-hex channel key literal in tracked JSON: {offenders}"
