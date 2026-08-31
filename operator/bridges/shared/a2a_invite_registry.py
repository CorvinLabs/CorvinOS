"""A2A Invite Registry — ADR-0063 L38 M4.

File-backed store for issued invite tokens.  Tracks lifecycle state
(pending → accepted / revoked) so single-use and revocation constraints
can be enforced on the issuing instance.

Storage: ``<corvin_home>/global/remote_trigger/invites.json``  (mode 0600)
Format:  dict keyed by ``ikey`` (16-hex-char sig prefix).

Thread-safety: ``fcntl.flock`` on every write.

CI lint: module MUST NOT ``import anthropic``.
"""
from __future__ import annotations

from _compat_fcntl import fcntl  # portable: real fcntl on POSIX, no-op flock on Windows
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_REGISTRY_ENV = "CORVIN_A2A_INVITE_REGISTRY_PATH"
_DEFAULT_SUBPATH = "global/remote_trigger/invites.json"


def _corvin_home() -> Path:
    env = os.environ.get("CORVIN_HOME") or os.environ.get("CORVIN_HOME")
    if env:
        return Path(env)
    return Path.home() / ".corvin"


def _default_registry_path() -> Path:
    env = os.environ.get(_REGISTRY_ENV)
    if env:
        return Path(env)
    return _corvin_home() / _DEFAULT_SUBPATH


@dataclass
class InviteEntry:
    ikey: str
    oid: str
    lbl: str
    iat: float
    exp: float | None
    su: bool
    accepted: bool = False
    accepted_at: float | None = None
    revoked: bool = False
    revoked_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ikey": self.ikey,
            "oid": self.oid,
            "lbl": self.lbl,
            "iat": self.iat,
            "exp": self.exp,
            "su": self.su,
            "accepted": self.accepted,
            "accepted_at": self.accepted_at,
            "revoked": self.revoked,
            "revoked_at": self.revoked_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "InviteEntry":
        return cls(
            ikey=d["ikey"],
            oid=d.get("oid", ""),
            lbl=d.get("lbl", ""),
            iat=float(d.get("iat", 0)),
            exp=float(d["exp"]) if d.get("exp") is not None else None,
            su=bool(d.get("su", False)),
            accepted=bool(d.get("accepted", False)),
            accepted_at=float(d["accepted_at"]) if d.get("accepted_at") is not None else None,
            revoked=bool(d.get("revoked", False)),
            revoked_at=float(d["revoked_at"]) if d.get("revoked_at") is not None else None,
        )

    @property
    def status(self) -> str:
        if self.revoked:
            return "revoked"
        if self.accepted:
            return "accepted"
        if self.exp is not None and time.time() >= self.exp:
            return "expired"
        return "pending"


class InviteRegistry:
    """File-backed invite registry with flock-based concurrency."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _default_registry_path()

    # ── internal I/O ──────────────────────────────────────────────────

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self._path.exists():
            return {}
        try:
            raw = self._path.read_text("utf-8")
            return json.loads(raw) if raw.strip() else {}
        except Exception:
            return {}

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            json.dump(data, fh, sort_keys=True, indent=2)
            fh.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self._path)
        if self._path.exists():
            os.chmod(self._path, 0o600)

    # ── public API ────────────────────────────────────────────────────

    def create(self, entry: InviteEntry) -> None:
        """Add a new invite entry (idempotent on ikey)."""
        data = self._load()
        data[entry.ikey] = entry.to_dict()
        self._save(data)

    def get(self, ikey: str) -> dict[str, Any] | None:
        """Return raw entry dict or None if not found."""
        return self._load().get(ikey)

    def mark_accepted(self, ikey: str) -> bool:
        """Mark invite as accepted.  Returns False if not found."""
        data = self._load()
        if ikey not in data:
            return False
        data[ikey]["accepted"] = True
        data[ikey]["accepted_at"] = time.time()
        self._save(data)
        return True

    def revoke(self, ikey: str) -> bool:
        """Mark invite as revoked.  Returns False if not found."""
        data = self._load()
        if ikey not in data:
            return False
        data[ikey]["revoked"] = True
        data[ikey]["revoked_at"] = time.time()
        self._save(data)
        return True

    def list_all(self) -> list[InviteEntry]:
        """Return all entries, newest first."""
        data = self._load()
        entries = [InviteEntry.from_dict(v) for v in data.values()]
        return sorted(entries, key=lambda e: e.iat, reverse=True)

    def cleanup(self, max_age_s: float = 86400) -> int:
        """Remove entries that expired more than ``max_age_s`` seconds ago.

        Returns number of entries removed.
        """
        now = time.time()
        data = self._load()
        before = len(data)
        data = {
            k: v
            for k, v in data.items()
            if v.get("exp") is None or now - float(v["exp"]) < max_age_s
        }
        if len(data) < before:
            self._save(data)
        return before - len(data)

    def find_by_label(self, label: str) -> InviteEntry | None:
        """Find first non-revoked entry whose lbl matches (case-insensitive)."""
        label_lower = label.lower()
        for entry in self.list_all():
            if entry.lbl.lower() == label_lower and not entry.revoked:
                return entry
        return None
