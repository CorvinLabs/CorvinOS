"""Tool quarantine for Forge Bundle imports (ADR-2229 D2, Phase 3).

Tool Forge has no review state: ``Registry.create`` makes a tool callable at
once. An imported tool therefore lands here first, and only an operator's
explicit accept calls ``MultiRegistry.create``.

Layout (tenant-scoped, outside every registry root, so nothing can load it)::

    <tenant_home>/global/forge_bundle/quarantine/tools/<qid>/meta.json
    <tenant_home>/global/forge_bundle/quarantine/tools/<qid>/<impl file>

``qid`` is a random 32-hex token. It is the ONLY handle the console accepts,
and it is checked against ``QID_RE`` before any path is built from it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .envelope import is_safe_id, is_semver
from .validate import BundleRejected, _scan_text

QID_RE = re.compile(r"^[0-9a-f]{32}$")
RUNTIMES = {"python": ".py", "bash": ".sh"}
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.]{1,128}$")


class QuarantineError(RuntimeError):
    """A quarantine entry could not be staged, read, accepted or rejected."""


class QuarantineNotFound(QuarantineError):
    pass


class QuarantineConflict(QuarantineError):
    pass


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


class ToolQuarantine:
    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id
        self.root = quarantine_root(tenant_id)

    # ── staging ──────────────────────────────────────────────────────────
    def stage(
        self, *, tool_id: str, version: str, bundle_id: str, bundle_version: str,
        spec: dict[str, Any], impl_bytes: bytes,
    ) -> QuarantinedTool:
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
        try:
            impl_text = impl_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise QuarantineError("tool implementation is not UTF-8 text") from None
        _scan(impl_text, f"tool {tool_id} implementation")
        _scan(json.dumps(spec), f"tool {tool_id} spec")

        qid = uuid.uuid4().hex
        impl_filename = "impl" + RUNTIMES[runtime]
        entry = QuarantinedTool(
            quarantine_id=qid, tool_id=tool_id, version=version,
            bundle_id=bundle_id, bundle_version=bundle_version,
            staged_at=datetime.now(timezone.utc).isoformat(),
            runtime=runtime, description=str(spec.get("description", ""))[:2000],
            input_schema=input_schema, impl_filename=impl_filename,
            impl_sha256=hashlib.sha256(impl_bytes).hexdigest(),
        )
        meta = {k: v for k, v in entry.__dict__.items()}

        self.root.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=".staging-", dir=self.root))
        try:
            os.chmod(tmp, 0o700)
            (tmp / impl_filename).write_bytes(impl_bytes)
            (tmp / "meta.json").write_text(json.dumps(meta, indent=2))
            for p in tmp.iterdir():
                os.chmod(p, 0o600)
            tmp.rename(self.root / qid)
        except OSError as exc:
            shutil.rmtree(tmp, ignore_errors=True)
            raise QuarantineError(f"could not stage tool {tool_id}: {type(exc).__name__}") from exc
        return entry

    # ── reading ──────────────────────────────────────────────────────────
    def get(self, qid: str) -> QuarantinedTool:
        _check_qid(qid)
        meta_path = self.root / qid / "meta.json"
        try:
            meta = json.loads(meta_path.read_text())
            return QuarantinedTool(**meta)
        except FileNotFoundError:
            raise QuarantineNotFound("unknown quarantine id") from None
        except (OSError, ValueError, TypeError) as exc:
            raise QuarantineError(f"quarantine entry {qid} is unreadable") from exc

    def read_impl(self, entry: QuarantinedTool) -> str:
        data = (self.root / entry.quarantine_id / entry.impl_filename).read_bytes()
        if hashlib.sha256(data).hexdigest() != entry.impl_sha256:
            raise QuarantineError("quarantined implementation changed on disk since staging")
        return data.decode("utf-8")

    def list(self) -> list[QuarantinedTool]:
        if not self.root.is_dir():
            return []
        out = []
        for d in self.root.iterdir():
            if d.is_dir() and QID_RE.match(d.name):
                try:
                    out.append(self.get(d.name))
                except QuarantineError:
                    continue
        return sorted(out, key=lambda e: e.staged_at, reverse=True)

    def remove(self, qid: str) -> None:
        _check_qid(qid)
        shutil.rmtree(self.root / qid, ignore_errors=False)


def accept(tenant_id: str, qid: str, *, actor: str) -> QuarantinedTool:
    """Operator approval: re-scan, audit, then create the tool in the user scope.

    Audit-first: ``forge_bundle.quarantine_accepted`` commits before the
    registry write; if the write then fails, ``forge_bundle.artifact_failed``
    records it and the entry stays in quarantine.
    """
    from forge.multi_registry import MultiRegistry

    from .audit import emit

    q = ToolQuarantine(tenant_id)
    entry = q.get(qid)
    impl = q.read_impl(entry)
    _scan(impl, f"tool {entry.tool_id} implementation")

    registry = MultiRegistry(tenant_id=tenant_id)
    if registry.get(entry.tool_id) is not None:
        raise QuarantineConflict(f"a tool named {entry.tool_id!r} already exists")

    emit("forge_bundle.quarantine_accepted", tenant_id=tenant_id,
         artifact_kind="tool", artifact_id=entry.tool_id, artifact_version=entry.version,
         quarantine_id=qid, bundle_id=entry.bundle_id, actor=actor)
    try:
        registry.create(
            scope="user", name=entry.tool_id, description=entry.description,
            input_schema=entry.input_schema, impl=impl, runtime=entry.runtime,
            meta={"origin": "forge_bundle", "bundle_id": entry.bundle_id,
                  "bundle_version": entry.bundle_version, "bundle_tool_version": entry.version,
                  "origin_verified": False},
        )
    except Exception as exc:
        emit("forge_bundle.artifact_failed", tenant_id=tenant_id,
             artifact_kind="tool", artifact_id=entry.tool_id, artifact_version=entry.version,
             bundle_id=entry.bundle_id, phase="accept", error_class=type(exc).__name__, actor=actor)
        raise
    q.remove(qid)
    return entry


def reject(tenant_id: str, qid: str, *, actor: str) -> QuarantinedTool:
    from .audit import emit

    q = ToolQuarantine(tenant_id)
    entry = q.get(qid)
    emit("forge_bundle.quarantine_rejected", tenant_id=tenant_id,
         artifact_kind="tool", artifact_id=entry.tool_id, artifact_version=entry.version,
         quarantine_id=qid, bundle_id=entry.bundle_id, actor=actor)
    q.remove(qid)
    return entry
