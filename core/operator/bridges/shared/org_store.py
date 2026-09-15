"""Layer 42 CorvinOrg — Org store: path helpers, member management, endorsements.

Storage layout per org:
    <tenant_global>/orgs/<org_handle>/
      actor.json         (0644) — public org actor document
      keypair.json       (0600) — active Ed25519 signing key (root key, M1)
      config.json        (0600) — responsible_party, approval policy
      members.json       (0600) — member roles
      endorsements/              — agent affiliation endorsements
        <endorsement_id>.json

All mutating methods assume the caller has already emitted the corresponding
L16 audit event (audit-first invariant, ADR-0055).

CI AST lint: MUST NOT import anthropic.
"""
from __future__ import annotations

import json
import os
import re
import stat
import threading
import time
from pathlib import Path

try:
    from .paths import tenant_global_dir  # type: ignore[import-not-found]
    from .audit import audit_event  # type: ignore[import-not-found]
    from . import social_envelope  # type: ignore[import-not-found]
except ImportError:
    import sys

    _here = Path(__file__).resolve().parent
    if str(_here) not in sys.path:
        sys.path.insert(0, str(_here))
    from paths import tenant_global_dir  # type: ignore[import-not-found]
    from audit import audit_event  # type: ignore[import-not-found]
    import social_envelope  # type: ignore[import-not-found]


VALID_ROLES = frozenset({"owner", "admin", "editor", "agent"})
_ORG_MEMBERS_LOCK = threading.Lock()
_HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


class OrgError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ── Path helpers ──────────────────────────────────────────────────────────────


def orgs_root(tenant_id: str | None = None) -> Path:
    return tenant_global_dir(tenant_id) / "orgs"


def org_dir(org_handle: str, tenant_id: str | None = None) -> Path:
    return orgs_root(tenant_id) / org_handle


def list_org_handles(tenant_id: str | None = None) -> list[str]:
    """Return all org handles that have an actor.json in the tenant's orgs dir."""
    root = orgs_root(tenant_id)
    if not root.is_dir():
        return []
    return [
        d.name
        for d in sorted(root.iterdir())
        if d.is_dir() and (d / "actor.json").exists()
    ]


def _validate_handle(handle: str) -> None:
    if not _HANDLE_RE.match(handle):
        raise OrgError(
            f"invalid org handle {handle!r} — must match [a-z0-9][a-z0-9_-]{{0,62}}"
        )


