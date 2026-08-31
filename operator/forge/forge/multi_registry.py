"""Multi-scope tool registry.

Lookup-Order beim list/get: task -> session -> project -> user
(higher scope shadows lower). create() writes into the per-scope
selected root.

Note on the term "scope": Registry.create has its own ``scope``
parameter that means "permission scope" (session|project|user) —
that's an entirely separate axis from the workspace scope this
class composes over. To avoid the collision we call the workspace
scope ``ws_scope`` internally where needed; the public surface
keeps ``scope`` since callers only ever talk about workspace
scope here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .registry import Registry, ToolSpec
from .scope import VALID_SCOPES, detect_scope, scope_root


class MultiRegistry:
    """Composes per-scope Registries with shadowing semantics (TENANT-NATIVE)."""

    def __init__(
        self,
        *,
        tenant_id: str | None = None,  # NEW: tenant-aware (Phase B)
        channel_id: str | None = None,
        task_id: str | None = None,
        project_root: Path | None = None,
        hash_chain: bool = True,
    ):
        from .paths import _resolve_tenant_id
        # Resolve tenant_id: explicit > env > _default (ADR-0433)
        self.tenant_id = _resolve_tenant_id(tenant_id)
        self._kwargs = dict(
            tenant_id=self.tenant_id,
            channel_id=channel_id,
            task_id=task_id,
            project_root=project_root,
        )
        self._hash_chain = hash_chain
        self._registries: dict[str, Registry] = {}

    def _registry(self, scope: str) -> Registry:
        if scope not in VALID_SCOPES:
            raise ValueError(
                f"unknown scope: {scope!r} (valid: {VALID_SCOPES})"
            )
        if scope not in self._registries:
            root = scope_root(scope, **self._kwargs)
            root.mkdir(parents=True, exist_ok=True)
            self._registries[scope] = Registry(
                root, hash_chain=self._hash_chain
            )
        return self._registries[scope]

    # -- create / get / list / delete -------------------------------------

    def create(
        self,
        *,
        scope: str | None = None,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        impl: str,
        runtime: str = "python",
        overwrite: bool = False,
        meta: dict[str, Any] | None = None,
        permission_scope: str = "session",
    ) -> ToolSpec:
        """Create a tool in the given workspace scope (default: detect_scope())."""
        ws_scope = scope or detect_scope()
        return self._registry(ws_scope).create(
            name=name,
            description=description,
            input_schema=input_schema,
            impl=impl,
            runtime=runtime,
            scope=permission_scope,
            overwrite=overwrite,
            meta=meta,
        )

    def get(self, name: str) -> ToolSpec | None:
        """Lookup with shadowing — higher scope wins."""
        for ws_scope in VALID_SCOPES:
            spec = self._registry(ws_scope).get(name)
            if spec:
                return spec
        return None

    def get_in_scope(self, name: str, scope: str) -> ToolSpec | None:
        return self._registry(scope).get(name)

    def find_scope(self, name: str) -> str | None:
        """Return the highest workspace scope that contains the tool."""
        for ws_scope in VALID_SCOPES:
            if self._registry(ws_scope).get(name):
                return ws_scope
        return None

    def list(self) -> list[ToolSpec]:
        """Merge with shadowing: first occurrence wins."""
        seen: dict[str, ToolSpec] = {}
        for ws_scope in VALID_SCOPES:
            for spec in self._registry(ws_scope).list():
                if spec.name not in seen:
                    seen[spec.name] = spec
        return list(seen.values())

    def list_with_scope(self) -> list[tuple[str, ToolSpec]]:
        """Like list() but returns (workspace_scope, spec) — for tools/list+ debug."""
        seen: dict[str, tuple[str, ToolSpec]] = {}
        for ws_scope in VALID_SCOPES:
            for spec in self._registry(ws_scope).list():
                if spec.name not in seen:
                    seen[spec.name] = (ws_scope, spec)
        return list(seen.values())

    def delete(self, name: str, *, scope: str | None = None) -> bool:
        """Delete a tool. If scope is given, only delete from that scope.
        Otherwise delete from the highest scope where the tool exists."""
        if scope is not None:
            reg = self._registry(scope)
            if reg.get(name) is None:
                return False
            reg.delete(name)
            return True
        for ws_scope in VALID_SCOPES:
            reg = self._registry(ws_scope)
            if reg.get(name):
                reg.delete(name)
                return True
        return False

    def promote(self, name: str, *, to: str) -> ToolSpec:
        """Copy a tool definition from its current (highest) scope to ``to``.

        Targets ``session``, ``project`` or ``user`` only — promoting to
        ``task`` makes no sense (task is shorter-lived than session).
        """
        if to not in ("session", "project", "user"):
            raise ValueError(
                f"unknown target scope: {to!r} (valid: session|project|user)"
            )
        for ws_scope in VALID_SCOPES:
            spec = self._registry(ws_scope).get(name)
            if not spec:
                continue
            if ws_scope == to:
                return spec  # already there
            # Replicate via Registry.create in the target scope. We re-read
            # the impl source from disk so the SHA stays consistent.
            impl_text = Path(spec.impl_path).read_text()
            new_spec = self._registry(to).create(
                name=spec.name,
                description=spec.description,
                input_schema=spec.input_schema,
                impl=impl_text,
                runtime=spec.runtime,
                scope=getattr(spec, "scope", "user"),
                overwrite=True,
                meta=getattr(spec, "meta", {}) or {},
            )
            return new_spec
        raise KeyError(f"tool not found in any scope: {name!r}")
