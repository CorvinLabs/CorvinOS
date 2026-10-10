"""ADR-2175 T-0105 / ADR-0533 — every content version of a prompt skill, kept and pinnable.

A prompt skill has no version field and SkillForge overwrites its body on edit, so
without a catalog an old version is gone the moment it changes and a pin could only
name nothing. The catalog records each body the registry is about to execute:

* ``<tenant>/skills/prompt_versions/catalog.jsonl`` — one content-free line per
  (name, sha256): ``seq`` (per skill, first-seen order) and ``first_seen``;
* ``bodies/<sha256>.md`` — content-addressed copy, written once (``O_EXCL``), and
  re-hashed on read: a tampered copy is never served.

Version string: ``0.0.<seq>+<sha8>`` — semver 2.0, the build part names the content,
the patch number orders it (``SemanticVersion`` ignores build metadata for precedence).

Pins: ``spec.skills.<skill id>.version`` in ``tenants/<tid>/global/tenant.corvin.yaml``,
resolved by ``version_manager.VersionResolver`` (a pin to an unavailable version FAILS —
it never falls through to "latest", which would re-install what the operator rolled
away from). No canaries: single-operator installs roll out 100 % (operator rule).
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from .version_manager import TenantVersionPin, VersionResolver

_LOCK = threading.Lock()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def format_version(seq: int, sha256: str) -> str:
    return f"0.0.{seq}+{sha256[:8]}"


class PromptVersionCatalog:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.catalog = self.root / "catalog.jsonl"
        self.bodies = self.root / "bodies"

    @classmethod
    def for_tenant(cls, tenant_id: str) -> "PromptVersionCatalog":
        from core.paths.tenant import tenant_home  # noqa: PLC0415
        return cls(tenant_home(tenant_id) / "skills" / "prompt_versions")

    def _rows(self) -> List[dict]:
        try:
            lines = self.catalog.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def versions(self, name: str) -> Dict[str, str]:
        """``{version: sha256}`` for every recorded content of ``name``."""
        return {format_version(r["seq"], r["sha256"]): r["sha256"]
                for r in self._rows() if r.get("name") == name}

    def record(self, name: str, body: str) -> str:
        """Return the version of ``body`` for ``name``, recording it if new (idempotent)."""
        sha = _sha(body)
        with _LOCK:
            rows = [r for r in self._rows() if r.get("name") == name]
            for r in rows:
                if r.get("sha256") == sha:
                    return format_version(r["seq"], sha)
            self.bodies.mkdir(parents=True, exist_ok=True)
            blob = self.bodies / f"{sha}.md"
            try:
                fd = os.open(blob, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            except FileExistsError:
                pass  # same content under another name — content-addressed, keep it
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(body)
            seq = max((int(r.get("seq", 0)) for r in rows), default=0) + 1
            line = json.dumps({"name": name, "seq": seq, "sha256": sha,
                               "first_seen": round(time.time(), 3)}, separators=(",", ":"))
            fd = os.open(self.catalog, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, (line + "\n").encode("utf-8"))
            finally:
                os.close(fd)
            return format_version(seq, sha)

    def body(self, sha256: str) -> Optional[str]:
        """The stored body for ``sha256``, or None when absent or not matching its hash."""
        try:
            text = (self.bodies / f"{sha256}.md").read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return None
        return text if _sha(text) == sha256 else None


def load_pins(tenant_id: str) -> Optional[TenantVersionPin]:
    """``spec.skills`` of the tenant config as a ``TenantVersionPin``; None without pins."""
    try:
        from core.paths.tenant import tenant_home  # noqa: PLC0415
        import yaml  # type: ignore[import-untyped]  # noqa: PLC0415
        p = tenant_home(tenant_id) / "global" / "tenant.corvin.yaml"
        spec = ((yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("spec") or {})
    except (FileNotFoundError, OSError, ValueError):
        return None
    except Exception:  # noqa: BLE001 — a malformed file pins nothing, it never breaks a turn
        return None
    skills = spec.get("skills")
    return TenantVersionPin(skills) if isinstance(skills, dict) and skills else None


class PinUnavailable(LookupError):
    """The tenant pinned a version that is not in the catalog (ADR-0533: never fall through)."""


def resolve(catalog: PromptVersionCatalog, skill_id: str, name: str, current_version: str,
            pins: Optional[TenantVersionPin]) -> tuple[str, Optional[str]]:
    """Return ``(version, body_override)``; override is None when the current body serves.

    Raises ``PinUnavailable`` for a pin the catalog cannot serve.
    """
    if pins is None or not pins.get_skill_version(skill_id):
        return current_version, None
    available = catalog.versions(name)
    try:
        chosen = VersionResolver().resolve_version(skill_id, list(available), tenant_pin=pins)
    except ValueError as exc:
        raise PinUnavailable(str(exc)) from exc
    if chosen == current_version:
        return chosen, None
    body = catalog.body(available[chosen])
    if body is None:
        raise PinUnavailable(f"stored body for {skill_id} {chosen} missing or altered")
    return chosen, body


__all__ = ["PinUnavailable", "PromptVersionCatalog", "format_version", "load_pins", "resolve"]
