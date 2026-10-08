"""Tool quarantine for Forge Bundle imports (ADR-2229 D2, Phase 3).

Tool Forge has no review state: ``Registry.create`` makes a tool callable at
once. An imported tool therefore lands here first, and only an operator's
explicit accept calls ``MultiRegistry.create``.

Layout (tenant-scoped, outside every registry root, so nothing can load it)::

    <tenant_home>/global/forge_bundle/quarantine/tools/<qid>/meta.json
    <tenant_home>/global/forge_bundle/quarantine/tools/<qid>/impl.py | impl.sh

``qid`` is a random 32-hex token, the ONLY handle the console accepts, checked
against ``QID_RE`` before any path is built from it. A decision first CLAIMS
the entry by renaming ``<qid>`` to ``.claimed-<qid>`` — one atomic rename, so
two concurrent decisions cannot both proceed and a decided entry can never be
listed, accepted or rejected again, even if deleting it afterwards fails.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .envelope import is_safe_id, is_semver
from .validate import BundleRejected, _scan_text

QID_RE = re.compile(r"^[0-9a-f]{32}$")
RUNTIMES = {"python": "impl.py", "bash": "impl.sh"}
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.]{1,128}$")
# A pip requirement by NAME only: no URLs, paths, VCS refs or options — those
# would let a bundle choose where code is downloaded from.
_REQUIREMENT_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?(\[[A-Za-z0-9._,-]{1,100}\])?"
    r"(\s*(==|>=|<=|~=|!=|<|>)\s*[A-Za-z0-9.*+!_-]{1,50}(\s*,\s*(==|>=|<=|~=|!=|<|>)\s*[A-Za-z0-9.*+!_-]{1,50})*)?$"
)
# pip treats an argument that looks like an archive as a local FILE to install.
# pip's archive extensions (ZIP/BZ2/XZ/TAR lists in pip._internal.utils.filetypes) + a margin.
_ARCHIVE_SUFFIXES = (".whl", ".zip", ".tar", ".gz", ".tgz", ".bz2", ".tbz", ".tbz2", ".xz", ".txz",
                     ".lz", ".tlz", ".lzma", ".zst", ".egg", ".7z", ".rar")
_MAX_BUDGET_VALUE = 10 ** 9
_STALE_SECONDS = 3600
_BUDGET_KEYS = frozenset({"cpu_seconds", "wall_seconds", "artifact_bytes", "output_bytes", "memory_mb"})
MAX_REQUIREMENTS = 32


class QuarantineError(RuntimeError):
    """A quarantine entry could not be staged, read, accepted or rejected."""


class QuarantineNotFound(QuarantineError):
    pass


class QuarantineConflict(QuarantineError):
    pass


class QuarantineForbidden(QuarantineError):
    pass


class OutcomeNotRecorded(QuarantineError):
    """The decision was carried out, but its outcome record did not commit."""


def _is_budget_value(v: Any) -> bool:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    if isinstance(v, float) and not math.isfinite(v):
        return False
    return 0 < v <= _MAX_BUDGET_VALUE  # compared before any float() — 10**400 is just "too big"


def _is_plain_requirement(req: Any) -> bool:
    if not isinstance(req, str):
        return False
    m = _REQUIREMENT_RE.match(req.strip())
    if not m:
        return False
    name = re.match(r"[A-Za-z0-9._-]+", req.strip()).group(0).lower()
    return not name.endswith(_ARCHIVE_SUFFIXES)


def clean_tool_meta(meta: Any) -> dict[str, Any]:
    """The behavioural subset of ``ToolSpec.meta`` that may travel, validated.

    ``requirements`` (pip names), ``secrets`` (vault key NAMES, never values),
    ``budget`` (clamped by policy at run time anyway) and ``deterministic``.
    Anything else is registry state of the exporting install and is dropped.
    """
    if meta is None:
        return {}
    if not isinstance(meta, dict):
        raise QuarantineError("tool meta must be an object")
    out: dict[str, Any] = {}
    reqs = meta.get("requirements")
    if reqs is not None:
        if (not isinstance(reqs, list) or len(reqs) > MAX_REQUIREMENTS
                or not all(_is_plain_requirement(r) for r in reqs)):
            raise QuarantineError("tool requirements must be up to 32 plain package specifiers (no URLs or paths)")
        if reqs:
            out["requirements"] = [r.strip() for r in reqs]
    if meta.get("secrets") is not None:
        from forge.secret_vault import SecretRefError, validate_secret_refs

        try:
            refs = validate_secret_refs(meta.get("secrets"))
        except SecretRefError as exc:
            raise QuarantineError(f"tool secret references invalid: {exc}") from None
        if refs:
            out["secrets"] = refs
    budget = meta.get("budget")
    if budget is not None:
        if (not isinstance(budget, dict) or not set(budget) <= _BUDGET_KEYS
                or not all(_is_budget_value(v) for v in budget.values())):
            raise QuarantineError("tool budget must map known limits to positive numbers")
        out["budget"] = dict(budget)
    if meta.get("deterministic") is not None:
        if not isinstance(meta["deterministic"], bool):
            raise QuarantineError("tool deterministic flag must be a boolean")
        out["deterministic"] = meta["deterministic"]
    return out


@dataclass(frozen=True)
class QuarantinedTool:
    quarantine_id: str
    tool_id: str
    version: str
    bundle_id: str
    bundle_version: str
    staged_at: str
    runtime: str
    description: str
    input_schema: dict[str, Any]
    impl_filename: str
    impl_sha256: str
    meta: dict[str, Any] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        return {
            "quarantine_id": self.quarantine_id,
            "kind": "tool",
            "tool_id": self.tool_id,
            "version": self.version,
            "bundle_id": self.bundle_id,
            "bundle_version": self.bundle_version,
            "staged_at": self.staged_at,
            "runtime": self.runtime,
            "description": self.description,
            "impl_sha256": self.impl_sha256,
            "requirements": list(self.meta.get("requirements", [])),
            "secrets": list(self.meta.get("secrets", [])),
            "origin_verified": False,
        }


def quarantine_root(tenant_id: str) -> Path:
    from core.paths import tenant_home

    return tenant_home(tenant_id) / "global" / "forge_bundle" / "quarantine" / "tools"


def _check_qid(qid: str) -> None:
    if not isinstance(qid, str) or not QID_RE.match(qid):
        raise QuarantineNotFound("unknown quarantine id")


def _validate_tool_name(name: str) -> None:
    # Same rules Registry.create enforces — refused at staging, not at accept,
    # so an entry the operator sees can actually be accepted.
    if not _TOOL_NAME_RE.match(name or "") or ".." in name or name.startswith(".") or name.endswith("."):
        raise QuarantineError(f"tool name {name!r} is not a valid Tool Forge name")


def _scan(text: str, where: str) -> None:
    try:
        _scan_text(text, where)
    except BundleRejected as exc:
        raise QuarantineError(exc.reason) from None


def _registry(tenant_id: str):
    from forge.multi_registry import MultiRegistry

    return MultiRegistry(tenant_id=tenant_id)


def _name_taken(registry, tool_id: str) -> bool:
    """Case-insensitive: on macOS/Windows ``CSV.count`` and ``csv.count`` share one impl file."""
    folded = tool_id.casefold()
    return registry.get(tool_id) is not None or any(s.name.casefold() == folded for s in registry.list())


def _load_meta(entry_dir: Path, qid: str) -> QuarantinedTool:
    try:
        meta = json.loads((entry_dir / "meta.json").read_text())
        entry = QuarantinedTool(**meta)
    except FileNotFoundError:
        raise QuarantineNotFound("unknown quarantine id") from None
    except (OSError, ValueError, TypeError):
        raise QuarantineError("quarantine entry is unreadable") from None
    if entry.quarantine_id != qid or RUNTIMES.get(entry.runtime) != entry.impl_filename:
        raise QuarantineError("quarantine entry metadata is inconsistent")
    return entry


class ToolQuarantine:
    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id
        self.root = quarantine_root(tenant_id)

    # ── staging ──────────────────────────────────────────────────────────
    def stage(
        self, *, tool_id: str, version: str, bundle_id: str, bundle_version: str,
        spec: dict[str, Any], impl_bytes: bytes,
    ) -> tuple[QuarantinedTool, bool]:
        """Stage a tool; returns (entry, created). ``created`` is False when an
        identical entry (same tool, same code, same meta) is already waiting."""
        _validate_tool_name(tool_id)
        if spec.get("name") != tool_id:
            raise QuarantineError(f"spec.json names {spec.get('name')!r}, envelope names {tool_id!r}")
        runtime = spec.get("runtime", "python")
        if runtime not in RUNTIMES:
            raise QuarantineError(f"unsupported tool runtime {runtime!r}")
        if not (is_safe_id(bundle_id) and is_semver(bundle_version) and is_semver(version)):
            raise QuarantineError("bundle id/version or tool version malformed")
        input_schema = spec.get("input_schema", {})
        if not isinstance(input_schema, dict):
            raise QuarantineError("input_schema must be an object")
        meta = clean_tool_meta(spec.get("meta"))
        try:
            impl_text = impl_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise QuarantineError("tool implementation is not UTF-8 text") from None
        _scan(impl_text, f"tool {tool_id} implementation")
        _scan(json.dumps(spec, ensure_ascii=False), f"tool {tool_id} spec")

        if _name_taken(_registry(self.tenant_id), tool_id):
            raise QuarantineConflict(f"a tool named {tool_id!r} already exists on this install")
        impl_sha = hashlib.sha256(impl_bytes).hexdigest()
        description = str(spec.get("description", ""))[:2000]
        for existing in self.list():
            # Reused only when EVERYTHING the operator reviews is identical (R2A-7).
            if (existing.tool_id, existing.version, existing.bundle_id, existing.bundle_version,
                    existing.runtime, existing.description, existing.input_schema,
                    existing.impl_sha256, existing.meta) == (
                    tool_id, version, bundle_id, bundle_version, runtime, description,
                    input_schema, impl_sha, meta):
                return existing, False

        qid = uuid.uuid4().hex
        entry = QuarantinedTool(
            quarantine_id=qid, tool_id=tool_id, version=version,
            bundle_id=bundle_id, bundle_version=bundle_version,
            staged_at=datetime.now(timezone.utc).isoformat(),
            runtime=runtime, description=description,
            input_schema=input_schema, impl_filename=RUNTIMES[runtime],
            impl_sha256=impl_sha, meta=meta,
        )
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=".staging-", dir=self.root))
        try:
            os.chmod(tmp, 0o700)
            (tmp / entry.impl_filename).write_bytes(impl_bytes)
            (tmp / "meta.json").write_text(json.dumps(entry.__dict__, indent=2))
            for p in tmp.iterdir():
                os.chmod(p, 0o600)
            tmp.rename(self.root / qid)
        except OSError as exc:
            shutil.rmtree(tmp, ignore_errors=True)
            raise QuarantineError(f"could not stage tool {tool_id}: {type(exc).__name__}") from exc
        return entry, True

    # ── reading ──────────────────────────────────────────────────────────
    def get(self, qid: str) -> QuarantinedTool:
        _check_qid(qid)
        return _load_meta(self.root / qid, qid)

    def _sweep(self) -> None:
        """Remove claim/staging leftovers older than an hour (a decision takes seconds)."""
        cutoff = time.time() - _STALE_SECONDS
        for d in self.root.iterdir():
            if d.name.startswith((".claimed-", ".staging-")):
                try:
                    if d.stat().st_mtime < cutoff:
                        shutil.rmtree(d, ignore_errors=True)
                except OSError:
                    pass

    def list(self) -> list[QuarantinedTool]:
        if not self.root.is_dir():
            return []
        self._sweep()
        out = []
        for d in self.root.iterdir():
            if d.is_dir() and QID_RE.match(d.name):
                try:
                    out.append(self.get(d.name))
                except QuarantineError:
                    continue
        return sorted(out, key=lambda e: e.staged_at, reverse=True)

    # ── deciding ─────────────────────────────────────────────────────────
    def claim(self, qid: str) -> tuple[QuarantinedTool, Path]:
        """Atomically take an entry out of the queue. Only one caller wins."""
        _check_qid(qid)
        claimed = self.root / f".claimed-{qid}"
        try:
            (self.root / qid).rename(claimed)
        except FileNotFoundError:
            raise QuarantineNotFound("unknown quarantine id") from None
        except OSError as exc:
            raise QuarantineError(f"could not claim quarantine entry: {type(exc).__name__}") from None
        try:
            os.utime(claimed)  # rename keeps the staging mtime; the sweep must age the CLAIM
        except OSError:
            pass
        try:
            return _load_meta(claimed, qid), claimed
        except QuarantineError:
            self.release(qid, claimed)
            raise

    def release(self, qid: str, claimed: Path) -> None:
        """Put a claimed entry back into the queue (the decision did not happen)."""
        try:
            claimed.rename(self.root / qid)
        except OSError:
            pass  # stays claimed: invisible, never decided twice

    @staticmethod
    def discard(claimed: Path) -> None:
        shutil.rmtree(claimed, ignore_errors=True)

    def read_impl(self, entry: QuarantinedTool, entry_dir: Path) -> str:
        try:
            data = (entry_dir / entry.impl_filename).read_bytes()
        except OSError:
            raise QuarantineError("quarantined implementation is unreadable") from None
        if hashlib.sha256(data).hexdigest() != entry.impl_sha256:
            raise QuarantineError("quarantined implementation changed on disk since staging")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            raise QuarantineError("quarantined implementation is not UTF-8 text") from None


def accept(tenant_id: str, qid: str, *, actor: str) -> QuarantinedTool:
    """Operator approval. Claim → checks → decision record → create → outcome record.

    The decision (``quarantine_accepted``) commits before the registry write;
    the outcome is ``artifact_created`` or ``artifact_failed``. If the create
    fails the entry goes back into the queue. If the chain cannot take the
    decision, nothing is created and the entry goes back.
    """
    from .audit import emit

    q = ToolQuarantine(tenant_id)
    entry, claimed = q.claim(qid)
    try:
        impl = q.read_impl(entry, claimed)
        _scan(impl, f"tool {entry.tool_id} implementation")
        registry = _registry(tenant_id)
        if _name_taken(registry, entry.tool_id):
            raise QuarantineConflict(f"a tool named {entry.tool_id!r} already exists on this install")
        common = dict(tenant_id=tenant_id, artifact_kind="tool", artifact_id=entry.tool_id,
                      artifact_version=entry.version, quarantine_id=qid,
                      bundle_id=entry.bundle_id, actor=actor)
        emit("forge_bundle.quarantine_accepted", **common)
    except BaseException:
        q.release(qid, claimed)
        raise
    try:
        registry.create(
            scope="user", name=entry.tool_id, description=entry.description,
            input_schema=entry.input_schema, impl=impl, runtime=entry.runtime,
            meta={**entry.meta, "origin": "forge_bundle", "bundle_id": entry.bundle_id,
                  "bundle_version": entry.bundle_version, "bundle_tool_version": entry.version,
                  "origin_verified": False, "quarantine_id": qid},
        )
    except BaseException as exc:
        # Registry.create writes the tool BEFORE its own audit record; if that
        # record failed, the tool exists. Decide by what is on disk (R2A-1).
        created = registry.get(entry.tool_id)
        if created is None or (created.meta or {}).get("quarantine_id") != qid:
            q.release(qid, claimed)
            try:
                emit("forge_bundle.artifact_failed", phase="accept", error_class=type(exc).__name__,
                     **{k: v for k, v in common.items() if k != "quarantine_id"})
            except Exception:
                pass  # the queue still holds the entry; the original error is the one to report (R2A-8)
            if isinstance(exc, FileExistsError):
                # Registry.create's own (locked, case-insensitive) uniqueness
                # check refused: a concurrent accept won the name (ADV-01).
                # The entry is back in the queue; answer 409, not a bare 500.
                raise QuarantineConflict(
                    f"a tool named {entry.tool_id!r} (or a case variant) was created meanwhile") from None
            if isinstance(exc, PermissionError):
                raise QuarantineForbidden("this licence tier may not create tools") from None
            raise
    q.discard(claimed)
    try:
        emit("forge_bundle.artifact_created", **common)
    except Exception:
        raise OutcomeNotRecorded(
            f"tool {entry.tool_id!r} was created, but the audit chain did not record the outcome") from None
    return entry


def reject(tenant_id: str, qid: str, *, actor: str) -> QuarantinedTool:
    from .audit import emit

    q = ToolQuarantine(tenant_id)
    entry, claimed = q.claim(qid)
    try:
        emit("forge_bundle.quarantine_rejected", tenant_id=tenant_id,
             artifact_kind="tool", artifact_id=entry.tool_id, artifact_version=entry.version,
             quarantine_id=qid, bundle_id=entry.bundle_id, actor=actor)
    except BaseException:
        q.release(qid, claimed)
        raise
    q.discard(claimed)
    return entry