def _write_secure(path: Path, data: dict, mode: int = 0o600) -> None:
    """Atomic write with umask-safe mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, sort_keys=True, indent=2)
        fh.write("\n")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


# ── OrgStore ──────────────────────────────────────────────────────────────────


class OrgStore:
    """Filesystem façade for a single organisation's data directory.

    ``org_handle`` is the org's short identifier (slug). ``tenant_id``
    selects which tenant's orgs directory is used.
    """

    def __init__(self, org_handle: str, tenant_id: str | None = "_default") -> None:
        _validate_handle(org_handle)
        self._handle = org_handle
        self._tenant_id = tenant_id
        self._dir = org_dir(org_handle, tenant_id)
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / "endorsements").mkdir(exist_ok=True)
        (self._dir / "grants").mkdir(exist_ok=True)

    @property
    def handle(self) -> str:
        return self._handle

    @property
    def directory(self) -> Path:
        return self._dir

    # ── Keypair ───────────────────────────────────────────────────────────────

    def generate_keypair(self) -> tuple[str, str]:
        """Generate and persist a fresh Ed25519 keypair. Returns (priv_hex, pub_hex).

        Caller MUST emit ``org.dsk_issued`` audit event BEFORE calling.
        M1: this is the sole signing key (no threshold sharding yet).
        """
        priv_hex, pub_hex = social_envelope.generate_keypair()
        _write_secure(
            self._dir / "keypair.json",
            {"private_key_hex": priv_hex, "public_key_hex": pub_hex, "created_at": time.time()},
            mode=0o600,
        )
        return priv_hex, pub_hex

    def load_keypair(self) -> tuple[str, str]:
        """Load (priv_hex, pub_hex) from keypair.json. Raises OrgError if missing."""
        path = self._dir / "keypair.json"
        if not path.exists():
            raise OrgError(f"org {self._handle!r} has no keypair — call generate_keypair() first")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise OrgError(f"failed to load keypair: {exc}") from exc
        priv = data.get("private_key_hex", "")
        pub = data.get("public_key_hex", "")
        if not priv or not pub:
            raise OrgError("keypair.json missing private_key_hex or public_key_hex")
        return priv, pub

    def get_public_key_hex(self) -> str:
        return self.load_keypair()[1]

    def check_keypair_mode(self) -> bool:
        """Return True if keypair.json is mode 0600. Emits CRITICAL audit if not."""
        path = self._dir / "keypair.json"
        if not path.exists():
            return True
        if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            audit_event(
                "org.keypair_world_readable",
                severity="CRITICAL",
                details={"org_handle": self._handle},
            )
            return False
        return True

    # ── Actor document ────────────────────────────────────────────────────────

    def save_actor(self, actor_doc: dict) -> None:
        """Persist the org actor document (mode 0644, world-readable)."""
        _write_secure(self._dir / "actor.json", actor_doc, mode=0o644)

    def get_actor(self) -> dict:
        """Return the org actor document. Raises OrgError if missing."""
        path = self._dir / "actor.json"
        if not path.exists():
            raise OrgError(f"org {self._handle!r} has no actor.json — not yet created")
        return json.loads(path.read_text(encoding="utf-8"))

    def actor_exists(self) -> bool:
        return (self._dir / "actor.json").exists()

    # ── Config ────────────────────────────────────────────────────────────────

    def get_config(self) -> dict:
        path = self._dir / "config.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def save_config(self, config: dict) -> None:
        _write_secure(self._dir / "config.json", config, mode=0o600)

    # ── Members ───────────────────────────────────────────────────────────────

    def get_members(self) -> list[dict]:
        path = self._dir / "members.json"
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("members", [])

    def _save_members(self, members: list[dict]) -> None:
        _write_secure(self._dir / "members.json", {"members": members}, mode=0o600)

    def add_member(self, actor_id: str, role: str) -> None:
        """Add or update a member. Caller MUST emit ``org.member_added`` BEFORE calling."""
        if role not in VALID_ROLES:
            raise OrgError(f"invalid role {role!r} — must be one of {sorted(VALID_ROLES)}")
        with _ORG_MEMBERS_LOCK:
            members = self.get_members()
            for m in members:
                if m["actor_id"] == actor_id:
                    m["role"] = role
                    self._save_members(members)
                    return
            members.append({"actor_id": actor_id, "role": role, "joined_at": int(time.time())})
            self._save_members(members)

    def remove_member(self, actor_id: str) -> bool:
        """Remove a member. Returns True if found. Caller MUST emit audit BEFORE calling."""
        with _ORG_MEMBERS_LOCK:
            members = self.get_members()
            target = next((m for m in members if m["actor_id"] == actor_id), None)
            if target and target.get("role") == "owner":
                owners = [m for m in members if m.get("role") == "owner"]
                if len(owners) <= 1:
                    raise OrgError("cannot remove the last owner of an org")
            new = [m for m in members if m["actor_id"] != actor_id]
            if len(new) == len(members):
                return False
            self._save_members(new)
            return True

    def get_member(self, actor_id: str) -> dict | None:
        return next((m for m in self.get_members() if m["actor_id"] == actor_id), None)

    def list_owners(self) -> list[str]:
        return [m["actor_id"] for m in self.get_members() if m["role"] == "owner"]

    # ── Endorsements ─────────────────────────────────────────────────────────

    def _endorsement_path(self, endorsement_id: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", endorsement_id)
        return self._dir / "endorsements" / f"{safe}.json"

    def save_endorsement(self, doc: dict) -> None:
        """Persist an agent endorsement. Caller MUST emit ``org.agent_affiliated`` BEFORE."""
        eid = doc.get("endorsement_id", "")
        if not eid:
            raise OrgError("endorsement_id must not be empty")
        path = self._endorsement_path(eid)
        _write_secure(path, doc, mode=0o644)

    def get_endorsement(self, endorsement_id: str) -> dict | None:
        path = self._endorsement_path(endorsement_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_endorsements(self, include_revoked: bool = False) -> list[dict]:
        edir = self._dir / "endorsements"
        docs = []
        for p in sorted(edir.glob("*.json")):
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
                if not include_revoked and doc.get("revoked_at") is not None:
                    continue
                docs.append(doc)
            except Exception:
                continue
        return docs

    def find_endorsement_for_agent(self, agent_actor_id: str) -> dict | None:
        """Return the first active endorsement for the given agent, or None."""
        return next(
            (
                e
                for e in self.list_endorsements(include_revoked=False)
                if e.get("agent_actor_id") == agent_actor_id
            ),
            None,
        )

    def revoke_endorsement(self, endorsement_id: str, *, revoked_at: int | None = None) -> bool:
        """Mark an endorsement revoked. Returns True if found and not already revoked."""
        doc = self.get_endorsement(endorsement_id)
        if doc is None or doc.get("revoked_at") is not None:
            return False
        doc["revoked_at"] = revoked_at or int(time.time())
        _write_secure(self._endorsement_path(endorsement_id), doc, mode=0o644)
        return True

    # ── Grant store path ──────────────────────────────────────────────────────

    def grant_db_path(self) -> Path:
        """Return the path to this org's isolated GrantStore DB."""
        return self._dir / "grants" / "grants.db"

    # ── Dissolution (hard delete) ─────────────────────────────────────────────

    def dissolve(self) -> int:
        """Hard-delete the entire org directory. Returns count of files removed.

        Caller MUST emit ``org.dissolved`` audit event BEFORE calling.
        """
        import shutil

        count = sum(1 for _ in self._dir.rglob("*") if _.is_file())
        shutil.rmtree(str(self._dir), ignore_errors=True)
        return count
